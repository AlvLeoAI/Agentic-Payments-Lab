"""Separately enrolled simulator campaign; legacy CallLimits and pilot stay unchanged.

One durable campaign owner, no owner recovery or restart/resume. Cross-file financial
state is witnessed at quiescent boundaries, never presumed atomically committed with
the API journal. A crash or missing witness blocks the entire campaign.
"""

import hashlib
import importlib.metadata
import json
import os
import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Annotated, Literal
from uuid import uuid4

from pydantic import Field

from ..domain import Grant, PurchaseRequest, canonical, digest
from ..payments import PaymentFault
from ..provider import DeliveryFault
from ..runtime import Runtime
from ..runtime_guard import require_pilot_runtime
from .call_budget import SCHEMA, BudgetDenied, CallBudget, CallLimits
from .campaign_cases import frozen_plan
from .contracts import Goal
from .pilot_envelope import FILES
from .pilot_pricing import LunaRates
from .store import METADATA_TIMEOUT, AgentStore

SOURCE_ROOT = Path(__file__).resolve().parents[3]
CEILING = 1_500_000_000


def _request_counts(requests):
    """Count journal evidence, never infer dispatch from a committed reservation."""
    reserved = len(requests)
    complete = sum(row["status"] == "complete" for row in requests)
    return {
        "request_attempts_reserved": reserved,
        "known_complete_responses": complete,
        "actual_responses_requests": reserved if complete == reserved else None,
    }


class CampaignLimits(CallLimits):
    campaign_kind: Literal["luna_simulator_evaluation_v1"] = "luna_simulator_evaluation_v1"
    execution_mode: Literal["responses_mock", "live_simulator"] = "responses_mock"
    model: Literal["gpt-6-luna"] = "gpt-6-luna"
    reasoning_effort: Literal["none"] = "none"
    max_invocations: Annotated[int, Field(strict=True, ge=24, le=24)] = 24
    max_requests: Annotated[int, Field(strict=True, ge=1, le=144)] = 144
    max_request_bytes: Annotated[int, Field(strict=True, ge=1, le=32768)] = 32768
    estimate_ceiling_nano_usd: Annotated[int, Field(strict=True, ge=0, le=CEILING)] = CEILING
    rates: LunaRates = LunaRates()


def source_witness():
    """Bind all execution, tests, docs, catalog, dependency and validation inputs.

    Never discover credentials, databases, caches, arbitrary parent or sibling files.
    This guards accidental drift, not malicious edits to the trusted local runtime.
    """
    paths = [
        SOURCE_ROOT / name
        for name in (
            "AGENTS.md",
            "README.md",
            "LICENSE",
            "pyproject.toml",
            "uv.lock",
            ".python-version",
            ".gitignore",
        )
    ]
    for name in ("src", "tests", "scripts", "evals", "docs", ".github/workflows"):
        paths.extend(
            p
            for p in (SOURCE_ROOT / name).rglob("*")
            if p.is_file() and p.suffix in (".py", ".md", ".json", ".jsonl", ".yml", ".yaml")
        )
    if any(p.is_symlink() or not p.is_file() for p in paths):
        raise BudgetDenied("campaign_source_unavailable")
    return {
        p.relative_to(SOURCE_ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(paths)
    }


def dependencies():
    return dict(sorted((d.metadata["Name"], d.version) for d in importlib.metadata.distributions()))


@contextmanager
def readonly_snapshot(path):
    # Reporting/cancellation must not inherit the financial stores' 30-second wait.
    with closing(
        sqlite3.connect(
            path.as_uri() + "?mode=ro",
            uri=True,
            timeout=METADATA_TIMEOUT,
        )
    ) as db:
        db.execute("BEGIN")
        yield db


def state_witness(directory):
    """Hash logical records, including metadata, without retaining receipts or content."""
    result = {}
    try:
        for name in FILES:
            with readonly_snapshot(directory / name) as db:
                schema = db.execute("SELECT name,sql FROM sqlite_master ORDER BY name").fetchall()
                tables = db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
                ).fetchall()
                rows = {
                    table: db.execute(
                        'SELECT * FROM "' + table.replace('"', '""') + '" ORDER BY rowid'
                    ).fetchall()
                    for (table,) in tables
                }
                result[name] = digest(
                    {
                        "version": db.execute("PRAGMA user_version").fetchone()[0],
                        "schema": schema,
                        "rows": rows,
                    }
                )
    except (sqlite3.Error, OSError, ValueError):
        raise BudgetDenied("campaign_fixture_history_unavailable") from None
    return result


