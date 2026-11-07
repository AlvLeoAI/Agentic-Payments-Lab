"""Bounded offline observation. No HTTP/RPC client, endpoints or credentials."""

import asyncio
import json

from pydantic import ValidationError

from ..domain import digest
from ..x402_contracts import GateDenied, SigningTerms
from ..x402_evidence import (
    Block,
    CanonicalBlock,
    EventSearch,
    FixtureProfile,
    NonceState,
    Observation,
    ObservationLimits,
    Receipt,
    Transaction,
)


class FixtureSource:
    """Synthetic normalized JSON responses, copied at construction; no live backend.

    Selectors are recorded for independent tests, not interpreted as RPC requests.
    Missing records are unavailable; there is no lookup fallback or discovery I/O.
    """

    def __init__(self, replies: dict[str, bytes]):
        if type(replies) is not dict or any(
            type(k) is not str or type(v) is not bytes for k, v in replies.items()
        ):
            raise GateDenied("invalid_fixture_source")
        self._replies = dict(replies)
        self.calls: list[tuple[str, dict]] = []

    async def read(self, method: str, **selectors) -> bytes:
        self.calls.append((method, selectors))
        await asyncio.sleep(0)
        return self._replies[method]


class ObservationCanceled(asyncio.CancelledError):
    def __init__(self, observation: Observation):
        super().__init__("offline_observation_canceled")
        self.observation = observation


class _Incomplete(Exception):
    pass


def _pairs(pairs):
    data = {}
    for key, value in pairs:
        if key in data:
            raise ValueError("duplicate field")
        data[key] = value
    return data


class OfflineEvidenceObserver:
    def __init__(self, source: FixtureSource, limits: ObservationLimits | None = None):
        if type(source) is not FixtureSource:
            raise GateDenied("offline_fixture_source_required")
        self.source = source
        self.limits = ObservationLimits.model_validate((limits or ObservationLimits()).model_dump())

    async def observe(self, terms: SigningTerms) -> Observation:
        data = {"terms_hash": digest(terms), "limits": self.limits, "reads_attempted": 0}

        def result(failure="none"):
            return Observation(**data, failure=failure)

        async def read(field, method, model, **selectors):
            if data["reads_attempted"] >= self.limits.max_reads:
                raise _Incomplete("read_limit")
            data["reads_attempted"] += 1  # Count before awaiting, including failed reads.
            try:
                raw = await self.source.read(method, **selectors)
                if asyncio.current_task().cancelling():
                    raise asyncio.CancelledError
            except asyncio.CancelledError:
                raise
            except Exception:
                raise _Incomplete("unavailable") from None
            try:
                if type(raw) is not bytes or len(raw) > 16_384:
                    raise ValueError("invalid size/type")
                parsed = json.loads(raw, object_pairs_hook=_pairs)
                value = model.model_validate_json(json.dumps(parsed, allow_nan=False))
            except (ValueError, TypeError, RecursionError, ValidationError):
                raise _Incomplete("invalid_fixture") from None
            data[field] = value
            return value

        try:
            async with asyncio.timeout(self.limits.timeout_seconds):
                await read("profile", "profile", FixtureProfile)
                head = await read("head", "finalized_head", Block)
                state = await read(
                    "state",
                    "nonce_state",
                    NonceState,
                    asset=terms.asset,
                    payer=terms.payer,
                    nonce=terms.nonce,
                    block_hash=head.hash,
                )
                if state.used:
                    search = await read(
                        "search",
                        "authorization_events",
                        EventSearch,
                        asset=terms.asset,
                        payer=terms.payer,
                        nonce=terms.nonce,
                        from_block=self.limits.from_block,
                        to_block=min(self.limits.to_block, head.number),
                        finalized_head_hash=head.hash,
                    )
                    if len(search.events) == 1:
                        event = search.events[0]
                        await read(
                            "transaction", "transaction", Transaction, hash=event.transaction_hash
                        )
                        receipt = await read(
                            "receipt", "receipt", Receipt, hash=event.transaction_hash
                        )
                        await read(
                            "canonical",
                            "canonical_block",
                            CanonicalBlock,
                            number=receipt.block_number,
                            finalized_head_hash=head.hash,
                        )
                await read("head_check", "finalized_head_check", Block)
        except _Incomplete as exc:
            return result(exc.args[0])
        except TimeoutError:
            return result("timeout")
        except asyncio.CancelledError:
            raise ObservationCanceled(result("canceled")) from None
        return result()
