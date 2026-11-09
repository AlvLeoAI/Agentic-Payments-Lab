"""Bounded, allowlisted historical data; validation does not authenticate evidence."""

import json
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

MAX_BYTES = 32_768
SNAPSHOT = Path(__file__).with_name("pilot-v1.json")
Count = Annotated[int, Field(strict=True, ge=0, le=10**9)]
Hash = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$", strict=True)]
Commit = Annotated[str, Field(pattern=r"^[0-9a-f]{40}$", strict=True)]


class SnapshotUnavailable(ValueError):
    """Fixed message only; never echo rejected fields or parser exceptions."""


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class Provenance(Record):
    recorded_at_utc: Literal["2026-11-08T21:34:45.215467+00:00"]
    tested_base_commit: Commit
    tested_manifest_sha256: Hash
    published_commit: Commit
    summary_sha256: Hash
    purchase_sha256: Hash
    replay_sha256: Hash
    refusal_sha256: Hash


class Allocation(Record):
    details_kind: Literal["absent"]
    observed_ttl_30m_tokens: None
    observed_ttl_12h_tokens: None
    derived_ttl_30m_tokens: Count
    derived_ttl_12h_tokens: Count

    @model_validator(mode="after")
    def zero_allocation(self):
        if self.derived_ttl_30m_tokens != 0 or self.derived_ttl_12h_tokens != 0:
            raise ValueError("allocation")
        return self


class Usage(Record):
    input_tokens: Count
    output_tokens: Count
    total_tokens: Count
    cached_tokens: Count
    cache_write_tokens: Count
    reasoning_tokens: Count

    @model_validator(mode="after")
    def totals(self):
        if (
            self.total_tokens != self.input_tokens + self.output_tokens
            or self.cached_tokens != 0
            or self.cache_write_tokens != 0
            or self.reasoning_tokens != 0
        ):
            raise ValueError("usage")
        return self


class Request(Record):
    ordinal: Annotated[int, Field(strict=True, ge=1, le=6)]
    usage: Usage
    cache_allocation: Allocation
    estimated_cost_nano_usd: Count

    @model_validator(mode="after")
    def cost(self):
        # Historical ordinary-input/output categories only; all cache counts are zero.
        if (
            self.usage.output_tokens > 512
            or self.usage.input_tokens > 272_000
            or self.estimated_cost_nano_usd
            != self.usage.input_tokens * 100 + self.usage.output_tokens * 500
        ):
            raise ValueError("cost")
        return self


class ToolEvent(Record):
    attempt: Annotated[int, Field(strict=True, ge=1, le=8)]
    tool: Literal["get_purchase_status", "list_services", "get_quote", "purchase"]
    outcome: Literal["ok"]


class Authority(Record):
    executions: Count
    authorizations: Count
    charges: Count
    charged_atomic: Count


class Scenario(Record):
    name: Literal["purchase", "replay", "refusal"]
    termination: Literal["completed"]
    decision: Literal["purchase_recorded", "no_suitable_service"]
    purchase_alias: Literal["purchase-1"] | None
    payment_state: Literal["settled", "not_started"]
    delivery_state: Literal["delivered", "not_requested"]
    purchase_amount_atomic: Count | None
    grant_spent_atomic: Count
    grant_reserved_atomic: Count
    authority_after: Authority
    requests: Annotated[list[Request], Field(min_length=1, max_length=6)]
    tool_trace: Annotated[list[ToolEvent], Field(min_length=1, max_length=8)]
    usage: Usage
    estimated_cost_nano_usd: Count

    @model_validator(mode="after")
    def consistent(self):
        if [r.ordinal for r in self.requests] != list(range(1, len(self.requests) + 1)):
            raise ValueError("request sequence")
        if [t.attempt for t in self.tool_trace] != list(range(1, len(self.tool_trace) + 1)):
            raise ValueError("tool sequence")
        for key in Usage.model_fields:
            if getattr(self.usage, key) != sum(getattr(r.usage, key) for r in self.requests):
                raise ValueError("request totals")
        if self.estimated_cost_nano_usd != sum(r.estimated_cost_nano_usd for r in self.requests):
            raise ValueError("scenario cost")
        paid = self.name != "refusal"
        expected = (
            ("purchase_recorded", "purchase-1", "settled", "delivered", 10_000, 10_000)
            if paid
            else ("no_suitable_service", None, "not_started", "not_requested", None, 0)
        )
        if (
            self.decision,
            self.purchase_alias,
            self.payment_state,
            self.delivery_state,
            self.purchase_amount_atomic,
            self.grant_spent_atomic,
        ) != expected or self.grant_reserved_atomic != 0:
            raise ValueError("financial state")
        tools = {
            "purchase": ["get_purchase_status", "list_services", "get_quote", "purchase"],
            "replay": ["get_purchase_status"],
            "refusal": ["get_purchase_status", "list_services"],
        }
        if [t.tool for t in self.tool_trace] != tools[self.name]:
            raise ValueError("historical tool trace")
        return self


