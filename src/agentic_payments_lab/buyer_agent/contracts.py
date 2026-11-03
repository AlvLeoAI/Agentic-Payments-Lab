"""Application-owned inputs and minimal, sanitized agent results."""

from typing import Annotated, Literal

from pydantic import ConfigDict, Field

from ..domain import Atomic, DeliveryState, Identifier, Model, PaymentState

Quality = Literal["basic", "standard", "premium"]
ToolName = Literal["list_services", "get_quote", "purchase", "get_purchase_status", "unavailable"]


class Goal(Model):
    subject: Annotated[str, Field(min_length=1, max_length=120)]
    purpose: Identifier
    quality: Quality


class Settings(Model):
    mode: Literal["offline", "live"] = "offline"
    model: (
        Annotated[str, Field(min_length=1, max_length=100, pattern=r"^[a-zA-Z0-9._:-]+$")] | None
    ) = None
    reasoning_effort: Literal["none", "minimal", "low", "medium", "high", "xhigh", "max"] | None = (
        None
    )
    service_tier: (
        Literal["auto", "default", "flex", "scale", "priority", "fast", "ultrafast"] | None
    ) = None
    tool_limit: Annotated[int, Field(strict=True, ge=1, le=8)] = 8
    max_turns: Annotated[int, Field(strict=True, ge=1, le=16)] = 10
    timeout_seconds: Annotated[float, Field(gt=0, le=120, allow_inf_nan=False)] = 30
    max_output_tokens: Annotated[int, Field(strict=True, ge=128, le=2048)] = 512


class Arguments(Model):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


class ServiceArguments(Arguments):
    service_id: Identifier


class QuoteArguments(Arguments):
    quote_id: Identifier


class Explanation(Model):
    explanation: Annotated[str, Field(max_length=1000)]


class PurchaseSummary(Model):
    purchase_id: Identifier
    payment_state: PaymentState | Literal["not_started"]
    delivery_state: DeliveryState
    amount_atomic: Atomic | None
    simulated_receipt_id: Identifier | None
    operator_reconciliation_required: bool


class ServiceSummary(Model):
    service_id: Identifier
    quality: Quality
    amount_atomic: Atomic


class CatalogResult(Model):
    services: list[ServiceSummary]
    recommended_service_id: Identifier | None
    reason: Literal["eligible", "no_suitable_service"]
    available_atomic: Atomic
    per_purchase_limit_atomic: Atomic
    required_quality: Quality


class QuoteResult(Model):
    quote_id: Identifier
    service_id: Identifier
    amount_atomic: Atomic


class ToolError(Model):
    error: Literal[
        "invalid_arguments",
        "invalid_quote_handle",
        "service_not_eligible",
        "purchase_exists",
        "operation_rejected",
    ]


class ToolEvent(Model):
    attempt: int
    tool: ToolName
    outcome: str  # Application-generated codes only; no arguments or exception messages.


class ModelUsage(Model):
    source: Literal["scripted", "openai"]
    requests_started: int = 0
    responses_received: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    unresolved_requests: int = (
        0  # Attempts without complete observed usage, including pending ones.
    )
    incomplete: bool = False
    api_cost: None = None  # Tokens are not SIM-USDC and are not a billing estimate.


class AgentReport(Model):
    run_id: str
    mode: Literal["offline", "live"]
    model: str
    task_id: Identifier
    termination: str
    decision: Literal[
        "no_suitable_service",
        "not_purchased",
        "purchase_recorded",
        "operator_reconciliation_required",
    ]
    purchase: PurchaseSummary
    model_explanation: str | None
    model_usage: ModelUsage
    metadata_persisted: bool = True
    run_complete: bool = False
    history_incomplete: bool = False
    tool_attempts: int
    events: list[ToolEvent]
