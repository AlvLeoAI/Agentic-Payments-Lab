"""Bridge fixtures and durable independent call counters. No retained state."""

import asyncio
import sqlite3

from merchant_support import scenario

from agentic_payments_lab.buyer_agent.contracts import Goal, Settings
from agentic_payments_lab.buyer_agent.offline_x402 import OfflineBoundary, OfflineX402Buyer
from agentic_payments_lab.buyer_agent.runner import run_buyer
from agentic_payments_lab.buyer_agent.store import AgentStore


def instrument(shop, client):
    with sqlite3.connect(shop.authority.path) as db:
        db.execute("CREATE TABLE IF NOT EXISTS bridge_calls (kind TEXT NOT NULL)")

    def entry(kind):
        with sqlite3.connect(shop.authority.path) as db:
            db.execute("INSERT INTO bridge_calls VALUES(?)", (kind,))

    sign, dispatch = shop.signer.sign, client.dispatch

    async def signing(terms):
        entry("signer")
        return await sign(terms)

    async def dispatching(**kwargs):
        entry("dispatcher")
        return await dispatch(**kwargs)

    shop.signer.sign, client.dispatch = signing, dispatching


def configured(shop, path, *, task="task", initialize=True):
    client = shop.client
    instrument(shop, client)
    buyer = OfflineX402Buyer(shop.buyer, client, shop.signer, lambda: shop.now[0], task)
    goal = Goal(subject=shop.request.subject, purpose=shop.request.purpose, quality="basic")
    metadata = AgentStore(path / "agent.sqlite3")
    if initialize:
        buyer.initialize(metadata, goal)
    return buyer, metadata, goal


def setup(path):
    shop = scenario(path)
    return (shop, *configured(shop, path))


def drive(buyer, metadata, goal, *, model=None, settings=None):
    return asyncio.run(run_buyer(buyer, metadata, goal, settings or Settings(), model=model))


def quote(buyer, metadata, goal):
    boundary = OfflineBoundary(buyer, metadata, goal)
    return asyncio.run(boundary.invoke("get_quote", '{"service_id":"test-report"}')).quote_id


def counters(shop):
    with sqlite3.connect(f"file:{shop.authority.path}?mode=ro", uri=True) as db:
        return (
            db.execute("SELECT count(*) FROM bridge_calls WHERE kind='signer'").fetchone()[0],
            db.execute("SELECT count(*) FROM bridge_calls WHERE kind='dispatcher'").fetchone()[0],
            db.execute("SELECT count(*) FROM calls").fetchone()[0],
            db.execute("SELECT count(*) FROM transfers").fetchone()[0],
            db.execute("SELECT coalesce(sum(amount),0) FROM transfers").fetchone()[0],
        )


def finance(shop):
    with sqlite3.connect(f"file:{shop.buyer.db.path}?mode=ro", uri=True) as db:
        return db.execute("SELECT reserved,spent FROM grants").fetchone()
