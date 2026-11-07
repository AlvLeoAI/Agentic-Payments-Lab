"""Trusted host adapter for the closed ASGI fixture; no live/backend auto-selection.

Only enrollment writes agent_bindings in the buyer file. Tools read that witness
and use the existing gate as the sole owner of reservations/signing/dispatch.
"""

import asyncio
import base64
from collections.abc import Callable
from typing import Literal

from pydantic import ValidationError

from ..domain import (
    AgentMetadataError,
    PurchaseConflict,
    PurchaseRequest,
    TaskContext,
    Timestamp,
    canonical,
    digest,
)
from ..x402_adapter.gate import OfflineGate, SignerDouble
from ..x402_adapter.protocol import required_data, validate_required
from ..x402_contracts import GateDenied, Hash, StrictModel
from ..x402_provider import OfflineMerchantClient
from ..x402_store import OfflineGateStore
from .boundary import Boundary
from .contracts import (
    CatalogResult,
    Goal,
    OfflinePurchaseSummary,
    QuoteResult,
    ServiceSummary,
    ToolError,
)
from .store import AgentStore


class Scope(StrictModel):
    payment_backend: Literal["offline_x402"] = "offline_x402"
    policy_hash: Hash
    request_hash: Hash
    requirements_hash: Hash


class Binding(StrictModel):
    context: TaskContext
    goal: Goal
    scope: Scope


class OfflineQuote(StrictModel):
    context: TaskContext
    scope: Scope
    issued_at: Timestamp
    expires_at: Timestamp


class OfflineX402Buyer:
    """Explicitly selected by host Python, over existing freshly initialized fixtures.

    Does not create tasks/merchants or open/migrate databases. No cancellation,
    reconciliation, retrieval or repair operation is exposed through this adapter.
    The injected double is trusted Python, not sandboxed executable code.
    """

    def __init__(
        self,
        store: OfflineGateStore,
        client: OfflineMerchantClient,
        signer: SignerDouble,
        clock: Callable[[], int],
        task_id: str,
    ):
        if (
            type(store) is not OfflineGateStore
            or type(client) is not OfflineMerchantClient
            or digest(store.policy) != digest(client.merchant.policy)
        ):
            raise GateDenied("offline_buyer_profile_required")
        self.store, self.client, self.clock = store, client, clock
        self.gate = OfflineGate(store, signer, client, clock, identified=True)
        with store.db.connect() as db:
            db.execute("BEGIN")
            task = store._task(db, task_id)
            self.context = TaskContext(
                task_id=task_id, grant_id=task["grant_id"], purchase_id=task["purchase_id"]
            )
            self.request = PurchaseRequest.model_validate_json(task["request"])
        self.scope = Scope(
            policy_hash=digest(store.policy),
            request_hash=digest(self.request),
            requirements_hash=digest(required_data(store.policy, identified=True)),
        )

    def _identity(self, db, task_id: str) -> None:
        task = self.store._task(db, task_id)
        if (
            task_id != self.context.task_id
            or task["grant_id"] != self.context.grant_id
            or task["purchase_id"] != self.context.purchase_id
            or task["request"] != canonical(self.request)
        ):
            raise AgentMetadataError("agent_recovery_required")

    def validate_goal(self, goal: Goal) -> None:
        goal = Goal.model_validate(goal.model_dump())
        if (
            goal.subject != self.request.subject
            or goal.purpose != self.request.purpose
            or goal.quality != "basic"
        ):
            raise PurchaseConflict("Offline agent goal must match the original basic fixture")

    def task(self, task_id: str) -> TaskContext:
        with self.store.db.connect() as db:
            self._identity(db, task_id)
        return self.context

    def _validate_binding(self, data: str) -> None:
        try:
            binding = Binding.model_validate_json(data)
            self.validate_goal(binding.goal)
            if binding.context != self.context or binding.scope != self.scope:
                raise ValueError
        except (ValueError, PurchaseConflict):
            raise AgentMetadataError("agent_recovery_required") from None

    def agent_binding(self, task_id: str) -> str | None:
        with self.store.db.connect() as db:
            db.execute("BEGIN")
            self._identity(db, task_id)
            row = db.execute(
                "SELECT data FROM agent_bindings WHERE task_id=?", (task_id,)
            ).fetchone()
        if row:
            self._validate_binding(row["data"])
        return row["data"] if row else None

    def initialize_agent(self, task_id: str, binding: str) -> None:
        """AgentStore commits this immutable independent witness before its own copy."""
        self._validate_binding(binding)
        with self.store.db.transaction() as db:
            self._identity(db, task_id)
            if any(
                db.execute(sql, (value,)).fetchone()
                for sql, value in (
                    ("SELECT 1 FROM agent_bindings WHERE task_id=?", task_id),
                    ("SELECT 1 FROM x402_attempts WHERE purchase_id=?", self.context.purchase_id),
                    ("SELECT 1 FROM x402_cancellations WHERE task_id=?", task_id),
                    ("SELECT 1 FROM purchases WHERE purchase_id=?", self.context.purchase_id),
                )
            ):
                raise AgentMetadataError("agent_recovery_required")
            db.execute("INSERT INTO agent_bindings VALUES(?,?)", (task_id, binding))

    def initialize(self, metadata: AgentStore, goal: Goal) -> None:
        """Explicit first-use enrollment; execution never calls this method."""
        self.validate_goal(goal)
        metadata.initialize(self, self.context.task_id, goal, scope=self.scope.model_dump())

    def recover(self, metadata: AgentStore) -> None:
        """Explicitly copy the verified original witness, never infer a new goal."""
        metadata.recover(self, self.context.task_id)


