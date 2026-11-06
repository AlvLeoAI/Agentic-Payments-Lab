"""Atomic authorization, budget accounting, and durable purchase state."""

import json
from pathlib import Path

from pydantic import TypeAdapter

from .buyer_schema import SIMULATION_PROFILE, open_buyer_database
from .domain import (
    Budget,
    BuyerConstraints,
    DeliveryState,
    Grant,
    Identifier,
    PaymentIntent,
    PaymentState,
    PolicyDenied,
    PurchaseConflict,
    PurchaseOutcome,
    PurchaseRequest,
    Quote,
    Resolution,
    ResourceEnvelope,
    SimulatedReceipt,
    TaskContext,
    canonical,
    digest,
)
from .policy import Policy

SCHEMA = """
CREATE TABLE IF NOT EXISTS grants (
    grant_id TEXT PRIMARY KEY,
    data TEXT NOT NULL,
    budget INTEGER NOT NULL CHECK(typeof(budget)='integer' AND budget>=0),
    reserved INTEGER NOT NULL DEFAULT 0 CHECK(typeof(reserved)='integer' AND reserved>=0),
    spent INTEGER NOT NULL DEFAULT 0 CHECK(typeof(spent)='integer' AND spent>=0),
    revoked INTEGER NOT NULL DEFAULT 0 CHECK(revoked IN (0,1)),
    CHECK(reserved+spent<=budget)
);
CREATE TABLE IF NOT EXISTS quotes (quote_id TEXT PRIMARY KEY, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS tasks (
    task_id TEXT PRIMARY KEY,
    grant_id TEXT NOT NULL,
    purchase_id TEXT NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS agent_bindings (
    task_id TEXT PRIMARY KEY REFERENCES tasks(task_id),
    data TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS agent_bindings_no_update BEFORE UPDATE ON agent_bindings
BEGIN SELECT RAISE(ABORT, 'agent bindings are immutable'); END;
CREATE TRIGGER IF NOT EXISTS agent_bindings_no_delete BEFORE DELETE ON agent_bindings
BEGIN SELECT RAISE(ABORT, 'agent bindings are immutable'); END;
CREATE TABLE IF NOT EXISTS purchases (
    purchase_id TEXT PRIMARY KEY,
    fingerprint TEXT NOT NULL,
    policy_version TEXT NOT NULL DEFAULT 'phase1-v1',
    grant_id TEXT NOT NULL,
    request TEXT NOT NULL,
    quote TEXT NOT NULL,
    amount INTEGER NOT NULL CHECK(typeof(amount)='integer' AND amount>=0),
    intent TEXT,
    authorization_id TEXT UNIQUE,
    payment_state TEXT NOT NULL CHECK(payment_state IN
        ('denied','reserved','executing','unknown','settled','failed','not_required')),
    delivery_state TEXT NOT NULL DEFAULT 'not_requested'
        CHECK(delivery_state IN ('not_requested','failed','delivered')),
    payment_error TEXT,
    delivery_error TEXT,
    receipt TEXT,
    resource TEXT
);
CREATE TABLE IF NOT EXISTS events (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    purchase_id TEXT,
    event_type TEXT NOT NULL,
    data TEXT NOT NULL,
    recorded_at INTEGER NOT NULL
);
CREATE TRIGGER IF NOT EXISTS events_no_update BEFORE UPDATE ON events
BEGIN SELECT RAISE(ABORT, 'audit events are append-only'); END;
CREATE TRIGGER IF NOT EXISTS events_no_delete BEFORE DELETE ON events
BEGIN SELECT RAISE(ABORT, 'audit events are append-only'); END;
"""


