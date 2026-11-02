"""Pure authorization checks; spending reservations live in the ledger transaction."""

from .catalog import Catalog
from .domain import Grant, PolicyDenied, PurchaseRequest, Quote, digest

QUOTE_TTL = 60


class Policy:
    def __init__(self, catalog: Catalog):
        self.catalog = catalog

    def validate(self, request: PurchaseRequest, quote: Quote, grant: Grant, now: int):
        service = self.catalog.get(request.service_id)
        if request.service_id not in grant.service_ids or request.purpose != grant.purpose:
            raise PolicyDenied("grant_scope_mismatch")
        if not grant.issued_at <= now < grant.expires_at:
            raise PolicyDenied("grant_expired_or_not_active")
        if quote.request_hash != digest(request):
            raise PolicyDenied("quote_request_mismatch")
        for field in (
            "service_id",
            "merchant_id",
            "recipient",
            "network",
            "asset",
            "amount",
            "decimals",
            "origin",
            "route",
        ):
            if getattr(quote, field) != getattr(service, field):
                raise PolicyDenied(f"quote_{field}_mismatch")
        if not quote.issued_at <= now < quote.expires_at:
            raise PolicyDenied("quote_expired_or_not_active")
        if quote.expires_at - quote.issued_at > QUOTE_TTL:
            raise PolicyDenied("quote_lifetime_exceeded")
        if quote.amount > grant.per_purchase_limit:
            raise PolicyDenied("per_purchase_limit")
