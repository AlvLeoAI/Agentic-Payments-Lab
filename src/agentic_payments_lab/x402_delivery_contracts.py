"""Bounded offline delivery records; no transport or financial authority."""

import json
from typing import Annotated, Literal

from pydantic import Field, model_validator

from .domain import Identifier, PurchaseRequest, TaskContext, digest
from .x402_contracts import Hash, SigningTerms, StrictModel
from .x402_merchant_contracts import SettlementObservation, payment_identifier

MAX_DELIVERY_BYTES = 16_384


class DeliveryLimits(StrictModel):
    timeout_seconds: Annotated[int, Field(ge=1, le=30)] = 5


class DeliveryResource(StrictModel):
    service_id: Identifier
    subject: Annotated[str, Field(min_length=1, max_length=120)]
    request_hash: Hash
    quality: Literal["basic"]
    summary: Annotated[str, Field(max_length=256)]


class DeliveryEnvelope(StrictModel):
    purchase_id: Identifier
    resource: DeliveryResource
    resource_hash: Hash
    receipt_id: None


class MerchantDeliveryReport(StrictModel):
    mode: Literal["offline_x402_merchant"]
    blockchain_settlement_verified: bool
    purchase_id: Identifier
    payment_id: Identifier
    settlement_may_have_occurred: bool
    settlement_observation: SettlementObservation | None
    delivery_state: Literal["not_ready", "pending", "gone", "cached"]
    resource: DeliveryEnvelope | None

    @model_validator(mode="after")
    def coherent_report(self):
        if self.blockchain_settlement_verified or (
            (self.delivery_state == "cached") != (self.resource is not None)
        ):
            raise ValueError("Invalid offline delivery report")
        if self.resource is not None and (
            not self.settlement_may_have_occurred
            or self.settlement_observation is None
            or self.settlement_observation.outcome != "reported_success"
        ):
            raise ValueError("Invalid offline cached delivery")
        return self


def parse_delivery_report(raw: bytes) -> MerchantDeliveryReport:
    if type(raw) is not bytes or len(raw) > MAX_DELIVERY_BYTES:
        raise ValueError("Offline delivery response exceeds limit")

    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate delivery field")
            result[key] = value
        return result

    return MerchantDeliveryReport.model_validate(
        json.loads(raw, object_pairs_hook=unique), strict=True
    )


class DeliveryBinding(StrictModel):
    context: TaskContext
    request: PurchaseRequest
    terms: SigningTerms


class DeliveryAcceptance(StrictModel):
    source: Literal["offline_fixture"] = "offline_fixture"
    binding_hash: Hash
    authorization_id: Identifier
    request_hash: Hash
    terms_hash: Hash
    resource_hash: Hash
    envelope_hash: Hash
    quality: Literal["basic"] = "basic"


class DeliveryFailure(StrictModel):
    source: Literal["offline_fixture"] = "offline_fixture"
    binding_hash: Hash
    reason: Literal["unavailable_or_invalid", "timeout", "not_ready", "pending", "gone"]


def validate_delivery_identity(binding: DeliveryBinding, report: MerchantDeliveryReport):
    if report.purchase_id != binding.context.purchase_id or report.payment_id != payment_identifier(
        binding.context.purchase_id
    ):
        raise ValueError("Offline delivery identity mismatch")


def acceptance_for(binding: DeliveryBinding, report: MerchantDeliveryReport) -> DeliveryAcceptance:
    validate_delivery_identity(binding, report)
    envelope = report.resource
    request = binding.request
    if envelope is None or report.delivery_state != "cached":
        raise ValueError("Offline resource unavailable")
    resource = envelope.resource
    if (
        envelope.purchase_id != binding.context.purchase_id
        or resource.request_hash != digest(request)
        or resource.request_hash != binding.terms.request_hash
        or resource.service_id != request.service_id
        or resource.subject != request.subject
        or resource.summary != f"Offline x402 fixture report for {request.subject}."
        or envelope.resource_hash != digest(resource)
    ):
        raise ValueError("Offline resource mismatch")
    # The existing merchant supplies this exact deterministic basic fixture.
    # These digests attest acceptance of fixture content, never chain settlement.
    return DeliveryAcceptance(
        binding_hash=digest(binding),
        authorization_id=binding.terms.authorization_id,
        request_hash=resource.request_hash,
        terms_hash=digest(binding.terms),
        resource_hash=envelope.resource_hash,
        envelope_hash=digest(envelope),
    )
