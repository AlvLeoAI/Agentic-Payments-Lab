"""Offline-only x402 merchant: ASGI transport, explicit enrollment, one settle double.

There is no HTTP facilitator, signature verification, chain observer or public
retrieval service. The process/ASGI caller and injected doubles are trusted fixtures.
"""

import asyncio
import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Literal, Protocol
from urllib.parse import urlsplit

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import TypeAdapter

from .domain import Identifier, PurchaseRequest, canonical, digest
from .x402_adapter.merchant_protocol import challenge, response_header, validate_submission
from .x402_adapter.protocol import _unique_object, required_data
from .x402_contracts import GateDenied
from .x402_delivery_contracts import parse_delivery_report
from .x402_merchant_contracts import (
    MerchantAuthorization,
    MerchantRequest,
    SettlementObservation,
)
from .x402_merchant_store import MerchantConflict, MerchantStore


class SettlementDouble(Protocol):
    mode: Literal["offline_double"]

    async def settle(
        self, *, payment_header: str, authorization: MerchantAuthorization
    ) -> SettlementObservation: ...


class OfflineMerchant:
    def __init__(
        self, store: MerchantStore, settlement: SettlementDouble, clock: Callable[[], int]
    ):
        url = urlsplit(store.policy.resource)
        if (
            settlement.mode != "offline_double"
            or store.policy.method != "POST"
            or not url.hostname.endswith(".invalid")
            or not re.fullmatch(r"/[a-zA-Z0-9_/-]+", url.path)
            or url.path.startswith("/__offline/")
            or store.profile["requirements_hash"]
            != digest(required_data(store.policy, identified=True))
        ):
            raise GateDenied("only_offline_merchant_profile_allowed")
        self.store, self.settlement, self.clock = store, settlement, clock
        self.policy = store.policy
        self._access = object()  # In-process capability, not a credential or remote authentication.
        self.app = FastAPI(
            title="OFFLINE x402 fixture merchant", docs_url=None, redoc_url=None, openapi_url=None
        )
        self.app.post(url.path)(self._paid)
        self.app.get("/__offline/status/{purchase_id}")(self._status)

    @classmethod
    def initialize(cls, path, policy, settlement, clock):
        return cls(
            MerchantStore.initialize(
                Path(path), policy, digest(required_data(policy, identified=True))
            ),
            settlement,
            clock,
        )

    def _authorized(self, request: Request):
        if request.scope.get("offline_merchant_access") is not self._access:
            raise GateDenied("trusted_asgi_access_required")

    def _result(self, purchase_id: str):
        report = self.store.report(purchase_id, self.clock())
        headers = {}
        if report["settlement_observation"]:
            headers["PAYMENT-RESPONSE"] = response_header(
                self.policy, SettlementObservation.model_validate(report["settlement_observation"])
            )
        status = {"cached": 200, "gone": 410}.get(report["delivery_state"], 202)
        return JSONResponse(report, status_code=status, headers=headers)

    async def _paid(self, request: Request):
        header = None
        claimed = False
        try:
            self._authorized(request)
            if str(request.url) != self.policy.resource:
                raise GateDenied("merchant_destination_not_approved")
            if request.headers.get("content-type") != "application/json":
                raise GateDenied("merchant_json_required")
            raw = bytearray()
            async for chunk in request.stream():
                if len(raw) + len(chunk) > 4096:
                    raise GateDenied("merchant_body_too_large")
                raw.extend(chunk)
            body = MerchantRequest.model_validate(
                json.loads(raw, object_pairs_hook=_unique_object), strict=True
            )
            self.store.check_request(body)
            headers = request.headers.getlist("payment-signature")
            if not headers:
                return JSONResponse(
                    {"mode": "offline_x402_merchant", "error": "payment_required"},
                    status_code=402,
                    headers={"PAYMENT-REQUIRED": challenge(self.policy)},
                )
            if len(headers) != 1:
                raise GateDenied("duplicate_payment_signature")
            header = headers[0]
            submission = validate_submission(header, self.policy, body.purchase_id)
            owner = self.store.claim(body, submission, self.clock())
            if owner is not None:
                claimed = True
                # The claim commit precedes any await/call to the settlement double.
                # Every existing claim, even unknown/pending/failed, forbids another call.
                if asyncio.current_task().cancelling():
                    raise asyncio.CancelledError
                observation = await self.settlement.settle(
                    payment_header=header, authorization=submission.authorization
                )
                self.store.observe(submission.payment_id, owner, observation)
                if asyncio.current_task().cancelling():
                    raise asyncio.CancelledError
                if observation.outcome == "reported_success":
                    self.store.prepare_delivery(body.purchase_id, self.clock())
            return self._result(body.purchase_id)
        except asyncio.CancelledError:
            # No compensating financial write; any durable claim remains ambiguous.
            # Do not copy untrusted cancellation arguments/notes/chains.
            raise asyncio.CancelledError("offline_merchant_canceled") from None
        except MerchantConflict:
            return JSONResponse(
                {
                    "error": "merchant_unavailable_inspect_existing_state"
                    if claimed
                    else "merchant_identity_conflict"
                },
                status_code=503 if claimed else 409,
            )
        except (GateDenied, ValueError, TypeError, RecursionError):
            return JSONResponse(
                {
                    "error": "merchant_unavailable_inspect_existing_state"
                    if claimed
                    else "invalid_offline_merchant_request"
                },
                status_code=503 if claimed else 400,
            )
        except Exception:
            # Unknown outcome, including failed commit acknowledgement. No retries.
            return JSONResponse(
                {"error": "merchant_unavailable_inspect_existing_state"}, status_code=503
            )
        finally:
            header = None  # Reference release only; no Python/OS memory-erasure claim.

    async def _status(self, request: Request, purchase_id: str):
        try:
            self._authorized(request)
            TypeAdapter(Identifier).validate_python(purchase_id)
            return self._result(purchase_id)
        except (GateDenied, ValueError):
            return JSONResponse({"error": "merchant_status_unavailable"}, status_code=404)
        except Exception:
            return JSONResponse(
                {"error": "merchant_unavailable_inspect_existing_state"}, status_code=503
            )


