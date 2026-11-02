"""Local merchant and an HTTP client that cannot open a network connection."""

from enum import StrEnum
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException

from .catalog import Catalog
from .clock import Clock
from .domain import (
    DeliveryRequest,
    PolicyDenied,
    PurchaseConflict,
    PurchaseRequest,
    Quote,
    Resource,
    ResourceEnvelope,
    canonical,
    digest,
)
from .payments import SimulatedPayments
from .policy import QUOTE_TTL
from .storage import Database

SCHEMA = """
CREATE TABLE IF NOT EXISTS quotes (quote_id TEXT PRIMARY KEY, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS deliveries (
    purchase_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, data TEXT NOT NULL
);
"""


class DeliveryFault(StrEnum):
    NONE = "none"
    UNAVAILABLE = "unavailable"
    CORRUPT_RESPONSE = "corrupt_response"


class MockProvider:
    def __init__(
        self,
        path: Path,
        catalog: Catalog,
        clock: Clock,
        payments: SimulatedPayments,
        fault: DeliveryFault = DeliveryFault.NONE,
    ):
        self.db = Database(path, SCHEMA)
        self.catalog, self.clock, self.payments, self.fault = catalog, clock, payments, fault
        self.app = FastAPI(title="SIMULATED paid merchant", docs_url=None, redoc_url=None)
        self.app.get("/services")(self.services)
        self.app.post("/quotes", response_model=Quote)(self.quote)
        self.app.post("/resources", response_model=ResourceEnvelope)(self.deliver)

    def services(self) -> list[dict]:
        return self.catalog.describe()

    def quote(self, request: PurchaseRequest) -> Quote:
        try:
            service = self.catalog.get(request.service_id)
        except PolicyDenied as exc:
            raise HTTPException(404, "unknown_service") from exc
        terms = {
            name: getattr(service, name)
            for name in (
                "service_id",
                "merchant_id",
                "recipient",
                "network",
                "asset",
                "amount",
                "decimals",
                "origin",
                "route",
            )
        }
        terms.update(
            request_hash=digest(request),
            issued_at=self.clock.now(),
            expires_at=self.clock.now() + QUOTE_TTL,
        )
        quote = Quote(quote_id="quote-" + digest(terms), **terms)
        with self.db.transaction() as db:
            db.execute(
                "INSERT OR IGNORE INTO quotes VALUES(?,?)", (quote.quote_id, canonical(quote))
            )
        return quote

    def deliver(self, delivery: DeliveryRequest) -> ResourceEnvelope:
        quote, request, receipt = delivery.quote, delivery.request, delivery.receipt
        with self.db.connect() as db:
            known = db.execute(
                "SELECT data FROM quotes WHERE quote_id=?", (quote.quote_id,)
            ).fetchone()
        if not known or known["data"] != canonical(quote) or quote.request_hash != digest(request):
            raise HTTPException(400, "invalid_quote")
        if quote.amount:
            if receipt is None:
                raise HTTPException(402, "simulated_payment_required")
            intent = receipt.intent
            matches = (
                intent.purchase_id == delivery.purchase_id
                and intent.request_hash == digest(request)
                and intent.quote_hash == digest(quote)
                and all(
                    getattr(intent, name) == getattr(quote, name)
                    for name in ("merchant_id", "recipient", "network", "asset", "amount")
                )
            )
            try:
                verified = matches and self.payments.verifies(receipt)
            except PurchaseConflict:
                verified = False
            if not verified:
                raise HTTPException(402, "invalid_simulated_receipt")
        elif receipt is not None:
            raise HTTPException(400, "unexpected_receipt")
        fingerprint = digest(delivery)
        with self.db.transaction() as db:
            cached = db.execute(
                "SELECT * FROM deliveries WHERE purchase_id=?", (delivery.purchase_id,)
            ).fetchone()
            if cached:
                if cached["fingerprint"] != fingerprint:
                    raise HTTPException(409, "delivery_identity_conflict")
                return ResourceEnvelope.model_validate_json(cached["data"])
            if self.fault == DeliveryFault.UNAVAILABLE:
                raise HTTPException(503, "simulated_delivery_unavailable")
            service = self.catalog.get(request.service_id)
            resource = Resource(
                service_id=service.service_id,
                subject=request.subject,
                request_hash=digest(request),
                quality=service.quality,
                summary=f"Simulated {service.quality} report for {request.subject}.",
            )
            envelope = ResourceEnvelope(
                purchase_id=delivery.purchase_id,
                resource=resource,
                resource_hash=digest(resource),
                receipt_id=receipt.receipt_id if receipt else None,
            )
            db.execute(
                "INSERT INTO deliveries VALUES(?,?,?)",
                (delivery.purchase_id, fingerprint, canonical(envelope)),
            )
        if self.fault == DeliveryFault.CORRUPT_RESPONSE:
            return envelope.model_copy(update={"resource_hash": "corrupt"})
        return envelope


class MerchantClient:
    """Routes are fixed and metadata URLs are never used for transport."""

    def __init__(self, app: FastAPI):
        self.app = app

    async def request(self, method: str, route: str, data: dict | None = None) -> httpx.Response:
        if (method, route) not in {
            ("GET", "/services"),
            ("POST", "/quotes"),
            ("POST", "/resources"),
        }:
            raise ValueError("Route is outside the local merchant interface")
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app),
            base_url="http://mock-provider.invalid",
            trust_env=False,
            follow_redirects=False,
        ) as client:
            return await client.request(method, route, json=data)

    async def quote(self, request: PurchaseRequest) -> Quote:
        response = await self.request("POST", "/quotes", request.model_dump(mode="json"))
        response.raise_for_status()
        return Quote.model_validate(response.json())

    async def deliver(self, request: DeliveryRequest) -> ResourceEnvelope:
        response = await self.request("POST", "/resources", request.model_dump(mode="json"))
        response.raise_for_status()
        return ResourceEnvelope.model_validate(response.json())
