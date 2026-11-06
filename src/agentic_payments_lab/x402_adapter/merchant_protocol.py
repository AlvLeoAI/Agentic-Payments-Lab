"""Strict offline merchant wire profile; SDK codecs never initiate payment retries."""

import base64
import json

from x402.http.utils import (
    decode_payment_signature_header,
    encode_payment_required_header,
    encode_payment_response_header,
)
from x402.schemas import PaymentPayload, PaymentRequired, SettleResponse

from ..domain import digest
from ..x402_contracts import GateDenied, OperatorPolicy
from ..x402_merchant_contracts import (
    MerchantAuthorization,
    MerchantSubmission,
    SettlementObservation,
    payment_identifier,
)
from .protocol import (
    MAX_DECODED_BYTES,
    MAX_HEADER_BYTES,
    OfflineSignature,
    _same_types_and_values,
    _unique_object,
    payment_extensions,
    required_data,
)


def challenge(policy: OperatorPolicy) -> str:
    return encode_payment_required_header(
        PaymentRequired.model_validate(required_data(policy, identified=True))
    )


def _atomic_string(value):
    if (
        type(value) is not str
        or not 1 <= len(value) <= 16
        or not value.isascii()
        or not value.isdecimal()
        or str(int(value)) != value
    ):
        raise ValueError("noncanonical_integer")
    return int(value)


def validate_submission(
    header: str, policy: OperatorPolicy, purchase_id: str
) -> MerchantSubmission:
    """Strict shape before SDK decoding. Only artificial signature markers accepted.

    Existing identical requests can read their state after expiry. The store checks
    the active window under the claim lock only for a first settlement attempt.
    """
    try:
        if type(header) is not str or not 0 < len(header) <= MAX_HEADER_BYTES:
            raise ValueError("header_size")
        raw = base64.b64decode(header.encode("ascii"), validate=True)
        if len(raw) > MAX_DECODED_BYTES:
            raise ValueError("decoded_size")
        data = json.loads(raw, object_pairs_hook=_unique_object)
        wire = data["payload"]["authorization"]
        authorization = MerchantAuthorization(
            payer=wire["from"],
            recipient=wire["to"],
            amount=_atomic_string(wire["value"]),
            valid_after=_atomic_string(wire["validAfter"]),
            valid_before=_atomic_string(wire["validBefore"]),
            nonce=wire["nonce"],
        )
        signature = OfflineSignature(data["payload"]["signature"])
        required = required_data(policy, identified=True)
        expected = {
            "x402Version": 2,
            "resource": required["resource"],
            "accepted": required["accepts"][0],
            "extensions": payment_extensions(purchase_id),
            "payload": {"authorization": authorization.wire_data(), "signature": signature.marker},
        }
        if (
            not _same_types_and_values(data, expected)
            or authorization.payer != policy.payer
            or authorization.recipient != policy.recipient
            or authorization.amount != policy.amount
            or authorization.valid_after != 0
            or not policy.grant.issued_at < authorization.valid_before <= policy.grant.expires_at
        ):
            raise ValueError("unapproved_payment")
        decoded = decode_payment_signature_header(header)
        if type(decoded) is not PaymentPayload or decoded.x402_version != 2:
            raise ValueError("unsupported_version")
        return MerchantSubmission(
            payment_id=payment_identifier(purchase_id),
            authorization=authorization,
            payload_hash=digest(data),
        )
    except (ValueError, TypeError, KeyError, RecursionError, GateDenied):
        raise GateDenied("invalid_offline_payment_signature") from None


def response_header(policy: OperatorPolicy, observation: SettlementObservation) -> str:
    return encode_payment_response_header(
        SettleResponse(
            success=observation.outcome == "reported_success",
            payer=policy.payer,
            transaction=observation.transaction or "",
            network=policy.network,
            amount=str(policy.amount),
            error_reason=None
            if observation.outcome == "reported_success"
            else "offline_" + observation.outcome,
        )
    )
