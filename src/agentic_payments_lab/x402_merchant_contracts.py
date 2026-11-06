"""Public offline merchant records. No SDK, signing, transport or financial authority."""

from typing import Literal

from pydantic import model_validator

from .domain import Identifier, PurchaseRequest, TaskContext, Timestamp, digest
from .x402_contracts import Address, Hash, Hex32, PositiveAmount, StrictModel


class MerchantOrder(StrictModel):
    context: TaskContext
    request: PurchaseRequest


class MerchantRequest(StrictModel):
    purchase_id: Identifier
    request: PurchaseRequest


class MerchantAuthorization(StrictModel):
    payer: Address
    recipient: Address
    amount: PositiveAmount
    valid_after: Timestamp
    valid_before: Timestamp
    nonce: Hex32

    def wire_data(self) -> dict:
        return {
            "from": self.payer,
            "to": self.recipient,
            "value": str(self.amount),
            "validAfter": str(self.valid_after),
            "validBefore": str(self.valid_before),
            "nonce": self.nonce,
        }


class MerchantSubmission(StrictModel):
    payment_id: Identifier
    authorization: MerchantAuthorization
    payload_hash: Hash


class SettlementObservation(StrictModel):
    """A double's report, never conclusive evidence for buyer accounting."""

    source: Literal["offline_double"] = "offline_double"
    outcome: Literal["reported_success", "reported_failure", "pending", "unknown"]
    transaction: Hex32 | None = None

    @model_validator(mode="after")
    def bounded_observation(self):
        if self.outcome == "reported_success" and self.transaction is None:
            raise ValueError("Reported success requires a synthetic transaction identifier")
        if self.outcome != "reported_success" and self.transaction is not None:
            raise ValueError("Transaction identifiers are only retained for reported success")
        return self


def order_fingerprint(order: MerchantOrder, resource: str, method: str, policy_hash: str) -> str:
    return digest(
        {"order": order.model_dump(), "resource": resource, "method": method, "policy": policy_hash}
    )


def payment_identifier(purchase_id: str) -> str:
    """Host-derived, stable across restarts; distinct from the authorization nonce."""
    return "pay_" + digest({"purchase_id": purchase_id})
