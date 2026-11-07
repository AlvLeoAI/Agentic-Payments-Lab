"""Explicit operator-only reconciliation over an already enrolled offline task."""

import asyncio
from collections.abc import Callable

from .x402_adapter.observer import ObservationCanceled, OfflineEvidenceObserver
from .x402_contracts import GateDenied
from .x402_store import OfflineGateStore


class ReconciliationUnavailable(RuntimeError):
    """Fixed public message; inspect the original durable state after failure."""


class OfflineReconciler:
    def __init__(
        self, store: OfflineGateStore, observer: OfflineEvidenceObserver, clock: Callable[[], int]
    ):
        if type(observer) is not OfflineEvidenceObserver:
            raise GateDenied("offline_evidence_observer_required")
        self.store, self.observer, self.clock = store, observer, clock

    async def reconcile(self, task_id: str) -> dict:
        try:
            terms = self.store.reconciliation_terms(task_id)
            observation = await self.observer.observe(terms)
            if asyncio.current_task().cancelling():
                raise asyncio.CancelledError
            current = self.store.record_observation(task_id, observation, self.clock())
            if asyncio.current_task().cancelling():
                raise asyncio.CancelledError
            report = self.store.report(task_id)
            # The durable snapshot may include intervening invocations. This
            # committed decision belongs to this call, even on a deduplicated replay.
            return {**report, "current_invocation": current}
        except ObservationCanceled as exc:
            try:
                # One synchronous, short lock-wait attempt. Abrupt exit, I/O or
                # persistence failure may defeat cleanup. No financial cleanup.
                self.store.record_observation(task_id, exc.observation, self.clock())
            except (Exception, asyncio.CancelledError):
                pass
            raise asyncio.CancelledError("offline_reconciliation_canceled") from None
        except asyncio.CancelledError:
            raise asyncio.CancelledError("offline_reconciliation_canceled") from None
        except GateDenied:
            raise
        except Exception:
            raise ReconciliationUnavailable(
                "offline_reconciliation_unavailable; inspect_existing_state"
            ) from None
