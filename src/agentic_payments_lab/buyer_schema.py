"""Additive buyer-file upgrades. No cross-file migration or automatic recovery."""

import sqlite3
from contextlib import closing
from functools import lru_cache
from pathlib import Path
from uuid import uuid4

from .domain import canonical
from .storage import Database

# 0: Phase 2; 1: gate; 2: cancellation; 3: reconciliation incident enforcement.
# Version 3 has the same tables as 2, but older binaries must not ignore incidents.
VERSION = 3
SIMULATION_PROFILE = {"mode": "simulated", "network": "simulation:local", "asset": "SIM-USDC"}
ADDITIONS = """
CREATE TABLE buyer_profile (
    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
    instance_id TEXT NOT NULL,
    data TEXT NOT NULL
);
CREATE TRIGGER buyer_profile_no_update BEFORE UPDATE ON buyer_profile
BEGIN SELECT RAISE(ABORT, 'buyer profile is immutable'); END;
CREATE TRIGGER buyer_profile_no_delete BEFORE DELETE ON buyer_profile
BEGIN SELECT RAISE(ABORT, 'buyer profile is immutable'); END;
CREATE INDEX events_purchase_id ON events(purchase_id);
CREATE TABLE x402_tasks (
    task_id TEXT PRIMARY KEY REFERENCES tasks(task_id),
    request TEXT NOT NULL
);
CREATE TRIGGER x402_tasks_no_update BEFORE UPDATE ON x402_tasks
BEGIN SELECT RAISE(ABORT, 'offline task binding is immutable'); END;
CREATE TRIGGER x402_tasks_no_delete BEFORE DELETE ON x402_tasks
BEGIN SELECT RAISE(ABORT, 'offline task binding is immutable'); END;
CREATE TABLE x402_attempts (
    purchase_id TEXT PRIMARY KEY REFERENCES tasks(purchase_id),
    grant_id TEXT NOT NULL REFERENCES grants(grant_id),
    authorization_id TEXT NOT NULL UNIQUE,
    nonce TEXT NOT NULL UNIQUE,
    owner TEXT NOT NULL UNIQUE,
    fingerprint TEXT NOT NULL,
    terms TEXT NOT NULL,
    amount INTEGER NOT NULL CHECK(typeof(amount)='integer' AND amount>0),
    dispatch_authorized INTEGER NOT NULL DEFAULT 0 CHECK(dispatch_authorized IN (0,1)),
    phase TEXT NOT NULL CHECK(phase IN (
        'signing_claimed','canceled_before_dispatch','dispatch_authorized',
        'reconciliation_required','fixture_settled','fixture_unused')),
    reason TEXT NOT NULL,
    evidence TEXT
);
CREATE TRIGGER x402_attempts_identity BEFORE UPDATE OF
    purchase_id,grant_id,authorization_id,nonce,owner,fingerprint,terms,amount ON x402_attempts
BEGIN SELECT RAISE(ABORT, 'offline authorization is immutable'); END;
CREATE TRIGGER x402_attempts_no_delete BEFORE DELETE ON x402_attempts
BEGIN SELECT RAISE(ABORT, 'offline authorization cannot be deleted'); END;
CREATE TRIGGER x402_dispatch_no_reset BEFORE UPDATE OF dispatch_authorized ON x402_attempts
WHEN OLD.dispatch_authorized=1 AND NEW.dispatch_authorized=0
BEGIN SELECT RAISE(ABORT, 'dispatch authorization cannot be reset'); END;
"""

CANCELLATIONS = """
CREATE TABLE x402_cancellations (
    task_id TEXT PRIMARY KEY REFERENCES x402_tasks(task_id),
    recorded_at INTEGER NOT NULL CHECK(typeof(recorded_at)='integer' AND recorded_at>=0)
);
CREATE TRIGGER x402_cancellations_no_update BEFORE UPDATE ON x402_cancellations
BEGIN SELECT RAISE(ABORT, 'offline cancellation is immutable'); END;
CREATE TRIGGER x402_cancellations_no_delete BEFORE DELETE ON x402_cancellations
BEGIN SELECT RAISE(ABORT, 'offline cancellation cannot be deleted'); END;
"""


class SchemaError(RuntimeError):
    pass


def _objects(db):
    return {
        (row["type"], row["name"]): " ".join(row["sql"].split())
        for row in db.execute(
            "SELECT type,name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"
        )
        if row["sql"] is not None
    }


@lru_cache(maxsize=3)
def _expected(schema: str):
    with sqlite3.connect(":memory:") as db:
        db.row_factory = sqlite3.Row
        db.executescript(schema)
        return _objects(db)


def _ddl(db, schema: str):
    # executescript would implicitly commit a pending transaction. Execute complete
    # statements instead, including entire trigger bodies, inside BEGIN IMMEDIATE.
    statement = ""
    for line in schema.splitlines(keepends=True):
        statement += line
        if sqlite3.complete_statement(statement):
            db.execute(statement)
            statement = ""
    if statement.strip():
        raise SchemaError("Incomplete migration DDL")


def _validate(db, base_schema: str, profile: dict, initialize: bool) -> int:
    version = db.execute("PRAGMA user_version").fetchone()[0]
    objects = _objects(db)
    if version == 0:
        if not initialize:
            raise SchemaError("Unversioned or missing history; explicit recovery required")
        if objects and (profile != SIMULATION_PROFILE or objects != _expected(base_schema)):
            raise SchemaError("Only the exact Phase 2 simulator schema can be upgraded")
    else:
        schema = base_schema + ADDITIONS + (CANCELLATIONS if version >= 2 else "")
        if version not in (1, 2, VERSION) or objects != _expected(schema):
            raise SchemaError("Unknown or incomplete buyer schema; operator recovery required")
        rows = db.execute("SELECT data FROM buyer_profile").fetchall()
        if len(rows) != 1 or rows[0]["data"] != canonical(profile):
            raise SchemaError("Missing/mismatched backend or operator policy; refusing to rebind")
    return version


def open_buyer_database(
    path: Path, base_schema: str, profile: dict, *, initialize: bool, timeout: float = 30
) -> Database:
    path = Path(path)
    if not initialize and not path.is_file():
        raise SchemaError("Buyer state missing; explicit operator initialization/recovery required")
    if path.is_file():
        # mode=ro cannot create a missing file or change journal mode. Use one
        # snapshot for version, schema and profile; never enable WAL on rejection.
        with closing(
            sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=timeout)
        ) as db:
            db.row_factory = sqlite3.Row
            db.execute("BEGIN")
            _validate(db, base_schema, profile, initialize)
    database = Database(path, "", timeout=timeout, defer_setup=True)
    with database.transaction() as db:
        # A concurrent opener may have migrated since preflight. Recheck under
        # the write lock before any DDL or persistent PRAGMA change.
        version = _validate(db, base_schema, profile, initialize)
        if version == 0:
            if not _objects(db):
                _ddl(db, base_schema)
            _ddl(db, ADDITIONS)
            db.execute("INSERT INTO buyer_profile VALUES(1,?,?)", (uuid4().hex, canonical(profile)))
        if version < 2:
            _ddl(db, CANCELLATIONS)
        if version < VERSION:
            db.execute(f"PRAGMA user_version={VERSION}")
    # SQLite cannot switch to WAL inside the migration transaction. A failure
    # here leaves a valid committed schema, never an implicitly repaired one;
    # explicit reopening can finish journal configuration without replaying work.
    with database.connect() as db:
        if db.execute("PRAGMA journal_mode=WAL").fetchone()[0] != "wal":
            raise SchemaError("Buyer WAL configuration failed; inspect existing state")
    return database
