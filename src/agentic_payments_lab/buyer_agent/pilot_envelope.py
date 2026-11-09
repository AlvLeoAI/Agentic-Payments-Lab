"""One fresh, immutable three-scenario pilot; one shared request/cost journal.

This is an explicit trusted-host API, not a global sandbox or a billing cap.
Missing history never initializes, repairs or creates a replacement batch.
"""

import json
import os
import sqlite3
from contextlib import closing
from pathlib import Path
from uuid import uuid4

from ..domain import Grant, canonical, digest
from ..runtime import Runtime
from ..runtime_guard import require_pilot_runtime
from .call_budget import SCHEMA, BudgetDenied, CallBudget, CallLimits
from .contracts import Goal
from .pilot_pricing import LunaRates
from .store import AgentStore

SCENARIOS = ("purchase", "replay", "refusal")
CEILING = 250_000_000  # One USD 0.25 planning envelope, never per scenario.
FILES = ("ledger.sqlite3", "payments.sqlite3", "provider.sqlite3", "agent.sqlite3")
GOAL = Goal(subject="synthetic-luna-pilot", purpose="research", quality="standard")


def schema_witness(path):
    with closing(sqlite3.connect(Path(path).as_uri() + "?mode=ro", uri=True)) as db:
        return digest(
            {
                "version": db.execute("PRAGMA user_version").fetchone()[0],
                "schema": db.execute("SELECT name,sql FROM sqlite_master ORDER BY name").fetchall(),
            }
        )


class PilotEnvelope(CallBudget):
    def __init__(self, path):
        super().__init__(path)
        try:
            with self.connect() as db:
                self.enrollment = json.loads(
                    db.execute("SELECT data FROM enrollment WHERE singleton=1").fetchone()[0]
                )
            if self.limits.batch_id != "pilot-" + digest(self.enrollment):
                raise ValueError
            if self.enrollment["root"] != str(self.path.parent.resolve()):
                raise ValueError
            if self.enrollment["scenarios"] != list(SCENARIOS):
                raise ValueError
            if not isinstance(self.limits.rates, LunaRates):
                raise ValueError
            if self.limits.estimate_ceiling_nano_usd > CEILING:
                raise ValueError
        except (ValueError, KeyError, TypeError, sqlite3.Error):
            raise BudgetDenied("pilot_history_unavailable") from None

    @classmethod
    def initialize(cls, *args, **kwargs):
        raise BudgetDenied("use_fresh_pilot_initialization")

    @staticmethod
    def _uncertain(db):
        return (
            CallBudget._uncertain(db)
            or db.execute(
                "SELECT 1 FROM invocations WHERE status='finished' AND outcome!='completed' LIMIT 1"
            ).fetchone()
        )

    def begin_invocation(self, invocation_id):
        owner = uuid4().hex
        with self.connect(write=True) as db:
            if self._uncertain(db):
                raise BudgetDenied("pilot_stopped")
            rows = db.execute("SELECT id,status FROM invocations ORDER BY rowid").fetchall()
            if any(row["status"] == "running" for row in rows):
                raise BudgetDenied("invocation_incomplete")
            if len(rows) >= self.limits.max_invocations:
                raise BudgetDenied("invocation_limit")
            if [row["id"] for row in rows] != list(SCENARIOS[: len(rows)]):
                raise BudgetDenied("pilot_history_unavailable")
            if invocation_id != SCENARIOS[len(rows)]:
                raise BudgetDenied("pilot_scenario_order")
            db.execute(
                "INSERT INTO invocations VALUES(?,?,'running','pending')", (invocation_id, owner)
            )
        self._owners[invocation_id] = owner

    def report(self):
        result = super().report()
        result["mode"] = "pilot_planning"
        result["blocked"] |= any(i["outcome"] != "completed" for i in result["invocations"])
        result["enrollment_hash"] = digest(
            {"enrollment": self.enrollment, "policy": self.limits.model_dump(mode="json")}
        )
        result["scenarios"] = list(SCENARIOS)
        return result


