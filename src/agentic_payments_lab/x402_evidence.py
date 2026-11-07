"""Closed, synthetic public evidence. No RPC encoding, signatures or live proofs.

The fixture provider is a trusted test authority. These checks establish internal
correspondence, not cryptographic authenticity, real finality or token behavior.
"""

from typing import Annotated, Literal

from pydantic import Field, model_validator

from .domain import Atomic, Timestamp, digest
from .x402_contracts import (
    ASSET,
    Address,
    FixtureEvidence,
    Hash,
    Hex32,
    SigningTerms,
    StrictModel,
)


class ObservationLimits(StrictModel):
    from_block: Timestamp = 0
    to_block: Timestamp = 999
    max_reads: Annotated[int, Field(strict=True, ge=1, le=8)] = 8
    timeout_seconds: Annotated[int, Field(strict=True, ge=1, le=30)] = 5

    @model_validator(mode="after")
    def bounded_range(self):
        if not 0 <= self.to_block - self.from_block < 1000:
            raise ValueError("Fixture range must contain between 1 and 1000 blocks")
        return self


class FixtureProfile(StrictModel):
    source: Literal["offline_fixture"]
    network: Literal["eip155:84532"]
    asset: Literal["0x036CbD53842c5426634e7929541eC2318f3dCF7e"]
    chain_id: Annotated[int, Field(strict=True, ge=84532, le=84532)]
    domain_name: Literal["USDC"]
    domain_version: Literal["2"]


class Block(StrictModel):
    number: Timestamp
    hash: Hex32
    timestamp: Timestamp


def conflicting_block_identity(left: Block, right: Block) -> bool:
    """A shared hash or height must identify the same complete block record."""
    return (left.hash == right.hash or left.number == right.number) and left != right


class CanonicalBlock(StrictModel):
    finalized_head_hash: Hex32
    block: Block


class NonceState(StrictModel):
    asset: Address
    payer: Address
    nonce: Hex32
    block_number: Timestamp
    block_hash: Hex32
    used: bool


class AuthorizationEvent(StrictModel):
    event: Literal["AuthorizationUsed", "AuthorizationCanceled"]
    asset: Address
    payer: Address
    nonce: Hex32
    transaction_hash: Hex32
    block_number: Timestamp
    block_hash: Hex32
    log_index: Timestamp


class TransferEvent(StrictModel):
    event: Literal["Transfer"]
    asset: Address
    payer: Address
    recipient: Address
    amount: Atomic
    transaction_hash: Hex32
    block_number: Timestamp
    block_hash: Hex32
    log_index: Timestamp


class EventSearch(StrictModel):
    from_block: Timestamp
    to_block: Timestamp
    finalized_head_hash: Hex32
    complete: bool
    events: Annotated[tuple[AuthorizationEvent, ...], Field(max_length=16)]


class PublicCall(StrictModel):
    method: Literal["transferWithAuthorization"]
    payer: Address
    recipient: Address
    amount: Atomic
    valid_after: Timestamp
    valid_before: Timestamp
    nonce: Hex32


class Transaction(StrictModel):
    hash: Hex32
    to: Address
    call: PublicCall  # Normalized public arguments only; never signature/calldata bytes.


class Receipt(StrictModel):
    transaction_hash: Hex32
    block_number: Timestamp
    block_hash: Hex32
    success: bool
    logs: Annotated[
        tuple[Annotated[AuthorizationEvent | TransferEvent, Field(discriminator="event")], ...],
        Field(max_length=32),
    ]


Failure = Literal["none", "read_limit", "unavailable", "invalid_fixture", "timeout", "canceled"]


class Observation(StrictModel):
    source: Literal["offline_fixture"] = "offline_fixture"
    terms_hash: Hash
    limits: ObservationLimits
    reads_attempted: Annotated[int, Field(strict=True, ge=0, le=8)]
    failure: Failure = "none"
    profile: FixtureProfile | None = None
    head: Block | None = None
    state: NonceState | None = None
    search: EventSearch | None = None
    transaction: Transaction | None = None
    receipt: Receipt | None = None
    canonical: CanonicalBlock | None = None
    head_check: Block | None = None

    @model_validator(mode="after")
    def bounded_reads(self):
        if self.reads_attempted > self.limits.max_reads:
            raise ValueError("Read ceiling exceeded")
        return self


def checkpoint_matches(terms: SigningTerms, observation: Observation, now: int) -> bool:
    """A stable synthetic finalized checkpoint, with nonce state pinned to it."""
    head, state = observation.head, observation.state
    return bool(
        observation.failure == "none"
        and observation.terms_hash == digest(terms)
        and observation.profile
        and head
        and head == observation.head_check
        and head.timestamp <= now
        and state
        and state.asset == terms.asset
        and state.payer == terms.payer
        and state.nonce == terms.nonce
        and state.block_number == head.number
        and state.block_hash == head.hash
    )


