"""Delivery fixtures with independent authority and execution instrumentation."""

import asyncio
import json
import sqlite3

from evidence_support import paid, reconcile, track_dispatches
from merchant_support import scenario
from test_x402_gate import NOW

from agentic_payments_lab.x402_delivery import OfflineDeliveryAcceptor


def settled_shop(path, monkeypatch):
    shop = scenario(path)
    shop.dispatch_entries = track_dispatches(monkeypatch)
    paid(shop)
    reconcile(shop.buyer)
    assert_no_new_execution(shop)
    return shop


def acceptor(shop, *, store=None, limits=None):
    return OfflineDeliveryAcceptor(store or shop.buyer, shop.client, lambda: NOW + 100, limits)


def accept(shop, **kwargs):
    return asyncio.run(acceptor(shop, **kwargs).accept("task"))


def acceptance_rows(store):
    with sqlite3.connect(store.db.path) as db:
        return db.execute(
            "SELECT sequence,data,recorded_at FROM events "
            "WHERE event_type='offline_delivery_accepted' ORDER BY sequence"
        ).fetchall()


def financial_rows(store):
    with sqlite3.connect(store.db.path) as db:
        return {
            table: db.execute(f'SELECT * FROM "{table}" ORDER BY rowid').fetchall()
            for table in (
                "buyer_profile",
                "grants",
                "tasks",
                "x402_tasks",
                "x402_attempts",
                "x402_cancellations",
                "agent_bindings",
                "purchases",
            )
        }


def merchant_rows(shop):
    with sqlite3.connect(shop.merchant.store.db.path) as db:
        return list(db.iterdump())


def assert_no_new_execution(shop):
    assert len(shop.signer.calls) == 1
    assert shop.dispatch_entries == ["dispatch"]
    # These are separate authority tables, not buyer/merchant status fields.
    with sqlite3.connect(shop.authority.path) as db:
        assert db.execute("SELECT count(*),sum(amount) FROM calls").fetchone() == (1, 10_000)
        assert db.execute("SELECT count(*),sum(amount) FROM transfers").fetchone() == (1, 10_000)


def cached_report(shop):
    return shop.merchant.store.report(shop.context().purchase_id, NOW)


def write_evidence(path, shop, report):
    path.write_text(
        json.dumps(
            {
                "report": report,
                "acceptance_rows": acceptance_rows(shop.buyer),
                "signer_calls": len(shop.signer.calls),
                "dispatcher_calls": len(shop.dispatch_entries),
                "authority": shop.authority.counts(),
            },
            indent=2,
        )
        + "\n"
    )