def initialize_activation(directory: Path, *, estimate_ceiling_nano_usd=CEILING, **lower_limits):
    """Initialize one operator-owned pilot directory, never an existing path.

    Financial and metadata initialization spans files. The envelope is the final
    ready marker; any interruption requires inspection, never automatic repair.
    """
    require_pilot_runtime()
    if type(estimate_ceiling_nano_usd) is not int or not 0 <= estimate_ceiling_nano_usd <= CEILING:
        raise BudgetDenied("pilot_ceiling")
    limits = CallLimits(
        batch_id="preflight",
        model="gpt-6-luna",
        rates=LunaRates(),
        estimate_ceiling_nano_usd=estimate_ceiling_nano_usd,
        **lower_limits,
    )
    root = Path(directory).resolve()
    root.mkdir(mode=0o700)
    runtime = Runtime(root / "state")
    metadata = AgentStore(root / "state/agent.sqlite3")
    bindings = {}
    for task, amount in (("pilot-purchase", 10_000), ("pilot-refusal", 0)):
        grant = Grant(
            grant_id=task,
            operator_id="operator",
            service_ids=("report-standard",),
            purpose="research",
            budget=amount,
            per_purchase_limit=amount,
            issued_at=runtime.clock.now(),
            expires_at=runtime.clock.now() + 3600,
        )
        runtime.ledger.add_grant(grant, runtime.clock.now())
        runtime.ledger.create_task(task, task, runtime.clock.now())
        metadata.initialize(runtime.ledger, task, GOAL)
        bindings[task] = runtime.ledger.agent_binding(task)
    enrollment = {
        "root": str(root),
        "scenarios": list(SCENARIOS),
        "bindings": bindings,
        "schemas": {name: schema_witness(root / "state" / name) for name in FILES},
    }
    limits = limits.model_copy(update={"batch_id": "pilot-" + digest(enrollment)})
    path = root / "envelope.sqlite3"
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(fd)
    with closing(sqlite3.connect(path)) as db, db:
        db.executescript(
            "BEGIN IMMEDIATE;\n"
            + SCHEMA
            + """
CREATE TABLE enrollment (singleton INTEGER PRIMARY KEY CHECK(singleton=1), data TEXT NOT NULL);
CREATE TRIGGER enrollment_no_update BEFORE UPDATE ON enrollment
BEGIN SELECT RAISE(ABORT,'immutable enrollment'); END;
CREATE TRIGGER enrollment_no_delete BEFORE DELETE ON enrollment
BEGIN SELECT RAISE(ABORT,'immutable enrollment'); END;
"""
        )
        db.execute("INSERT INTO enrollment VALUES(1,?)", (canonical(enrollment),))
        db.execute("INSERT INTO policy VALUES(1,?)", (canonical(limits),))
        db.execute("PRAGMA user_version=1")
    return PilotEnvelope(path)


def open_scenario(root, scenario):
    require_pilot_runtime()
    if scenario not in SCENARIOS:
        raise BudgetDenied("pilot_scenario_unknown")
    root = Path(root).resolve()
    envelope = PilotEnvelope(root / "envelope.sqlite3")
    # Read-only schema witnesses precede constructors that could initialize stores.
    try:
        for name in FILES:
            if schema_witness(root / "state" / name) != envelope.enrollment["schemas"][name]:
                raise ValueError
    except (sqlite3.Error, ValueError, KeyError):
        raise BudgetDenied("pilot_state_recovery_required") from None
    runtime = Runtime(root / "state")
    metadata = AgentStore(root / "state/agent.sqlite3")
    task = "pilot-refusal" if scenario == "refusal" else "pilot-purchase"
    for original_task, binding in envelope.enrollment["bindings"].items():
        if runtime.ledger.agent_binding(original_task) != binding:
            raise BudgetDenied("pilot_binding_changed")
        metadata.bind(runtime.ledger.task(original_task), GOAL, binding)
    return envelope, runtime, metadata, runtime.buyer(task), GOAL