class StoppedAttempt(Record):
    requests: Count
    observed_input_tokens: Count
    observed_output_tokens: Count
    usage_complete: bool
    accepted_estimate_nano_usd: None
    unresolved_reservation_nano_usd: Count

    @model_validator(mode="after")
    def uncertainty(self):
        if (
            self.requests != 1
            or self.observed_input_tokens != 510
            or self.observed_output_tokens != 15
            or self.usage_complete
            or self.unresolved_reservation_nano_usd != 1_239_000
        ):
            raise ValueError("stopped history")
        return self


class Snapshot(Record):
    schema_version: Annotated[int, Field(strict=True, ge=1, le=1)]
    snapshot_id: Literal["phase3b8-luna-2026-11-08"]
    model: Literal["gpt-6-luna"]
    reasoning_effort: Literal["none"]
    requested_tier: Literal["default"]
    returned_tier: Literal["default"]
    billing_verified: bool
    provenance: Provenance
    scenarios: Annotated[list[Scenario], Field(min_length=3, max_length=3)]
    fresh_usage: Usage
    fresh_estimated_cost_nano_usd: Count
    fresh_unresolved_reservation_nano_usd: Count
    original_stopped_attempt: StoppedAttempt

    @model_validator(mode="after")
    def historical_consistency(self):
        if self.billing_verified or self.fresh_unresolved_reservation_nano_usd != 0:
            raise ValueError("billing")
        if [s.name for s in self.scenarios] != ["purchase", "replay", "refusal"]:
            raise ValueError("scenario sequence")
        expected = Authority(executions=1, authorizations=1, charges=1, charged_atomic=10_000)
        if any(s.authority_after != expected for s in self.scenarios):
            raise ValueError("cumulative authority")
        for key in Usage.model_fields:
            if getattr(self.fresh_usage, key) != sum(getattr(s.usage, key) for s in self.scenarios):
                raise ValueError("fresh usage")
        if self.fresh_estimated_cost_nano_usd != sum(
            s.estimated_cost_nano_usd for s in self.scenarios
        ):
            raise ValueError("fresh cost")
        return self

    def charge_delta(self, index: int) -> int:
        previous = self.scenarios[index - 1].authority_after.charges if index else 0
        return self.scenarios[index].authority_after.charges - previous


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate field")
        result[key] = value
    return result


def _constant(_):
    raise ValueError("nonfinite number")


def decode_snapshot(data: bytes) -> Snapshot:
    try:
        if type(data) is not bytes or len(data) > MAX_BYTES:
            raise ValueError("size")
        value = json.loads(data, object_pairs_hook=_object, parse_constant=_constant)
        return Snapshot.model_validate(value)
    except (ValueError, TypeError, RecursionError, ValidationError):
        raise SnapshotUnavailable("Saved pilot data is unavailable or invalid.") from None


def load_snapshot() -> Snapshot:
    try:
        with SNAPSHOT.open("rb") as handle:
            return decode_snapshot(handle.read(MAX_BYTES + 1))
    except OSError:
        raise SnapshotUnavailable("Saved pilot data is unavailable or invalid.") from None


def usd(nano_usd: int) -> str:
    value = format(Decimal(nano_usd) / Decimal(1_000_000_000), ".9f").rstrip("0").rstrip(".")
    return "$" + value