EXTRA_SCHEMA = """
CREATE TABLE enrollment (singleton INTEGER PRIMARY KEY CHECK(singleton=1), data TEXT NOT NULL);
CREATE TABLE campaign (
 singleton INTEGER PRIMARY KEY CHECK(singleton=1),
 status TEXT NOT NULL CHECK(status IN ('prepared','running','finished','stopped')),
 owner TEXT, reason TEXT NOT NULL
);
CREATE TABLE entry_results (
 id TEXT PRIMARY KEY REFERENCES invocations(id), data TEXT NOT NULL, witness TEXT NOT NULL
);
CREATE TRIGGER enrollment_no_update BEFORE UPDATE ON enrollment
BEGIN SELECT RAISE(ABORT,'immutable campaign enrollment'); END;
CREATE TRIGGER enrollment_no_delete BEFORE DELETE ON enrollment
BEGIN SELECT RAISE(ABORT,'immutable campaign enrollment'); END;
CREATE TRIGGER results_no_update BEFORE UPDATE ON entry_results
BEGIN SELECT RAISE(ABORT,'immutable campaign result'); END;
CREATE TRIGGER results_no_delete BEFORE DELETE ON entry_results
BEGIN SELECT RAISE(ABORT,'retain campaign result'); END;
CREATE TRIGGER campaign_no_delete BEFORE DELETE ON campaign
BEGIN SELECT RAISE(ABORT,'retain campaign owner'); END;
CREATE TRIGGER campaign_no_reset BEFORE UPDATE ON campaign
WHEN OLD.status != 'prepared' AND (NEW.status='prepared' OR NEW.owner != OLD.owner)
BEGIN SELECT RAISE(ABORT,'retain campaign owner'); END;
"""


