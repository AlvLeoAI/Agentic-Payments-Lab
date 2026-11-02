"""Offline payment authority with durable receipts and cancellation fencing."""

from enum import StrEnum
from pathlib import Path
from typing import Protocol

from .clock import Clock
from .domain import PaymentIntent, PurchaseConflict, Resolution, SimulatedReceipt, canonical, digest
from .storage import Database


class PaymentAdapter(Protocol):
    def execute(self, intent: PaymentIntent) -> Resolution: ...
    def lookup(self, intent: PaymentIntent) -> Resolution: ...
    def cancel(self, intent: PaymentIntent) -> Resolution: ...


class ApprovedIntents(Protocol):
    def approved(self, intent: PaymentIntent) -> bool: ...


class PaymentUncertain(RuntimeError):
    pass


class PaymentFault(StrEnum):
    NONE = "none"
    DECLINE = "decline"
    TIMEOUT_BEFORE_COMMIT = "timeout_before_commit"
    TIMEOUT_AFTER_COMMIT = "timeout_after_commit"


SCHEMA = """
CREATE TABLE IF NOT EXISTS authorizations (
    authorization_id TEXT PRIMARY KEY,
    purchase_id TEXT NOT NULL UNIQUE,
    intent TEXT NOT NULL,
    result TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS attempts (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    authorization_id TEXT NOT NULL,
    recorded_at INTEGER NOT NULL
);
"""


class SimulatedPayments:
    def __init__(
        self,
        path: Path,
        clock: Clock,
        approvals: ApprovedIntents,
        fault: PaymentFault = PaymentFault.NONE,
    ):
        self.db = Database(path, SCHEMA)
        self.clock = clock
        self.fault = fault
        self.approvals = approvals

    @staticmethod
    def _stored(db, intent: PaymentIntent) -> Resolution | None:
        row = db.execute(
            "SELECT * FROM authorizations WHERE authorization_id=? OR purchase_id=?",
            (intent.authorization_id, intent.purchase_id),
        ).fetchone()
        if row:
            if row["intent"] != canonical(intent):
                raise PurchaseConflict("Payment identity already bound to another intent")
            return Resolution.model_validate_json(row["result"])
        return None

    def execute(self, intent: PaymentIntent) -> Resolution:
        if not self.approvals.approved(intent):
            raise PermissionError("Intent has no durable dispatch authorization")
        with self.db.transaction() as db:
            db.execute(
                "INSERT INTO attempts(authorization_id,recorded_at) VALUES(?,?)",
                (intent.authorization_id, self.clock.now()),
            )
            old = self._stored(db, intent)
            if old:
                return old  # A cancellation tombstone also fences a delayed executor.
            if (
                intent.network != "simulation:local"
                or intent.asset != "SIM-USDC"
                or intent.amount <= 0
                or self.clock.now() >= intent.expires_at
            ):
                result = Resolution(status="failed", reason="invalid_simulation_intent")
            elif self.fault == PaymentFault.DECLINE:
                result = Resolution(status="failed", reason="simulated_decline")
            elif self.fault == PaymentFault.TIMEOUT_BEFORE_COMMIT:
                result = Resolution(status="unknown", reason="simulated_pending")
            else:
                result = Resolution(
                    status="settled",
                    receipt=SimulatedReceipt(
                        receipt_id="sim-receipt-" + digest(intent),
                        intent=intent,
                        settled_at=self.clock.now(),
                    ),
                )
            db.execute(
                "INSERT INTO authorizations VALUES(?,?,?,?)",
                (intent.authorization_id, intent.purchase_id, canonical(intent), canonical(result)),
            )
        if (
            self.fault == PaymentFault.TIMEOUT_BEFORE_COMMIT
            and result.status == "unknown"
            or self.fault == PaymentFault.TIMEOUT_AFTER_COMMIT
            and result.status == "settled"
        ):
            raise PaymentUncertain("Simulated response lost; inspect the existing authorization")
        return result

    def lookup(self, intent: PaymentIntent) -> Resolution:
        with self.db.connect() as db:
            return self._stored(db, intent) or Resolution(status="unknown", reason="not_observed")

    def cancel(self, intent: PaymentIntent) -> Resolution:
        """Authoritative simulation-only cancellation, atomic against execute()."""
        if not self.approvals.approved(intent):
            raise PermissionError("Intent has no durable dispatch authorization")
        with self.db.transaction() as db:
            old = self._stored(db, intent)
            if old and old.status != "unknown":
                return old
            result = Resolution(status="failed", reason="simulated_cancellation_confirmed")
            db.execute(
                "INSERT INTO authorizations VALUES(?,?,?,?) "
                "ON CONFLICT(authorization_id) DO UPDATE SET result=excluded.result",
                (intent.authorization_id, intent.purchase_id, canonical(intent), canonical(result)),
            )
            return result

    def verifies(self, receipt: SimulatedReceipt) -> bool:
        result = self.lookup(receipt.intent)
        return result.status == "settled" and result.receipt == receipt

    def statistics(self) -> dict[str, int]:
        with self.db.connect() as db:
            results = [
                Resolution.model_validate_json(row[0])
                for row in db.execute("SELECT result FROM authorizations")
            ]
            return {
                "execution_attempts": db.execute("SELECT count(*) FROM attempts").fetchone()[0],
                "logical_charges": sum(result.status == "settled" for result in results),
                "charged_atomic": sum(
                    result.receipt.intent.amount for result in results if result.receipt
                ),
                "signing_attempts": 0,  # This adapter never performs cryptographic signing.
            }
