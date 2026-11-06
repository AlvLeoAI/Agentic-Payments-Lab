"""Short SQLite transactions, with a separate connection for every operation."""

import sqlite3
from contextlib import contextmanager
from pathlib import Path


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
            self.path, timeout=self.timeout if timeout is None else timeout, isolation_level=None
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
