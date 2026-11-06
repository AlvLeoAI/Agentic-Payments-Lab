"""Single-attempt orchestration over injected offline doubles, never live clients."""

import asyncio
from collections.abc import Callable
from typing import Literal, Protocol

from ..domain import PurchaseRequest
from ..x402_contracts import GateDenied, SigningTerms
from ..x402_store import OfflineGateStore
from .protocol import OfflineSignature, transient_header, validate_required


class SignerDouble(Protocol):
    mode: Literal["offline_double"]

    async def sign(self, terms: SigningTerms) -> OfflineSignature: ...


class DispatcherDouble(Protocol):
    mode: Literal["offline_double"]

    async def dispatch(
        self,
        *,
        resource: str,
        method: str,
        request: PurchaseRequest,
        purchase_id: str,
        payment_header: str,
    ) -> object: ...


class GateUnavailable(RuntimeError):
    """Sanitized boundary error. Inspect durable state; do not repeat side effects."""


class OfflineGate:
    def __init__(
        self,
        store: OfflineGateStore,
        signer: SignerDouble,
        dispatcher: DispatcherDouble,
        clock: Callable[[], int],
        *,
        identified: bool = False,
    ):
        if signer.mode != "offline_double" or dispatcher.mode != "offline_double":
            raise GateDenied("only_offline_doubles_allowed")
        self.store, self.signer, self.dispatcher, self.clock = store, signer, dispatcher, clock
        if type(identified) is not bool:
            raise GateDenied("invalid_offline_profile")
        self.identified = identified

    async def run(
        self, task_id: str, header: str, *, resource: str, method: str, status_code: int = 402
    ) -> dict:
        requirement_hash = validate_required(
            header,
            policy=self.store.policy,
            resource=resource,
            method=method,
            status_code=status_code,
            identified=self.identified,
        )
        # All validation and the commit precede the first await/side effect.
        try:
            claim = self.store.claim(task_id, requirement_hash, self.clock())
        except GateDenied:
            raise
        except Exception:
            raise GateUnavailable("claim_persistence_failed; inspect_existing_state") from None
        if claim is None:
            return self.store.report(task_id)
        owner, terms = claim
        signature = payment_header = None
        try:
            terms.validate_policy(self.store.policy, self.clock())
            if terms.requirements_hash != requirement_hash:
                raise GateDenied("requirements_changed")
            signature = await self.signer.sign(terms)
            # A cooperative double may swallow cancellation and return late. That
            # must not restore dispatch authority to the canceled caller.
            if asyncio.current_task().cancelling():
                raise asyncio.CancelledError
            payment_header = transient_header(
                self.store.policy, terms, signature, self.clock(), identified=self.identified
            )
            if self.store.authorize_dispatch(owner, terms, self.clock()):
                with self.store.db.connect() as db:
                    request = PurchaseRequest.model_validate_json(
                        self.store._task(db, task_id)["request"]
                    )
                # No DB transaction spans either awaited side effect. Responses
                # (including successful facilitator/HTTP-shaped values) are not proof.
                await self.dispatcher.dispatch(
                    resource=self.store.policy.resource,
                    method=self.store.policy.method,
                    request=request,
                    purchase_id=terms.purchase_id,
                    payment_header=payment_header,
                )
                self.store.inconclusive(terms, self.clock())
        except asyncio.CancelledError:
            try:
                # One synchronous local attempt, SQLite lock wait bounded at 0.2s.
                # I/O stalls/process death can still defeat cleanup; the already
                # committed ownership/dispatch marker remains conservative.
                self.store.cancel(task_id, self.clock())
            except (Exception, asyncio.CancelledError):
                pass
            # Do not copy the double/caller's arguments, notes or exception chain.
            # Standard reporting is sanitized; traceback locals are not erased.
            raise asyncio.CancelledError("offline_gate_canceled") from None
        except Exception:
            try:
                self.store.inconclusive(terms, self.clock())
            except Exception:
                raise GateUnavailable("gate_persistence_failed; reconciliation_required") from None
        finally:
            # Release our references only. Python/OS/debuggers may retain copies;
            # this is explicitly not a memory-erasure guarantee.
            signature = payment_header = None
        return self.store.report(task_id)
