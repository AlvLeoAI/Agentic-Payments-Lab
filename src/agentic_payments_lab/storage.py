"""Short SQLite transactions, with a separate connection for every operation.

A caller that owns a deadline declares it with ``blocking_deadline``. Every lock
wait opened inside it is capped by the time that is left, so a synchronous database
call cannot outlast the deadline by a fixed wait. Without a declared deadline the
configured wait applies unchanged.
"""

import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

# One dispatched payment call is finished and recorded even after the deadline.
# Each of its database calls may still wait this long for a lock, and no longer.
PAYMENT_STEP_LOCK_WAIT = 1.0

_DEADLINE: ContextVar[float | None] = ContextVar("blocking_deadline", default=None)
_MINIMUM_WAIT: ContextVar[float] = ContextVar("minimum_lock_wait", default=0.0)


class DeadlineExpired(TimeoutError):
    """A new blocking step was refused because the caller's deadline has passed."""


@contextmanager
def blocking_deadline(seconds: float) -> Iterator[None]:
    """Cap lock waits by the time left. A nested deadline can only be earlier."""
    deadline = time.monotonic() + seconds
    outer = _DEADLINE.get()
    token = _DEADLINE.set(deadline if outer is None else min(outer, deadline))
    try:
        yield
    finally:
        _DEADLINE.reset(token)


@contextmanager
def payment_step() -> Iterator[None]:
    """The in-flight step: bounded by its own lock wait, not cut to zero on expiry."""
    token = _MINIMUM_WAIT.set(PAYMENT_STEP_LOCK_WAIT)
    try:
        yield
    finally:
        _MINIMUM_WAIT.reset(token)


def remaining() -> float | None:
    deadline = _DEADLINE.get()
    return None if deadline is None else deadline - time.monotonic()


def expired() -> bool:
    left = remaining()
    return left is not None and left <= 0


def require_time() -> None:
    """Call before starting a step that must not begin after the deadline."""
    if expired():
        raise DeadlineExpired("deadline_expired")


def lock_wait(configured: float) -> float:
    left = remaining()
    if left is None:
        return configured
    return min(configured, max(left, _MINIMUM_WAIT.get(), 0.0))


class Database:
    def __init__(self, path: Path, schema: str, *, timeout: float = 30, defer_setup: bool = False):
        self.path = Path(path)
        self.timeout = timeout
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if defer_setup:
            # The versioned buyer opener validates/migrates before enabling WAL.
            if schema:
                raise ValueError("Deferred setup requires an empty schema")
            return
        with self.connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=FULL")
            connection.executescript(schema)

    @contextmanager
    def connect(self, *, timeout: float | None = None):
        connection = sqlite3.connect(
            self.path,
            timeout=lock_wait(self.timeout if timeout is None else timeout),
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA synchronous=FULL")
        try:
            yield connection
        finally:
            connection.close()

    @contextmanager
    def transaction(self):
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
