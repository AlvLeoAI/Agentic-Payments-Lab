"""A deliberately closed v2 exact/EIP-3009 HTTP profile above the SDK codec."""

import base64
import binascii
import json
from dataclasses import dataclass, field

from x402.http.utils import decode_payment_required_header, encode_payment_signature_header
from x402.schemas import PaymentPayload, PaymentRequired

from ..domain import digest
from ..x402_contracts import GateDenied, OperatorPolicy, SigningTerms

MAX_HEADER_BYTES = 8192
MAX_DECODED_BYTES = 6144


def required_data(policy: OperatorPolicy) -> dict:
    return {
        "x402Version": 2,
        "resource": {"url": policy.resource},
        "accepts": [
            {
                "scheme": "exact",
                "network": policy.network,
                "asset": policy.asset,
                "amount": str(policy.amount),
                "payTo": policy.recipient,
                "maxTimeoutSeconds": policy.max_timeout_seconds,
                "extra": {"name": "USDC", "version": "2", "assetTransferMethod": "eip3009"},
            }
        ],
    }


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_key")
        result[key] = value
    return result


def _same_types_and_values(actual, expected):
    if type(actual) is not type(expected):
        return False
    if isinstance(expected, dict):
        return actual.keys() == expected.keys() and all(
            _same_types_and_values(actual[key], value) for key, value in expected.items()
        )
    if isinstance(expected, list):
        return len(actual) == len(expected) and all(
            _same_types_and_values(a, b) for a, b in zip(actual, expected, strict=True)
        )
    return actual == expected


def validate_required(
    header: str, *, policy: OperatorPolicy, resource: str, method: str, status_code: int = 402
) -> str:
    """No selection fallback or coercion. Return only an approved requirements hash."""
    if type(status_code) is not int or status_code != 402:
        raise GateDenied("expected_http_402")
    if method != policy.method or resource != policy.resource:
        raise GateDenied("resource_or_method_not_approved")
    try:
        if type(header) is not str or not 0 < len(header) <= MAX_HEADER_BYTES:
            raise ValueError("header_size")
        raw = base64.b64decode(header.encode("ascii"), validate=True)
        if len(raw) > MAX_DECODED_BYTES:
            raise ValueError("decoded_size")
        data = json.loads(raw, object_pairs_hook=_unique_object)
        expected = required_data(policy)
        if not _same_types_and_values(data, expected):
            raise ValueError("unapproved_terms")
        # SDK models alone allow coercion/defaults/unknown fields. Validate locally
        # first, then exercise its public v2 decoder/schema for compatibility.
        decoded = decode_payment_required_header(header)
        if not isinstance(decoded, PaymentRequired) or decoded.x402_version != 2:
            raise ValueError("unsupported_version")
    except (ValueError, TypeError, RecursionError, UnicodeError, binascii.Error):
        raise GateDenied("malformed_or_unapproved_payment_required") from None
    return digest(expected)


@dataclass(frozen=True, repr=False)
class OfflineSignature:
    """A test marker, deliberately not a valid hex/EVM signature."""

    marker: str = field(repr=False)

    def __post_init__(self):
        prefix = "offline-test-signature:"
        if (
            type(self.marker) is not str
            or not self.marker.startswith(prefix)
            or not len(prefix) < len(self.marker) <= 128
            or not self.marker.isascii()
            or not self.marker.isprintable()
        ):
            raise GateDenied("only_offline_signature_markers_allowed")


def transient_header(
    policy: OperatorPolicy, terms: SigningTerms, signature: OfflineSignature, now: int
) -> str:
    terms.validate_policy(policy, now)
    if type(signature) is not OfflineSignature:
        raise GateDenied("only_offline_signature_markers_allowed")
    required = PaymentRequired.model_validate(required_data(policy))
    if terms.requirements_hash != digest(required_data(policy)):
        raise GateDenied("requirements_changed")
    return encode_payment_signature_header(
        PaymentPayload(
            x402_version=2,
            accepted=required.accepts[0],
            resource=required.resource,
            payload={"authorization": terms.authorization(), "signature": signature.marker},
        )
    )