def assess(
    terms: SigningTerms, observation: Observation, now: int
) -> tuple[FixtureEvidence | None, str]:
    """Derive a conclusion; no caller-provided success/finality flag is sufficient."""
    if observation.failure != "none":
        return None, observation.failure
    if not checkpoint_matches(terms, observation, now):
        return None, "checkpoint_unproven"
    head, state = observation.head, observation.state
    if not state.used:
        if head.timestamp <= terms.valid_before:
            return None, "nonuse_before_expiry"
        # Extra transfer-shaped records cannot accompany an unused conclusion.
        if any(
            (
                observation.search,
                observation.transaction,
                observation.receipt,
                observation.canonical,
            )
        ):
            return None, "contradictory_records"
        kind, block, tx_hash = "unused", head, None
    else:
        search, tx, receipt, canonical = (
            observation.search,
            observation.transaction,
            observation.receipt,
            observation.canonical,
        )
        if not all((search, tx, receipt, canonical)):
            return None, "transfer_records_missing"
        if (
            not search.complete
            or search.from_block != observation.limits.from_block
            or search.to_block != min(observation.limits.to_block, head.number)
            or search.finalized_head_hash != head.hash
            or len(search.events) != 1
        ):
            return None, "discovery_inconclusive"
        used = search.events[0]
        block = canonical.block
        expected_call = PublicCall(
            method="transferWithAuthorization",
            payer=terms.payer,
            recipient=terms.recipient,
            amount=terms.amount,
            valid_after=terms.valid_after,
            valid_before=terms.valid_before,
            nonce=terms.nonce,
        )
        transfers = [log for log in receipt.logs if type(log) is TransferEvent]
        authorizations = [log for log in receipt.logs if type(log) is AuthorizationEvent]
        if (
            used.event != "AuthorizationUsed"
            or used.asset != ASSET
            or used.payer != terms.payer
            or used.nonce != terms.nonce
            or not search.from_block <= used.block_number <= search.to_block
            or tx.hash != used.transaction_hash
            or tx.to != terms.asset
            or tx.call != expected_call
            or receipt.transaction_hash != tx.hash
            or not receipt.success
            or canonical.finalized_head_hash != head.hash
            or receipt.block_number != block.number
            or receipt.block_hash != block.hash
            or block.number != used.block_number
            or block.hash != used.block_hash
            or block.number > head.number
            or block.timestamp > head.timestamp
            or conflicting_block_identity(block, head)
            or not terms.valid_after < block.timestamp < terms.valid_before
            or authorizations != [used]
            or len(transfers) != 1
            or len({log.log_index for log in receipt.logs}) != len(receipt.logs)
        ):
            return None, "transfer_mismatched"
        transfer = transfers[0]
        if (
            transfer.asset != terms.asset
            or transfer.payer != terms.payer
            or transfer.recipient != terms.recipient
            or transfer.amount != terms.amount
            or transfer.transaction_hash != tx.hash
            or transfer.block_hash != block.hash
            or transfer.block_number != block.number
        ):
            return None, "transfer_mismatched"
        kind, tx_hash = "transfer", tx.hash
    return FixtureEvidence(
        source="offline_fixture",
        terms_hash=digest(terms),
        nonce=terms.nonce,
        kind=kind,
        finality="finalized",
        payer=terms.payer,
        recipient=terms.recipient,
        amount=terms.amount,
        block_hash=block.hash,
        block_number=block.number,
        block_timestamp=block.timestamp,
        transaction_hash=tx_hash,
        transaction_success=kind == "transfer",
        nonce_used=state.used,
    ), "validated_offline_records"


def contradicts(
    terms: SigningTerms,
    prior: FixtureEvidence,
    observed: Observation,
    now: int,
    conclusion: FixtureEvidence | None,
) -> bool:
    """Only coherent fixture counter-evidence raises an incident, not malformed input."""
    if not checkpoint_matches(terms, observed, now):
        return False
    if conclusion:
        if conclusion.kind != prior.kind:
            return True
        if prior.kind == "transfer" and (
            conclusion.transaction_hash,
            conclusion.block_hash,
            conclusion.block_number,
            conclusion.block_timestamp,
        ) != (prior.transaction_hash, prior.block_hash, prior.block_number, prior.block_timestamp):
            return True
    prior_block = Block(
        number=prior.block_number, hash=prior.block_hash, timestamp=prior.block_timestamp
    )
    if conflicting_block_identity(observed.head, prior_block):
        return True
    if observed.head.number < prior.block_number:
        return False  # A distinct older block is not a finality contradiction.
    if prior.kind == "transfer" and not observed.state.used:
        return True
    # A later nonce-use flag alone can represent cancellation after expiry; it
    # does not contradict earlier finalized non-use. Require transfer evidence.
    block = observed.canonical
    return bool(
        block
        and block.finalized_head_hash == observed.head.hash
        and block.block.timestamp <= observed.head.timestamp
        and conflicting_block_identity(block.block, prior_block)
    )