class OfflineBoundary(Boundary):
    """Same four argument schemas and common dispatch; backend-specific operations."""

    def __init__(self, session: OfflineX402Buyer, store: AgentStore, goal: Goal):
        self.session, self.store, self.goal = session, store, goal
        session.validate_goal(goal)
        self.history_incomplete = self._binding()

    def _binding(self) -> bool:
        return self.store.bind(
            self.session.context,
            self.goal,
            self.session.agent_binding(self.session.context.task_id),
            scope=self.session.scope.model_dump(),
        )

    @staticmethod
    def cancellation_fence() -> None:
        if asyncio.current_task().cancelling():
            raise asyncio.CancelledError("offline_buyer_canceled") from None

    def status(self, *, timeout: float | None = None) -> OfflinePurchaseSummary:
        report = self.session.store.report(self.session.context.task_id, timeout=timeout)
        if report["purchase_id"] != self.session.context.purchase_id:
            raise AgentMetadataError("agent_recovery_required")
        incident = report["reconciliation"]["operator_incident"]
        accepted = report["delivery"]["acceptance"]
        return OfflinePurchaseSummary(
            purchase_id=report["purchase_id"],
            payment_state=report["phase"],
            delivery_state=report["delivery_state"],
            amount_atomic=report["amount"],
            authorization_id=report["authorization_id"],
            dispatch_may_have_occurred=report["dispatch_may_have_occurred"],
            operator_reconciliation_required=bool(
                incident
                or report["reconciliation_required"]
                or (report["phase"] == "fixture_settled" and not accepted)
            ),
            operator_incident=incident,
            delivery_acceptance_hash=accepted["envelope_hash"] if accepted else None,
            reserved_atomic=report["budget"]["reserved"],
            spent_atomic=report["budget"]["spent"],
        )

    def has_purchase(self) -> bool:
        return self.status().payment_state != "not_started"

    def catalog(self) -> CatalogResult:
        session, eligible = self.session, []
        policy = session.store.policy
        with session.store.db.connect() as db:
            db.execute("BEGIN")
            session._identity(db, session.context.task_id)
            row = db.execute(
                "SELECT * FROM grants WHERE grant_id=?", (session.context.grant_id,)
            ).fetchone()
            if not row or row["data"] != canonical(policy.grant):
                raise GateDenied("grant_missing_or_changed")
            available = row["budget"] - row["reserved"] - row["spent"]
            incident = db.execute(
                "SELECT 1 FROM events WHERE event_type='offline_reconciliation_incident' LIMIT 1"
            ).fetchone()
            if (
                not incident
                and not row["revoked"]
                and policy.grant.issued_at <= session.clock() < policy.grant.expires_at
                and policy.amount <= min(available, policy.grant.per_purchase_limit)
            ):
                eligible.append(
                    ServiceSummary(
                        service_id=policy.service_id, quality="basic", amount_atomic=policy.amount
                    )
                )
        return CatalogResult(
            services=eligible,
            recommended_service_id=policy.service_id if eligible else None,
            reason="eligible" if eligible else "no_suitable_service",
            available_atomic=available,
            per_purchase_limit_atomic=policy.grant.per_purchase_limit,
            required_quality=self.goal.quality,
        )

    async def quote(self, service_id: str) -> QuoteResult:
        self.cancellation_fence()
        self._binding()
        session, policy = self.session, self.session.store.policy
        header = await session.client.challenge(session.context.purchase_id, session.request)
        self.cancellation_fence()
        fingerprint = validate_required(
            header, policy=policy, resource=policy.resource, method=policy.method, identified=True
        )
        if fingerprint != session.scope.requirements_hash:
            raise GateDenied("requirements_changed")
        now = session.clock()
        quote = OfflineQuote(
            context=session.context,
            scope=session.scope,
            issued_at=now,
            expires_at=min(now + policy.max_timeout_seconds, policy.grant.expires_at),
        )
        handle = "x402-quote-" + digest(quote)
        self._binding()
        # Persist only normalized public binding hashes, never headers or signatures.
        with self.store.db.transaction() as db:
            db.execute(
                "INSERT OR IGNORE INTO quote_handles VALUES(?,?,?,?)",
                (handle, session.context.task_id, canonical(session.request), canonical(quote)),
            )
        return QuoteResult(quote_id=handle, service_id=service_id, amount_atomic=policy.amount)

    async def purchase(self, quote_id: str):
        self.cancellation_fence()
        self._binding()
        session, policy = self.session, self.session.store.policy
        with self.store.db.connect() as db:
            row = db.execute(
                "SELECT * FROM quote_handles WHERE handle=? AND task_id=?",
                (quote_id, session.context.task_id),
            ).fetchone()
        try:
            if not row:
                raise ValueError
            quote = OfflineQuote.model_validate_json(row["quote"])
            if (
                quote.context != session.context
                or quote.scope != session.scope
                or row["request"] != canonical(session.request)
                or quote_id != "x402-quote-" + digest(quote)
            ):
                raise ValueError
        except (ValueError, ValidationError):
            return ToolError(error="invalid_quote_handle")
        if self.has_purchase():
            return self.status()  # Never enter the gate again for a recorded attempt/cancellation.
        now = session.clock()
        if not (
            quote.issued_at <= now < quote.expires_at <= policy.grant.expires_at
            and quote.expires_at - quote.issued_at <= policy.max_timeout_seconds
        ):
            return ToolError(error="invalid_quote_handle")
        if session.request.service_id != self.catalog().recommended_service_id:
            return ToolError(error="service_not_eligible")
        # The closed profile admits exactly these unsigned requirements. This is
        # not signature recovery, renewed authorization or a second merchant request.
        header = base64.b64encode(
            canonical(required_data(policy, identified=True)).encode()
        ).decode()
        self.cancellation_fence()
        await session.gate.run(
            session.context.task_id, header, resource=policy.resource, method=policy.method
        )
        return self.status()