class Campaign(CallBudget):
    def __init__(self, path):
        self.path = Path(path).resolve()
        self._owners = {}
        self._campaign_owner = None
        with self.connect() as db:
            db.execute("BEGIN")
            try:
                if db.execute("PRAGMA user_version").fetchone()[0] != 1:
                    raise ValueError
                self.limits = CampaignLimits.model_validate_json(
                    db.execute("SELECT data FROM policy WHERE singleton=1").fetchone()[0]
                )
                self.enrollment = json.loads(
                    db.execute("SELECT data FROM enrollment WHERE singleton=1").fetchone()[0]
                )
                db.execute("SELECT id,data,witness FROM entry_results")
                if db.execute("SELECT count(*) FROM campaign").fetchone()[0] != 1:
                    raise ValueError
                if (
                    self.enrollment["root"] != str(self.path.parent)
                    or self.enrollment["entries"] != frozen_plan()
                    or self.limits.batch_id != "campaign-" + digest(self.enrollment)
                ):
                    raise ValueError
            except (ValueError, TypeError, KeyError, sqlite3.Error):
                raise BudgetDenied("campaign_history_unavailable") from None

    @classmethod
    def initialize(cls, *args, **kwargs):
        raise BudgetDenied("use_explicit_campaign_initialization")

    @property
    def enrollment_hash(self):
        return digest({"enrollment": self.enrollment, "policy": self.limits.model_dump()})

    def verify_source(self):
        if (
            source_witness() != self.enrollment["source"]
            or dependencies() != self.enrollment["dependencies"]
            or require_pilot_runtime() != self.enrollment["runtime"]
        ):
            raise BudgetDenied("campaign_source_or_runtime_changed")

    def verify_states(self):
        expected = dict(self.enrollment["initial_states"])
        with self.connect() as db:
            db.execute("BEGIN")
            rows = db.execute("SELECT id,status,outcome FROM invocations ORDER BY rowid").fetchall()
            results = {
                r[0]: json.loads(r[1]) for r in db.execute("SELECT id,witness FROM entry_results")
            }
        ids = [entry["id"] for entry in self.enrollment["entries"]]
        if [row["id"] for row in rows] != ids[: len(rows)] or len(results) != len(rows):
            raise BudgetDenied("campaign_incomplete_history")
        for entry, row in zip(self.enrollment["entries"], rows, strict=False):
            if row["status"] != "finished" or row["outcome"] != "completed":
                raise BudgetDenied("campaign_incomplete_history")
            expected[entry["fixture"]] = results[row["id"]]
        for fixture, witness in expected.items():
            if state_witness(self.path.parent / "states" / fixture) != witness:
                raise BudgetDenied("campaign_fixture_history_changed")

    def activate(self, *, approved_enrollment_hash, execution_mode):
        if (
            approved_enrollment_hash != self.enrollment_hash
            or execution_mode != self.limits.execution_mode
        ):
            raise BudgetDenied("campaign_approval_binding")
        self.verify_source()
        self.verify_states()
        owner = uuid4().hex
        with self.connect(write=True) as db:
            if db.execute("SELECT status FROM campaign").fetchone()[0] != "prepared":
                raise BudgetDenied("campaign_already_claimed_no_resume")
            if (
                db.execute("SELECT 1 FROM invocations").fetchone()
                or db.execute("SELECT 1 FROM requests").fetchone()
            ):
                raise BudgetDenied("campaign_incomplete_history")
            db.execute("UPDATE campaign SET status='running',owner=?,reason='active'", (owner,))
        self._campaign_owner = owner

    def _require_owner(self, db):
        row = db.execute("SELECT status,owner FROM campaign").fetchone()
        if not self._campaign_owner or tuple(row) != ("running", self._campaign_owner):
            raise BudgetDenied("campaign_owner_unavailable")

    @staticmethod
    def _uncertain(db):
        # This check runs inside the inherited reservation's write transaction too.
        return (
            CallBudget._uncertain(db)
            or db.execute("SELECT 1 FROM campaign WHERE status!='running'").fetchone()
        )

    def begin_invocation(self, invocation_id):
        self.verify_source()
        self.verify_states()
        owner = uuid4().hex
        with self.connect(write=True) as db:
            self._require_owner(db)
            if self._uncertain(db):
                raise BudgetDenied("campaign_usage_uncertain")
            rows = db.execute("SELECT id,status,outcome FROM invocations ORDER BY rowid").fetchall()
            entries = self.enrollment["entries"]
            if len(rows) >= 24 or invocation_id != entries[len(rows)]["id"]:
                raise BudgetDenied("campaign_entry_order")
            if any(r["status"] != "finished" or r["outcome"] != "completed" for r in rows):
                raise BudgetDenied("campaign_incomplete_history")
            db.execute(
                "INSERT INTO invocations VALUES(?,?,'running','pending')", (invocation_id, owner)
            )
        self._owners[invocation_id] = owner

    def reserve(self, invocation_id, body):
        self.verify_source()
        with self.connect() as db:
            self._require_owner(db)
        return super().reserve(invocation_id, body)

    def finish_invocation(self, *args, **kwargs):
        raise BudgetDenied("campaign_result_required")

    def record_entry(self, entry, result, witness, *, stop=False):
        """Host-only atomic result/claim finalization, never financial repair."""
        with self.connect(write=True) as db:
            self._require_owner(db)
            stop = stop or bool(CallBudget._uncertain(db))
            if (
                db.execute(
                    "UPDATE invocations SET status='finished',outcome=? "
                    "WHERE id=? AND owner=? AND status='running'",
                    (
                        "stopped" if stop else "completed",
                        entry["id"],
                        self._owners.get(entry["id"]),
                    ),
                ).rowcount
                != 1
            ):
                raise BudgetDenied("campaign_entry_owner_unavailable")
            requests = db.execute(
                "SELECT status FROM requests WHERE invocation=?", (entry["id"],)
            ).fetchall()
            result = {**result, **_request_counts(requests)}
            db.execute(
                "INSERT INTO entry_results VALUES(?,?,?)",
                (entry["id"], canonical(result), canonical(witness)),
            )
            count = db.execute("SELECT count(*) FROM entry_results").fetchone()[0]
            if stop or count == 24:
                db.execute(
                    "UPDATE campaign SET status=?,reason=?",
                    (
                        "stopped" if stop else "finished",
                        "entry_stopped" if stop else "all_entries_recorded",
                    ),
                )

    def stop(self, reason):
        if reason not in ("cancelled", "interrupted", "cleanup_incomplete"):
            raise BudgetDenied("campaign_stop_reason")
        with self.connect(write=True) as db:
            self._require_owner(db)
            db.execute("UPDATE campaign SET status='stopped',reason=?", (reason,))

    def report(self):
        result = super().report()
        with self.connect() as db:
            db.execute("BEGIN")
            state = dict(db.execute("SELECT status,reason FROM campaign").fetchone())
            results = {
                r[0]: json.loads(r[1]) for r in db.execute("SELECT id,data FROM entry_results")
            }
        result.update(
            mode=self.limits.execution_mode,
            campaign=state,
            enrollment_hash=self.enrollment_hash,
            source_manifest_sha256=digest(self.enrollment["source"]),
            planned_entries=self.enrollment["entries"],
            results=results,
            unattempted_entry_ids=[
                entry["id"]
                for entry in self.enrollment["entries"]
                if entry["id"] not in {i["id"] for i in result["invocations"]}
            ],
            human_model_review="required_not_automated",
            **_request_counts(result["requests"]),
            model_quality_established=False,
            maximum_plan_nano_usd=144 * ((32768 + 1024) * 250 + 512 * 500),
        )
        result["blocked"] |= state["status"] in ("stopped", "finished") or (
            state["status"] == "running" and self._campaign_owner is None
        )
        return result


