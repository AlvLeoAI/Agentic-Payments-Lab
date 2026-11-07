"""Agent metadata only. This store never owns financial state or authorizes money."""

import sqlite3
from pathlib import Path
from typing import Protocol

from ..domain import (
    AgentMetadataError,
    PurchaseConflict,
    PurchaseRequest,
    Quote,
    TaskContext,
    canonical,
    digest,
)
from ..storage import Database
from .contracts import AgentReport, Goal, ToolEvent

SCHEMA = """
CREATE TABLE IF NOT EXISTS bindings (task_id TEXT PRIMARY KEY, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS quote_handles (
    handle TEXT PRIMARY KEY, task_id TEXT NOT NULL, request TEXT NOT NULL, quote TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tool_events (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL, data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS runs (run_id TEXT PRIMARY KEY, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS recovered_bindings (task_id TEXT PRIMARY KEY);
"""

# Agent bookkeeping must not inherit the financial stores' 30-second lock wait.
METADATA_TIMEOUT = 0.1


class BindingAuthority(Protocol):
    """Trusted independent witness; no payment execution or recovery capability."""

    def task(self, task_id: str) -> TaskContext: ...

    def agent_binding(self, task_id: str) -> str | None: ...

    def initialize_agent(self, task_id: str, binding: str) -> None: ...


class AgentStore:
    def __init__(self, path: Path):
        self.db = Database(path, SCHEMA, timeout=METADATA_TIMEOUT)

    def initialize(
        self, ledger: BindingAuthority, task_id: str, goal: Goal, *, scope: dict | None = None
    ) -> None:
        """Explicit operator operation; commit the independent witness first."""
        context = ledger.task(task_id)
        original = ledger.agent_binding(task_id)
        if original is not None:
            self.bind(context, goal, original, scope=scope)
            return
        with self.db.connect() as db:
            if db.execute("SELECT 1 FROM bindings WHERE task_id=?", (task_id,)).fetchone():
                # Pre-correction metadata has no independent witness. Never guess intent.
                raise AgentMetadataError("agent_recovery_required")
        data = self.binding_data(context, goal, scope)
        ledger.initialize_agent(task_id, data)
        with self.db.transaction() as db:
            db.execute("INSERT INTO bindings VALUES(?,?)", (task_id, data))

    def recover(self, ledger: BindingAuthority, task_id: str) -> None:
        """Restore only the original binding, never quotes, runs, usage or money."""
        data = ledger.agent_binding(task_id)
        if data is None:
            raise AgentMetadataError("agent_recovery_required")
        with self.db.transaction() as db:
            row = db.execute("SELECT data FROM bindings WHERE task_id=?", (task_id,)).fetchone()
            if row:
                if row["data"] != data:
                    raise AgentMetadataError("agent_recovery_required")
                return
            db.execute("INSERT INTO bindings VALUES(?,?)", (task_id, data))
            db.execute("INSERT OR IGNORE INTO recovered_bindings VALUES(?)", (task_id,))

    @staticmethod
    def binding_data(context: TaskContext, goal: Goal, scope: dict | None = None) -> str:
        data = {"context": context.model_dump(), "goal": goal.model_dump()}
        if scope is not None:
            data["scope"] = scope
        return canonical(data)

    def bind(
        self, context: TaskContext, goal: Goal, original: str | None, *, scope: dict | None = None
    ) -> bool:
        """Validate two existing copies; execution never initializes or repairs history."""
        data = self.binding_data(context, goal, scope)
        if original is None:
            raise AgentMetadataError("agent_initialization_required")
        if original != data:
            raise PurchaseConflict("Agent task context is immutable")
        with self.db.connect() as db:
            row = db.execute(
                "SELECT data FROM bindings WHERE task_id=?", (context.task_id,)
            ).fetchone()
            if not row or row["data"] != original:
                raise AgentMetadataError("agent_recovery_required")
            return bool(
                db.execute(
                    "SELECT 1 FROM recovered_bindings WHERE task_id=?", (context.task_id,)
                ).fetchone()
            )

    def quote(self, context: TaskContext, request: PurchaseRequest, quote: Quote) -> str:
        handle = "agent-quote-" + digest(
            {"task": context.model_dump(), "quote": quote.model_dump()}
        )
        with self.db.transaction() as db:
            db.execute(
                "INSERT OR IGNORE INTO quote_handles VALUES(?,?,?,?)",
                (
                    handle,
                    context.task_id,
                    canonical(request),
                    canonical(quote),
                ),
            )
        return handle

    def resolve(self, task_id: str, handle: str) -> tuple[PurchaseRequest, Quote]:
        with self.db.connect() as db:
            row = db.execute(
                "SELECT * FROM quote_handles WHERE handle=? AND task_id=?", (handle, task_id)
            ).fetchone()
            if not row:
                raise KeyError(handle)
            return PurchaseRequest.model_validate_json(row["request"]), Quote.model_validate_json(
                row["quote"]
            )

    def event(self, run_id: str, event: ToolEvent) -> None:
        with self.db.transaction() as db:
            db.execute(
                "INSERT INTO tool_events(run_id,data) VALUES(?,?)", (run_id, canonical(event))
            )

    def start(self, report: AgentReport) -> None:
        # Never persist model prose, prompts, raw tool arguments, credentials or receipts.
        data = report.model_dump(exclude={"model_explanation"})
        with self.db.transaction() as db:
            db.execute("INSERT INTO runs VALUES(?,?)", (report.run_id, canonical(data)))

    def progress(self, report: AgentReport) -> None:
        data = report.model_dump(exclude={"model_explanation"})
        with self.db.transaction() as db:
            if (
                db.execute(
                    "UPDATE runs SET data=? WHERE run_id=?", (canonical(data), report.run_id)
                ).rowcount
                != 1
            ):
                raise sqlite3.OperationalError("Run history is missing")

    def finish(self, report: AgentReport) -> None:
        self.progress(report)
