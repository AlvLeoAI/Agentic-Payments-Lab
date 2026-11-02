"""Vendor-independent contracts. Money is always an integer, never a float."""

import hashlib
import json
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

MAX_ATOMIC = 9_000_000_000_000_000  # Safely below SQLite's signed integer ceiling.
Atomic = Annotated[int, Field(strict=True, ge=0, le=MAX_ATOMIC)]
Timestamp = Annotated[int, Field(strict=True, ge=0, le=MAX_ATOMIC)]
Identifier = Annotated[str, Field(min_length=1, max_length=100, pattern=r"^[a-zA-Z0-9_-]+$")]


class Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


def canonical(value: BaseModel | dict) -> str:
    data = value.model_dump(mode="json") if isinstance(value, BaseModel) else value
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def digest(value: BaseModel | dict) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def format_usdc(amount: int) -> str:
    if type(amount) is not int or not 0 <= amount <= MAX_ATOMIC:
        raise ValueError("Expected a nonnegative integer atomic amount")
    return f"{amount // 1_000_000}.{amount % 1_000_000:06d}"


class PurchaseRequest(Model):
    service_id: Identifier
    subject: Annotated[str, Field(min_length=1, max_length=120)]
    purpose: Identifier


class Service(Model):
    service_id: Identifier
    merchant_id: Identifier
    recipient: Identifier
    name: str
    description: str
    quality: Literal["basic", "standard", "premium"]
    amount: Atomic
    network: Literal["simulation:local"] = "simulation:local"
    asset: Literal["SIM-USDC"] = "SIM-USDC"
    decimals: Literal[6] = 6
    origin: Literal["http://mock-provider.invalid"] = "http://mock-provider.invalid"
    route: Literal["/resources"] = "/resources"


class Grant(Model):
    grant_id: Identifier
    operator_id: Identifier
    service_ids: tuple[Identifier, ...]
    purpose: Identifier
    budget: Atomic
    per_purchase_limit: Atomic
    issued_at: Timestamp
    expires_at: Timestamp

    @model_validator(mode="after")
    def valid_window(self):
        if self.expires_at <= self.issued_at:
            raise ValueError("Grant must have a positive validity window")
        if self.per_purchase_limit > self.budget:
            raise ValueError("Per-purchase cap exceeds grant budget")
        return self


class Quote(Model):
    quote_id: Identifier
    request_hash: str
    service_id: Identifier
    merchant_id: Identifier
    recipient: Identifier
    network: str
    asset: str
    amount: Atomic
    decimals: Annotated[int, Field(strict=True)] = 6
    origin: str
    route: str
    issued_at: Timestamp
    expires_at: Timestamp


class PaymentIntent(Model):
    authorization_id: Identifier
    purchase_id: Identifier
    request_hash: str
    quote_hash: str
    merchant_id: Identifier
    recipient: Identifier
    network: str
    asset: str
    amount: Atomic
    expires_at: Timestamp
    policy_version: Literal["phase1-v1"] = "phase1-v1"


class TaskContext(Model):
    task_id: Identifier
    grant_id: Identifier
    purchase_id: Identifier


class SimulatedReceipt(Model):
    mode: Literal["simulated"] = "simulated"
    receipt_id: Identifier
    intent: PaymentIntent
    settled_at: Timestamp


class Resolution(Model):
    status: Literal["settled", "failed", "unknown"]
    receipt: SimulatedReceipt | None = None
    reason: str | None = None

    @model_validator(mode="after")
    def receipt_matches_status(self):
        if (self.status == "settled") != (self.receipt is not None):
            raise ValueError("Only a settled resolution contains a receipt")
        return self


class PaymentState(StrEnum):
    DENIED = "denied"
    RESERVED = "reserved"
    EXECUTING = "executing"
    UNKNOWN = "unknown"
    SETTLED = "settled"
    FAILED = "failed"
    NOT_REQUIRED = "not_required"


class DeliveryState(StrEnum):
    NOT_REQUESTED = "not_requested"
    FAILED = "failed"
    DELIVERED = "delivered"


class Resource(Model):
    service_id: Identifier
    subject: str
    request_hash: str
    quality: Literal["basic", "standard", "premium"]
    summary: str


class ResourceEnvelope(Model):
    purchase_id: Identifier
    resource: Resource
    resource_hash: str
    receipt_id: str | None


class DeliveryRequest(Model):
    purchase_id: Identifier
    request: PurchaseRequest
    quote: Quote
    receipt: SimulatedReceipt | None = None


class PurchaseOutcome(Model):
    mode: Literal["simulated"] = "simulated"
    purchase_id: Identifier
    payment_state: PaymentState
    delivery_state: DeliveryState
    amount: Atomic
    payment_error: str | None = None
    delivery_error: str | None = None
    receipt: SimulatedReceipt | None = None
    resource: ResourceEnvelope | None = None


class Budget(Model):
    limit: Atomic
    reserved: Atomic
    spent: Atomic
    available: Atomic


class PurchaseConflict(ValueError):
    """A durable identifier was reused with different contents."""


class PolicyDenied(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)