class OfflineMerchantClient:
    """Fixed ASGI-only routes; no injected network transport or automatic paid retry."""

    mode = "offline_double"

    def __init__(self, merchant: OfflineMerchant):
        self.merchant = merchant

    async def _request(self, *, purchase_id, request=None, payment_header=None):
        TypeAdapter(Identifier).validate_python(purchase_id)
        merchant = self.merchant

        async def trusted_asgi(scope, receive, send):
            scope = {**scope, "offline_merchant_access": merchant._access}
            await merchant.app(scope, receive, send)

        url = urlsplit(merchant.policy.resource)
        target = (
            merchant.policy.resource
            if request is not None
            else (f"{url.scheme}://{url.netloc}/__offline/status/{purchase_id}")
        )
        headers = {"content-type": "application/json"}
        if payment_header is not None:
            headers["PAYMENT-SIGNATURE"] = payment_header
        body = (
            MerchantRequest(purchase_id=purchase_id, request=request)
            if request is not None
            else None
        )
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=trusted_asgi),
                trust_env=False,
                follow_redirects=False,
            ) as client:
                return await client.request(
                    "POST" if body else "GET",
                    target,
                    headers=headers,
                    content=canonical(body).encode() if body else None,
                )
        except asyncio.CancelledError:
            raise asyncio.CancelledError("offline_merchant_canceled") from None
        except Exception:
            raise GateDenied("offline_merchant_transport_failed") from None

    async def challenge(self, purchase_id: str, request: PurchaseRequest) -> str:
        response = await self._request(purchase_id=purchase_id, request=request)
        if response.status_code != 402:
            raise GateDenied("offline_merchant_challenge_unavailable")
        return response.headers["PAYMENT-REQUIRED"]

    async def dispatch(self, *, resource, method, request, purchase_id, payment_header):
        if resource != self.merchant.policy.resource or method != self.merchant.policy.method:
            raise GateDenied("merchant_destination_not_approved")
        response = await self._request(
            purchase_id=purchase_id, request=request, payment_header=payment_header
        )
        if response.status_code not in (200, 202, 410):
            raise GateDenied("offline_merchant_dispatch_failed")
        # Return no HTTP request object that could retain its signature header.
        return response.json()

    async def retrieve(self, purchase_id: str) -> dict:
        """Non-charging host-only retrieval, including after expiry or process restart."""
        response = await self._request(purchase_id=purchase_id)
        if response.status_code not in (200, 202, 410):
            raise GateDenied("offline_merchant_status_unavailable")
        try:
            # HTTPX/ASGI has already buffered content. This bounds parsing and
            # acceptance, not transport allocation or in-process merchant work.
            report = parse_delivery_report(response.content)
            if response.headers.get("content-type", "").partition(";")[
                0
            ].strip().lower() != "application/json" or response.status_code != {
                "cached": 200,
                "gone": 410,
            }.get(report.delivery_state, 202):
                raise ValueError("Invalid delivery HTTP envelope")
            return report.model_dump(mode="json")
        except (ValueError, TypeError, RecursionError):
            raise GateDenied("invalid_offline_merchant_delivery") from None
