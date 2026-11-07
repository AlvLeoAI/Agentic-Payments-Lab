"""Narrow application tools over BuyerSession; no SDK or model-based authorization."""

from pydantic import ValidationError

from ..coordinator import BuyerSession
from ..domain import Model, PurchaseRequest
from .contracts import (
    Arguments,
    CatalogResult,
    Goal,
    PurchaseSummary,
    QuoteArguments,
    QuoteResult,
    ServiceArguments,
    ServiceSummary,
    ToolError,
)
from .store import AgentStore

QUALITY = {"basic": 0, "standard": 1, "premium": 2}
ARGUMENTS = {
    "list_services": Arguments,
    "get_quote": ServiceArguments,
    "purchase": QuoteArguments,
    "get_purchase_status": Arguments,
}


class Boundary:
    def __init__(self, session: BuyerSession, store: AgentStore, goal: Goal):
        self.session, self.store, self.goal = session, store, goal
        self.history_incomplete = store.bind(session.context, goal, session.agent_binding())

    def status(self, *, timeout: float | None = None) -> PurchaseSummary:
        outcome = self.session.status_or_none(timeout=timeout)
        return PurchaseSummary(
            purchase_id=self.session.context.purchase_id,
            payment_state=outcome.payment_state if outcome else "not_started",
            delivery_state=outcome.delivery_state if outcome else "not_requested",
            amount_atomic=outcome.amount if outcome else None,
            simulated_receipt_id=outcome.receipt.receipt_id
            if outcome and outcome.receipt
            else None,
            operator_reconciliation_required=bool(
                outcome
                and (
                    outcome.payment_state in ("reserved", "executing", "unknown")
                    or (
                        outcome.payment_state in ("settled", "not_required")
                        and outcome.delivery_state != "delivered"
                    )
                )
            ),
        )

    def catalog(self) -> CatalogResult:
        constraints = self.session.constraints()
        grant, goal = constraints.grant, self.goal
        eligible = []
        if (
            not constraints.revoked
            and grant.issued_at <= constraints.now < grant.expires_at
            and goal.purpose == grant.purpose
        ):
            for service in self.session.list_services():
                if (
                    service["service_id"] in grant.service_ids
                    and QUALITY[service["quality"]] >= QUALITY[goal.quality]
                    and service["amount"]
                    <= min(grant.per_purchase_limit, constraints.budget.available)
                ):
                    eligible.append(
                        ServiceSummary(
                            service_id=service["service_id"],
                            quality=service["quality"],
                            amount_atomic=service["amount"],
                        )
                    )
        eligible.sort(key=lambda service: (service.amount_atomic, service.service_id))
        # Only trusted catalog terms are returned: no merchant descriptions or delivered prose.
        return CatalogResult(
            services=eligible,
            recommended_service_id=eligible[0].service_id if eligible else None,
            reason="eligible" if eligible else "no_suitable_service",
            available_atomic=constraints.budget.available,
            per_purchase_limit_atomic=grant.per_purchase_limit,
            required_quality=goal.quality,
        )

    async def invoke(self, name: str, raw: str) -> Model:
        if len(raw.encode()) > 1024:
            return ToolError(error="invalid_arguments")
        try:
            args = ARGUMENTS[name].model_validate_json(raw)
        except (ValidationError, ValueError):
            return ToolError(error="invalid_arguments")
        if name == "list_services":
            return self.catalog()
        if name == "get_purchase_status":
            return self.status()
        if name == "get_quote":
            if self.has_purchase():
                return ToolError(error="purchase_exists")
            if args.service_id != self.catalog().recommended_service_id:
                return ToolError(error="service_not_eligible")
            return await self.quote(args.service_id)
        return await self.purchase(args.quote_id)

    def has_purchase(self) -> bool:
        return self.session.status_or_none() is not None

    async def quote(self, service_id: str) -> QuoteResult:
        request = PurchaseRequest(
            service_id=service_id, subject=self.goal.subject, purpose=self.goal.purpose
        )
        quote = await self.session.quote(request)
        handle = self.store.quote(self.session.context, request, quote)
        return QuoteResult(quote_id=handle, service_id=service_id, amount_atomic=quote.amount)

    async def purchase(self, quote_id: str) -> Model:
        try:
            request, quote = self.store.resolve(self.session.context.task_id, quote_id)
        except KeyError:
            return ToolError(error="invalid_quote_handle")
        if request.subject != self.goal.subject or request.purpose != self.goal.purpose:
            return ToolError(error="invalid_quote_handle")
        if self.session.status_or_none():
            return self.status()  # Never resume payment or delivery from an agent retry.
        if request.service_id != self.catalog().recommended_service_id:
            return ToolError(error="service_not_eligible")
        await self.session.purchase(request, quote)
        return self.status()
