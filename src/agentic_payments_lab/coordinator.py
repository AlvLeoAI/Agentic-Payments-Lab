"""Deterministic orchestration; all money decisions live below the buyer surface."""

from collections.abc import Callable
from enum import StrEnum

from .catalog import Catalog
from .clock import Clock
from .domain import (
    DeliveryRequest,
    DeliveryState,
    PaymentIntent,
    PaymentState,
    PurchaseOutcome,
    PurchaseRequest,
    Quote,
    Resolution,
    SimulatedReceipt,
    digest,
)
from .ledger import Ledger
from .payments import PaymentAdapter
from .provider import MerchantClient


class Checkpoint(StrEnum):
    AFTER_RESERVATION = "after_reservation"
    AFTER_DISPATCH = "after_dispatch"
    AFTER_PAYMENT = "after_payment"
    AFTER_SETTLEMENT = "after_settlement"
    AFTER_DELIVERY = "after_delivery"


class SimulatedCrash(BaseException):
    """Test-only interruption that bypasses normal exception handling."""


def no_fault(checkpoint: Checkpoint) -> None:
    pass


class Coordinator:
    def __init__(
        self,
        catalog: Catalog,
        ledger: Ledger,
        payments: PaymentAdapter,
        merchant: MerchantClient,
        clock: Clock,
        checkpoint: Callable[[Checkpoint], None] = no_fault,
    ):
        self.catalog, self.ledger, self.payments = catalog, ledger, payments
        self.merchant, self.clock, self.checkpoint = merchant, clock, checkpoint

    async def quote(self, request: PurchaseRequest) -> Quote:
        self.catalog.get(request.service_id)
        quote = await self.merchant.quote(request)
        self.ledger.record_quote(quote)
        return quote

    async def purchase(
        self, task_id: str, request: PurchaseRequest, quote: Quote
    ) -> PurchaseOutcome:
        task = self.ledger.task(task_id)
        created = self.ledger.reserve(
            task.purchase_id, request, quote, task.grant_id, self.clock.now()
        )
        if not created:
            return self.ledger.outcome(task.purchase_id)
        outcome = self.ledger.outcome(task.purchase_id)
        if outcome.payment_state == PaymentState.DENIED:
            return outcome
        self.checkpoint(Checkpoint.AFTER_RESERVATION)
        if outcome.payment_state == PaymentState.RESERVED:
            intent = self.ledger.claim(task.purchase_id, self.clock.now())
            if intent:
                self.checkpoint(Checkpoint.AFTER_DISPATCH)
                try:
                    result = self.payments.execute(intent)
                except Exception:
                    # Never put exception bodies (potential proofs/secrets) in ordinary records.
                    result = Resolution(status="unknown", reason="adapter_response_unavailable")
                self.checkpoint(Checkpoint.AFTER_PAYMENT)
                self.ledger.resolve(task.purchase_id, result, self.clock.now())
                self.checkpoint(Checkpoint.AFTER_SETTLEMENT)
        await self._deliver(task.purchase_id)
        return self.ledger.outcome(task.purchase_id)

    async def _deliver(self, purchase_id: str) -> None:
        row = self.ledger.record(purchase_id)
        if (
            row["payment_state"] not in (PaymentState.SETTLED, PaymentState.NOT_REQUIRED)
            or row["delivery_state"] == DeliveryState.DELIVERED
        ):
            return
        request = PurchaseRequest.model_validate_json(row["request"])
        quote = Quote.model_validate_json(row["quote"])
        receipt = SimulatedReceipt.model_validate_json(row["receipt"]) if row["receipt"] else None
        try:
            resource = await self.merchant.deliver(
                DeliveryRequest(
                    purchase_id=purchase_id,
                    request=request,
                    quote=quote,
                    receipt=receipt,
                )
            )
            service = self.catalog.get(request.service_id)
            if (
                resource.purchase_id != purchase_id
                or resource.resource_hash != digest(resource.resource)
                or resource.receipt_id != (receipt.receipt_id if receipt else None)
                or resource.resource.request_hash != digest(request)
                or resource.resource.service_id != request.service_id
                or resource.resource.subject != request.subject
                or resource.resource.quality != service.quality
            ):
                raise ValueError("Delivery does not match the purchase")
        except Exception:
            self.ledger.record_delivery(
                purchase_id, None, "delivery_unavailable_or_invalid", self.clock.now()
            )
            return
        self.checkpoint(Checkpoint.AFTER_DELIVERY)
        self.ledger.record_delivery(purchase_id, resource, None, self.clock.now())

    async def reconcile(self, task_id: str, *, cancel: bool = False) -> PurchaseOutcome:
        """Operator operation. Lookup/cancel only: this never calls execute()."""
        purchase_id = self.ledger.task(task_id).purchase_id
        self.ledger.reconciliation_started(purchase_id, cancel, self.clock.now())
        row = self.ledger.record(purchase_id)
        if row["payment_state"] == PaymentState.RESERVED:
            self.ledger.cancel_reserved(purchase_id, self.clock.now())
            row = self.ledger.record(purchase_id)
        if row["payment_state"] in (PaymentState.EXECUTING, PaymentState.UNKNOWN):
            intent = PaymentIntent.model_validate_json(row["intent"])
            try:
                result = self.payments.cancel(intent) if cancel else self.payments.lookup(intent)
            except Exception:
                result = Resolution(status="unknown", reason="reconciliation_unavailable")
            self.ledger.resolve(purchase_id, result, self.clock.now())
        await self._deliver(purchase_id)
        return self.ledger.outcome(purchase_id)


class BuyerSession:
    """Proposed future tool surface, bound by the application to a single task.

    This is an API boundary, not a Python sandbox. A model must only receive tool
    schemas/callables, never Python execution or access to the Runtime object.
    """

    def __init__(self, coordinator: Coordinator, task_id: str):
        self._coordinator, self._task = coordinator, coordinator.ledger.task(task_id)

    def list_services(self) -> list[dict]:
        return self._coordinator.catalog.describe()

    async def quote(self, request: PurchaseRequest) -> Quote:
        return await self._coordinator.quote(request)

    async def purchase(self, request: PurchaseRequest, quote: Quote) -> PurchaseOutcome:
        return await self._coordinator.purchase(self._task.task_id, request, quote)

    def status(self) -> PurchaseOutcome:
        return self._coordinator.ledger.outcome(self._task.purchase_id)