async def initialize_campaign(directory, *, execution_mode="responses_mock", **lower_limits):
    """Fresh explicit enrollment. Partial initialization is never reusable or repaired."""
    runtime_proof = require_pilot_runtime()
    limits = CampaignLimits(batch_id="preflight", execution_mode=execution_mode, **lower_limits)
    root = Path(directory).resolve()
    root.mkdir(mode=0o700)
    entries = frozen_plan()
    initial = {}
    for entry in entries:
        fixture = entry["fixture"]
        if fixture in initial:
            continue
        directory = root / "states" / fixture
        runtime = Runtime(
            directory,
            payment_fault=PaymentFault.TIMEOUT_BEFORE_COMMIT
            if entry["seed"] == "uncertain"
            else PaymentFault.NONE,
            delivery_fault=DeliveryFault.UNAVAILABLE
            if entry["seed"] == "failed_delivery"
            else DeliveryFault.NONE,
        )
        grant = Grant(
            grant_id=fixture,
            operator_id="evaluation-operator",
            service_ids=tuple(entry["services"]),
            purpose="research",
            budget=entry["budget"],
            per_purchase_limit=entry["cap"],
            issued_at=runtime.clock.now(),
            expires_at=runtime.clock.now() + 3600,
        )
        runtime.ledger.add_grant(grant, runtime.clock.now())
        runtime.ledger.create_task(entry["task_id"], fixture, runtime.clock.now())
        metadata = AgentStore(directory / "agent.sqlite3")
        goal = Goal.model_validate(entry["goal"])
        metadata.initialize(runtime.ledger, entry["task_id"], goal)
        if entry["seed"] != "empty":
            request = PurchaseRequest(
                service_id="report-standard", subject=goal.subject, purpose=goal.purpose
            )
            session = runtime.buyer(entry["task_id"])
            await session.purchase(request, await session.quote(request))
        initial[fixture] = state_witness(directory)
    enrollment = {
        "root": str(root),
        "entries": entries,
        "initial_states": initial,
        "source": source_witness(),
        "runtime": runtime_proof,
        "dependencies": dependencies(),
    }
    limits = limits.model_copy(update={"batch_id": "campaign-" + digest(enrollment)})
    path = root / "campaign.sqlite3"
    os.close(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))
    with closing(sqlite3.connect(path)) as db, db:
        db.executescript("BEGIN IMMEDIATE;\n" + SCHEMA + EXTRA_SCHEMA)
        db.execute("INSERT INTO policy VALUES(1,?)", (canonical(limits),))
        db.execute("INSERT INTO enrollment VALUES(1,?)", (canonical(enrollment),))
        db.execute("INSERT INTO campaign VALUES(1,'prepared',NULL,'not_activated')")
        db.execute("PRAGMA user_version=1")
    return Campaign(path)
