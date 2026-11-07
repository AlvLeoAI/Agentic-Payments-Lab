"""Explicit operator-only acceptance via one non-charging offline ASGI retrieval."""

import asyncio
from collections.abc import Callable

from .domain import digest
from .x402_contracts import GateDenied
from .x402_delivery_contracts import DeliveryLimits, MerchantDeliveryReport
from .x402_provider import OfflineMerchantClient
from .x402_store import OfflineGateStore


class DeliveryUnavailable(RuntimeError):
    """Fixed public error. Inspect existing state; a commit may have succeeded."""


class OfflineDeliveryAcceptor:
    def __init__(
        self,
        store: OfflineGateStore,
        client: OfflineMerchantClient,
        clock: Callable[[], int],
        limits: DeliveryLimits | None = None,
    ):
        if type(client) is not OfflineMerchantClient or digest(client.merchant.policy) != digest(
            store.policy
        ):
            raise GateDenied("matching_offline_merchant_required")
        self.store, self.client, self.clock = store, client, clock
        self.limits = DeliveryLimits.model_validate_json(
            (limits or DeliveryLimits()).model_dump_json()
        )

    @staticmethod
    def _cancellation_requested():
        if asyncio.current_task().cancelling():
            raise asyncio.CancelledError

    async def accept(self, task_id: str) -> dict:
        try:
            self._cancellation_requested()
            binding = self.store.delivery_request(task_id)
            attempts = 0
            if binding is None:
                current = {"outcome": "already_accepted", "reason": None}
            else:
                report, failure = None, "unavailable_or_invalid"
                try:
                    async with asyncio.timeout(self.limits.timeout_seconds):
                        self._cancellation_requested()
                        attempts = 1
                        raw = await self.client.retrieve(binding.context.purchase_id)
                        self._cancellation_requested()  # A late result cannot defeat cancellation.
                        report = MerchantDeliveryReport.model_validate(raw, strict=True)
                except TimeoutError:
                    failure = "timeout"
                except Exception:
                    pass  # No merchant/transport exception text or raw response escapes.
                self._cancellation_requested()
                now = self.clock()
                self._cancellation_requested()
                current = self.store.record_delivery(task_id, binding, report, failure, now)
            self._cancellation_requested()
            result = self.store.report(task_id)
            self._cancellation_requested()
            return {**result, "current_invocation": {**current, "retrieval_attempts": attempts}}
        except asyncio.CancelledError:
            # No compensating action or retry. A completed acceptance stays durable.
            raise asyncio.CancelledError("offline_delivery_canceled") from None
        except GateDenied:
            raise GateDenied("offline_delivery_rejected; inspect_existing_state") from None
        except Exception:
            raise DeliveryUnavailable(
                "offline_delivery_unavailable; inspect_existing_state"
            ) from None