class Ledger:
    def __init__(self, path: Path, policy: Policy):
        self.db = open_buyer_database(path, SCHEMA, SIMULATION_PROFILE, initialize=True)
        self.policy = policy

    @staticmethod
    def _event(db, purchase_id: str | None, kind: str, data: dict, now: int):
        db.execute(
            "INSERT INTO events(purchase_id,event_type,data,recorded_at) VALUES(?,?,?,?)",
            (purchase_id, kind, canonical(data), now),
        )

    def add_grant(self, grant: Grant, now: int) -> None:
        with self.db.transaction() as db:
            old = db.execute(
                "SELECT data FROM grants WHERE grant_id=?", (grant.grant_id,)
            ).fetchone()
            if old:
                if old["data"] != canonical(grant):
                    raise PurchaseConflict("Grant ID already exists with different contents")
                return
            db.execute(
                "INSERT INTO grants(grant_id,data,budget) VALUES(?,?,?)",
                (grant.grant_id, canonical(grant), grant.budget),
            )
            self._event(db, None, "grant_created", {"grant_id": grant.grant_id}, now)

    def revoke_grant(self, grant_id: str, now: int) -> None:
        with self.db.transaction() as db:
            if not db.execute("UPDATE grants SET revoked=1 WHERE grant_id=?", (grant_id,)).rowcount:
                raise KeyError(grant_id)
            self._event(db, None, "grant_revoked", {"grant_id": grant_id}, now)

    def create_task(self, task_id: str, grant_id: str, now: int) -> TaskContext:
        """Operator-only entry point; a buyer session cannot mint another task."""
        TypeAdapter(Identifier).validate_python(task_id)
        TypeAdapter(Identifier).validate_python(grant_id)
        context = TaskContext(
            task_id=task_id,
            grant_id=grant_id,
            purchase_id="purchase-" + digest({"task_id": task_id}),
        )
        with self.db.transaction() as db:
            old = db.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
            if old:
                if old["grant_id"] != grant_id:
                    raise PurchaseConflict("Task is already bound to another grant")
                return TaskContext(**dict(old))
            db.execute("INSERT INTO tasks VALUES(?,?,?)", (task_id, grant_id, context.purchase_id))
            self._event(db, context.purchase_id, "task_created", {"task_id": task_id}, now)
        return context

    def task(self, task_id: str) -> TaskContext:
        with self.db.connect() as db:
            row = db.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
            if not row:
                raise KeyError(task_id)
            return TaskContext(**dict(row))

    def approved(self, intent: PaymentIntent) -> bool:
        """The adapter accepts only the exact intent durably marked for dispatch."""
        with self.db.connect() as db:
            row = db.execute(
                "SELECT intent,payment_state FROM purchases WHERE purchase_id=?",
                (intent.purchase_id,),
            ).fetchone()
            return bool(
                row
                and row["intent"] == canonical(intent)
                and row["payment_state"] in (PaymentState.EXECUTING, PaymentState.UNKNOWN)
            )

    def agent_binding(self, task_id: str) -> str | None:
        """Original operator binding, independent of the expendable agent run store."""
        with self.db.connect() as db:
            row = db.execute(
                "SELECT data FROM agent_bindings WHERE task_id=?", (task_id,)
            ).fetchone()
            return row["data"] if row else None

    def initialize_agent(self, task_id: str, binding: str) -> None:
        """Operator-only enrollment. No grant, purchase or accounting changes."""
        with self.db.transaction() as db:
            task = db.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
            if not task:
                raise KeyError(task_id)
            if db.execute("SELECT 1 FROM agent_bindings WHERE task_id=?", (task_id,)).fetchone():
                raise PurchaseConflict("Agent already initialized; inspect or recover its metadata")
            if db.execute(
                "SELECT 1 FROM purchases WHERE purchase_id=?", (task["purchase_id"],)
            ).fetchone():
                raise PurchaseConflict("Initial agent binding requires an unpurchased task")
            db.execute("INSERT INTO agent_bindings VALUES(?,?)", (task_id, binding))

    def record_quote(self, quote: Quote) -> None:
        with self.db.transaction() as db:
            old = db.execute(
                "SELECT data FROM quotes WHERE quote_id=?", (quote.quote_id,)
            ).fetchone()
            if old and old["data"] != canonical(quote):
                raise PurchaseConflict("Quote ID already exists with different contents")
            db.execute(
                "INSERT OR IGNORE INTO quotes VALUES(?,?)", (quote.quote_id, canonical(quote))
            )

    def quote(self, quote_id: str) -> Quote:
        with self.db.connect() as db:
            row = db.execute("SELECT data FROM quotes WHERE quote_id=?", (quote_id,)).fetchone()
            if not row:
                raise KeyError(quote_id)
            return Quote.model_validate_json(row["data"])

    def _validate(self, db, request: PurchaseRequest, quote: Quote, grant_id: str, now: int):
        row = db.execute("SELECT * FROM grants WHERE grant_id=?", (grant_id,)).fetchone()
        if not row:
            raise PolicyDenied("grant_missing")
        if row["revoked"]:
            raise PolicyDenied("grant_revoked")
        grant = Grant.model_validate_json(row["data"])
        self.policy.validate(request, quote, grant, now)
        stored = db.execute(
            "SELECT data FROM quotes WHERE quote_id=?", (quote.quote_id,)
        ).fetchone()
        if not stored or stored["data"] != canonical(quote):
            raise PolicyDenied("quote_not_recorded_or_modified")
        return grant

    def reserve(
        self, purchase_id: str, request: PurchaseRequest, quote: Quote, grant_id: str, now: int
    ) -> bool:
        """True only for the creator. Duplicates never initiate another attempt."""
        TypeAdapter(Identifier).validate_python(purchase_id)
        TypeAdapter(Identifier).validate_python(grant_id)
        fingerprint = digest(
            {"request": request.model_dump(), "quote": quote.model_dump(), "grant_id": grant_id}
        )
        with self.db.transaction() as db:
            task = db.execute(
                "SELECT grant_id FROM tasks WHERE purchase_id=?", (purchase_id,)
            ).fetchone()
            if not task or task["grant_id"] != grant_id:
                raise PolicyDenied("task_not_registered")
            old = db.execute(
                "SELECT fingerprint FROM purchases WHERE purchase_id=?", (purchase_id,)
            ).fetchone()
            if old:
                if old["fingerprint"] != fingerprint:
                    raise PurchaseConflict(
                        "Purchase ID is bound to different request, quote, or grant"
                    )
                return False
            error = None
            intent = None
            try:
                grant = self._validate(db, request, quote, grant_id, now)
                changed = db.execute(
                    "UPDATE grants SET reserved=reserved+? WHERE grant_id=? "
                    "AND budget-spent-reserved>=?",
                    (quote.amount, grant_id, quote.amount),
                ).rowcount
                if not changed:
                    raise PolicyDenied("insufficient_budget")
                state = PaymentState.RESERVED if quote.amount else PaymentState.NOT_REQUIRED
                if quote.amount:
                    intent = PaymentIntent(
                        authorization_id="sim-auth-"
                        + digest({"id": purchase_id, "fp": fingerprint}),
                        purchase_id=purchase_id,
                        request_hash=digest(request),
                        quote_hash=digest(quote),
                        merchant_id=quote.merchant_id,
                        recipient=quote.recipient,
                        network=quote.network,
                        asset=quote.asset,
                        amount=quote.amount,
                        expires_at=min(quote.expires_at, grant.expires_at),
                    )
            except PolicyDenied as exc:
                state, error = PaymentState.DENIED, exc.code
            db.execute(
                "INSERT INTO purchases(purchase_id,fingerprint,grant_id,request,quote,amount,"
                "intent,authorization_id,payment_state,payment_error) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    purchase_id,
                    fingerprint,
                    grant_id,
                    canonical(request),
                    canonical(quote),
                    quote.amount,
                    canonical(intent) if intent else None,
                    intent.authorization_id if intent else None,
                    state,
                    error,
                ),
            )
            self._event(db, purchase_id, str(state), {"amount": quote.amount, "reason": error}, now)
            self._event(
                db,
                purchase_id,
                "authorization_decision",
                {
                    "approved": error is None,
                    "grant_id": grant_id,
                    "policy_version": "phase1-v1",
                    "fingerprint": fingerprint,
                },
                now,
            )
            return True

    def claim(self, purchase_id: str, now: int) -> PaymentIntent | None:
        """Persist dispatch before touching the adapter; recheck the grant and quote."""
        with self.db.transaction() as db:
            row = db.execute(
                "SELECT * FROM purchases WHERE purchase_id=?", (purchase_id,)
            ).fetchone()
            if row["payment_state"] != PaymentState.RESERVED:
                return None
            try:
                self._validate(
                    db,
                    PurchaseRequest.model_validate_json(row["request"]),
                    Quote.model_validate_json(row["quote"]),
                    row["grant_id"],
                    now,
                )
            except PolicyDenied as exc:
                db.execute(
                    "UPDATE grants SET reserved=reserved-? WHERE grant_id=?",
                    (row["amount"], row["grant_id"]),
                )
                db.execute(
                    "UPDATE purchases SET payment_state='denied',payment_error=? "
                    "WHERE purchase_id=?",
                    (exc.code, purchase_id),
                )
                self._event(db, purchase_id, "denied_before_dispatch", {"reason": exc.code}, now)
                return None
            db.execute(
                "UPDATE purchases SET payment_state='executing' WHERE purchase_id=?", (purchase_id,)
            )
            self._event(db, purchase_id, "dispatch_recorded", {}, now)
            return PaymentIntent.model_validate_json(row["intent"])

    def cancel_reserved(self, purchase_id: str, now: int) -> None:
        """Cancellation and dispatch compete in the same SQLite write transaction."""
        with self.db.transaction() as db:
            row = db.execute(
                "SELECT * FROM purchases WHERE purchase_id=?", (purchase_id,)
            ).fetchone()
            if row["payment_state"] != PaymentState.RESERVED:
                return
            db.execute(
                "UPDATE purchases SET payment_state='failed',payment_error='canceled_unsent' "
                "WHERE purchase_id=?",
                (purchase_id,),
            )
            db.execute(
                "UPDATE grants SET reserved=reserved-? WHERE grant_id=?",
                (row["amount"], row["grant_id"]),
            )
            self._event(db, purchase_id, "canceled_unsent", {}, now)

    def resolve(self, purchase_id: str, result: Resolution, now: int) -> None:
        with self.db.transaction() as db:
            row = db.execute(
                "SELECT * FROM purchases WHERE purchase_id=?", (purchase_id,)
            ).fetchone()
            if row["payment_state"] not in (PaymentState.EXECUTING, PaymentState.UNKNOWN):
                return  # Stale callbacks cannot downgrade terminal outcomes or count twice.
            intent = PaymentIntent.model_validate_json(row["intent"])
            if result.receipt and result.receipt.intent != intent:
                result = Resolution(status="unknown", reason="receipt_intent_mismatch")
            if result.status != "unknown":
                db.execute(
                    "UPDATE grants SET reserved=reserved-?,spent=spent+? WHERE grant_id=?",
                    (
                        row["amount"],
                        row["amount"] if result.status == "settled" else 0,
                        row["grant_id"],
                    ),
                )
            db.execute(
                "UPDATE purchases SET payment_state=?,payment_error=?,receipt=? "
                "WHERE purchase_id=?",
                (
                    result.status,
                    result.reason,
                    canonical(result.receipt) if result.receipt else None,
                    purchase_id,
                ),
            )
            self._event(db, purchase_id, result.status, {"reason": result.reason}, now)

    def record_delivery(
        self, purchase_id: str, resource: ResourceEnvelope | None, error: str | None, now: int
    ) -> None:
        with self.db.transaction() as db:
            changed = db.execute(
                "UPDATE purchases SET delivery_state=?,resource=?,delivery_error=? "
                "WHERE purchase_id=? AND payment_state IN ('settled','not_required') "
                "AND delivery_state!='delivered'",
                (
                    DeliveryState.DELIVERED if resource else DeliveryState.FAILED,
                    canonical(resource) if resource else None,
                    error,
                    purchase_id,
                ),
            ).rowcount
            if changed:
                self._event(
                    db,
                    purchase_id,
                    "delivered" if resource else "delivery_failed",
                    {"reason": error},
                    now,
                )

    def record(self, purchase_id: str, *, timeout: float | None = None) -> dict:
        connection = self.db.connect() if timeout is None else self.db.connect(timeout=timeout)
        with connection as db:
            row = db.execute(
                "SELECT * FROM purchases WHERE purchase_id=?", (purchase_id,)
            ).fetchone()
            if not row:
                raise KeyError(purchase_id)
            return dict(row)

    def outcome(self, purchase_id: str, *, timeout: float | None = None) -> PurchaseOutcome:
        row = self.record(purchase_id, timeout=timeout)
        return PurchaseOutcome(
            purchase_id=purchase_id,
            payment_state=row["payment_state"],
            delivery_state=row["delivery_state"],
            amount=row["amount"],
            payment_error=row["payment_error"],
            delivery_error=row["delivery_error"],
            receipt=SimulatedReceipt.model_validate_json(row["receipt"])
            if row["receipt"]
            else None,
            resource=ResourceEnvelope.model_validate_json(row["resource"])
            if row["resource"]
            else None,
        )

    def budget(self, grant_id: str) -> Budget:
        with self.db.connect() as db:
            row = db.execute("SELECT * FROM grants WHERE grant_id=?", (grant_id,)).fetchone()
            if not row:
                raise KeyError(grant_id)
            return Budget(
                limit=row["budget"],
                reserved=row["reserved"],
                spent=row["spent"],
                available=row["budget"] - row["reserved"] - row["spent"],
            )

    def buyer_constraints(self, grant_id: str, now: int) -> BuyerConstraints:
        """Read-only grant/accounting summary from one database row."""
        with self.db.connect() as db:
            row = db.execute("SELECT * FROM grants WHERE grant_id=?", (grant_id,)).fetchone()
            if not row:
                raise KeyError(grant_id)
            return BuyerConstraints(
                grant=Grant.model_validate_json(row["data"]),
                budget=Budget(
                    limit=row["budget"],
                    reserved=row["reserved"],
                    spent=row["spent"],
                    available=row["budget"] - row["reserved"] - row["spent"],
                ),
                revoked=bool(row["revoked"]),
                now=now,
            )

    def audit(self, purchase_id: str | None = None) -> list[dict]:
        with self.db.connect() as db:
            if purchase_id is None:
                rows = db.execute("SELECT * FROM events ORDER BY sequence").fetchall()
            else:
                rows = db.execute(
                    "SELECT * FROM events WHERE purchase_id=? ORDER BY sequence", (purchase_id,)
                ).fetchall()
            return [{**dict(row), "data": json.loads(row["data"])} for row in rows]

    def reconciliation_started(self, purchase_id: str, cancel: bool, now: int) -> None:
        with self.db.transaction() as db:
            self._event(db, purchase_id, "reconciliation_requested", {"cancel": cancel}, now)

    def export_audit(self, purchase_id: str) -> dict:
        """Explicit allowlist; omit receipt payloads, resource content, and raw requests."""
        with self.db.connect() as db, db:
            # A deferred read transaction keeps both SELECTs on one WAL snapshot
            # while allowing reconciliation to commit on another connection.
            db.execute("BEGIN")
            row = db.execute(
                "SELECT * FROM purchases WHERE purchase_id=?", (purchase_id,)
            ).fetchone()
            if not row:
                raise KeyError(purchase_id)
            events = db.execute(
                "SELECT * FROM events WHERE purchase_id=? ORDER BY sequence", (purchase_id,)
            ).fetchall()
        quote = Quote.model_validate_json(row["quote"])
        return {
            "mode": "simulated",
            "purchase_id": purchase_id,
            "request_fingerprint": row["fingerprint"],
            "grant_id": row["grant_id"],
            "policy_version": row["policy_version"],
            "service_id": quote.service_id,
            "merchant_id": quote.merchant_id,
            "network": quote.network,
            "asset": quote.asset,
            "recipient": quote.recipient,
            "amount": row["amount"],
            "payment_state": row["payment_state"],
            "delivery_state": row["delivery_state"],
            "events": [{**dict(event), "data": json.loads(event["data"])} for event in events],
        }
