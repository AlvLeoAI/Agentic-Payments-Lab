"""Explicit local composition; no environment variables or remote endpoints."""

from collections.abc import Callable
from pathlib import Path

from .catalog import default_catalog
from .clock import Clock, FixedClock
from .coordinator import BuyerSession, Checkpoint, Coordinator, no_fault
from .ledger import Ledger
from .payments import PaymentFault, SimulatedPayments
from .policy import Policy
from .provider import DeliveryFault, MerchantClient, MockProvider


class Runtime:
    def __init__(
        self,
        directory: Path,
        *,
        clock: Clock | None = None,
        payment_fault: PaymentFault = PaymentFault.NONE,
        delivery_fault: DeliveryFault = DeliveryFault.NONE,
        checkpoint: Callable[[Checkpoint], None] = no_fault,
    ):
        self.clock = clock or FixedClock()
        self.catalog = default_catalog()
        self.ledger = Ledger(directory / "ledger.sqlite3", Policy(self.catalog))
        self.payments = SimulatedPayments(
            directory / "payments.sqlite3", self.clock, self.ledger, payment_fault
        )
        self.provider = MockProvider(
            directory / "provider.sqlite3", self.catalog, self.clock, self.payments, delivery_fault
        )
        self.merchant = MerchantClient(self.provider.app)
        self.coordinator = Coordinator(
            self.catalog, self.ledger, self.payments, self.merchant, self.clock, checkpoint
        )

    def buyer(self, task_id: str) -> BuyerSession:
        return BuyerSession(self.coordinator, task_id)
