"""Vendor-independent, offline-only authorization and evidence contracts.

Addresses and nonces below are public data. Nothing in this module can sign,
send HTTP, observe a chain, or authorize the existing simulator adapter.
"""

from typing import TYPE_CHECKING, Annotated, Literal, Protocol
from urllib.parse import urlsplit

from pydantic import ConfigDict, Field, ValidationError, model_validator

from .domain import Atomic, Grant, Identifier, Model, PurchaseRequest, Timestamp, digest

if TYPE_CHECKING:
    from .x402_evidence import Observation

NETWORK = "eip155:84532"
ASSET = "0x036CbD53842c5426634e7929541eC2318f3dCF7e"
Address = Annotated[str, Field(pattern=r"^0x[0-9a-fA-F]{40}$")]
Hex32 = Annotated[str, Field(pattern=r"^0x[0-9a-f]{64}$")]
Hash = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
PositiveAmount = Annotated[int, Field(strict=True, gt=0, le=9_000_000_000_000_000)]


class StrictModel(Model):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True, hide_input_in_errors=True)


class OperatorPolicy(StrictModel):
    mode: Literal["offline_x402"] = "offline_x402"
    network: Literal["eip155:84532"] = NETWORK
    asset: Literal["0x036CbD53842c5426634e7929541eC2318f3dCF7e"] = ASSET
    grant: Grant
    service_id: Identifier
    resource: Annotated[str, Field(max_length=2048)]
    method: Literal["GET", "POST"]
    payer: Address
    recipient: Address
    amount: PositiveAmount
    max_timeout_seconds: Annotated[int, Field(strict=True, ge=1, le=300)]

    @model_validator(mode="after")
    def approved_profile(self):
        url = urlsplit(self.resource)
        if (
            url.scheme != "https"
            or not url.hostname
            or url.username is not None
            or url.password is not None
            or url.fragment
            or url.query
            or any(ord(c) <= 32 or ord(c) >= 127 for c in self.resource)
            or "\\" in self.resource
        ):
            raise ValueError("Operator resource must be an exact HTTPS URL without credentials")
        if (
            self.service_id not in self.grant.service_ids
            or self.amount > self.grant.per_purchase_limit
        ):
            raise ValueError("Operator service/amount exceeds grant")
        if int(self.payer, 16) == 0 or int(self.recipient, 16) == 0:
            raise ValueError("Zero addresses are not permitted")
        return self

    def validate_request(self, request: PurchaseRequest):
        if request.service_id != self.service_id or request.purpose != self.grant.purpose:
            raise GateDenied("request_not_approved")

    def profile(self) -> dict:
        return {"mode": self.mode, "policy": self.model_dump(mode="json")}


class GateDenied(RuntimeError):
    """Fixed local reason codes only; never include merchant/signer input."""


class SigningTerms(StrictModel):
    """Exact, public EIP-3009 fields; frozen all the way down (no mutable dicts)."""

    authorization_id: Identifier
    purchase_id: Identifier
    request_hash: Hash
    requirements_hash: Hash
    policy_hash: Hash
    network: Literal["eip155:84532"] = NETWORK
    asset: Literal["0x036CbD53842c5426634e7929541eC2318f3dCF7e"] = ASSET
    payer: Address
    recipient: Address
    amount: PositiveAmount
    valid_after: Timestamp
    valid_before: Timestamp
    nonce: Hex32
    domain_name: Literal["USDC"] = "USDC"
    domain_version: Literal["2"] = "2"
    chain_id: Literal[84532] = 84532
    primary_type: Literal["TransferWithAuthorization"] = "TransferWithAuthorization"

    def authorization(self) -> dict:
        return {
            "from": self.payer,
            "to": self.recipient,
            "value": str(self.amount),
            "validAfter": str(self.valid_after),
            "validBefore": str(self.valid_before),
            "nonce": self.nonce,
        }

    def typed_data(self) -> dict:
        # This is a description for an injected double, not a cryptographic hash
        # or signature. EIP-3009 does not sign request/resource/purchase identity.
        fields = (
            ("from", "address"),
            ("to", "address"),
            ("value", "uint256"),
            ("validAfter", "uint256"),
            ("validBefore", "uint256"),
            ("nonce", "bytes32"),
        )
        return {
            "domain": {
                "name": self.domain_name,
                "version": self.domain_version,
                "chainId": self.chain_id,
                "verifyingContract": self.asset,
            },
            "primaryType": self.primary_type,
            "types": {
                "TransferWithAuthorization": [{"name": name, "type": kind} for name, kind in fields]
            },
            "message": self.authorization(),
        }

    def validate_policy(self, policy: OperatorPolicy, now: int) -> None:
        try:
            SigningTerms.model_validate(self.model_dump())
        except ValidationError:
            raise GateDenied("invalid_signing_terms") from None
        if (
            self.policy_hash != digest(policy)
            or self.payer != policy.payer
            or self.recipient != policy.recipient
            or self.amount != policy.amount
            or self.valid_after != 0
            or not policy.grant.issued_at <= now < self.valid_before <= policy.grant.expires_at
            or self.valid_before - now > policy.max_timeout_seconds
        ):
            raise GateDenied("signing_terms_not_approved_or_expired")


class FixtureEvidence(StrictModel):
    """Trusted operator test input, NOT proof obtained from a real blockchain.

    A future chain observer must independently verify transaction/log identity,
    canonical block/finality and nonce state. HTTP success cannot construct proof.
    """

    source: Literal["offline_fixture"]
    terms_hash: Hash
    nonce: Hex32
    kind: Literal["transfer", "unused"]
    finality: Literal["pending", "finalized"]
    network: Literal["eip155:84532"] = NETWORK
    asset: Literal["0x036CbD53842c5426634e7929541eC2318f3dCF7e"] = ASSET
    payer: Address
    recipient: Address
    amount: Atomic
    block_hash: Hex32
    block_number: Timestamp
    block_timestamp: Timestamp
    transaction_hash: Hex32 | None
    transaction_success: bool
    nonce_used: bool


class ReconciliationObserver(Protocol):
    """Read-only observation contract; only the closed offline fixture source exists."""

    async def observe(self, terms: SigningTerms) -> "Observation": ...
