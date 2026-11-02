"""Operator-owned catalog: comparable reports with explicit simulation economics."""

from __future__ import annotations

from .domain import PolicyDenied, PurchaseRequest, ResourceEnvelope, Service


class Catalog:
    def __init__(self, services: tuple[Service, ...]):
        self._services = {service.service_id: service for service in services}
        if len(self._services) != len(services):
            raise ValueError("Duplicate service ID")

    def get(self, service_id: str) -> Service:
        try:
            return self._services[service_id]
        except KeyError:
            raise PolicyDenied("unknown_service") from None

    def list(self) -> tuple[Service, ...]:
        return tuple(self._services.values())

    def describe(self) -> list[dict]:
        return [
            {
                **service.model_dump(),
                "request_schema": PurchaseRequest.model_json_schema(),
                "response_schema": ResourceEnvelope.model_json_schema(),
            }
            for service in self.list()
        ]


def default_catalog() -> Catalog:
    return Catalog(
        tuple(
            Service(
                service_id=service_id,
                merchant_id="mock-research",
                recipient="sim-merchant-research",
                name=name,
                description=f"Deterministic {quality} research report for the requested subject.",
                quality=quality,
                amount=amount,
            )
            for service_id, name, quality, amount in (
                ("report-free", "Basic report", "basic", 0),
                ("report-standard", "Standard report", "standard", 10_000),
                ("report-premium", "Premium report", "premium", 30_000),
            )
        )
    )
