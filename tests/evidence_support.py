"""Independent normalized evidence fixtures and side-effect instrumentation."""

import asyncio
import copy
import json

from test_x402_gate import NOW

from agentic_payments_lab.x402_adapter.gate import OfflineGate
from agentic_payments_lab.x402_adapter.observer import FixtureSource, OfflineEvidenceObserver
from agentic_payments_lab.x402_provider import OfflineMerchantClient
from agentic_payments_lab.x402_reconciliation import OfflineReconciler

HEAD_HASH = "0x" + "cc" * 32
BLOCK_HASH = "0x" + "bb" * 32
TX_HASH = "0x" + "ab" * 32
OTHER_HASH = "0x" + "dd" * 32
TOKEN = "0x036CbD53842c5426634e7929541eC2318f3dCF7e"


def track_dispatches(monkeypatch):
    entries = []
    original = OfflineMerchantClient.dispatch

    async def dispatch(self, **kwargs):
        entries.append("dispatch")
        return await original(self, **kwargs)

    monkeypatch.setattr(OfflineMerchantClient, "dispatch", dispatch)
    return entries


def evidence_data(terms, *, unused=False):
    head = {"number": 100, "hash": HEAD_HASH, "timestamp": NOW + 100}
    block = {"number": 90, "hash": BLOCK_HASH, "timestamp": NOW + 1}
    used = {
        "event": "AuthorizationUsed",
        "asset": TOKEN,
        "payer": terms.payer,
        "nonce": terms.nonce,
        "transaction_hash": TX_HASH,
        "block_number": 90,
        "block_hash": BLOCK_HASH,
        "log_index": 7,
    }
    transfer = {
        "event": "Transfer",
        "asset": TOKEN,
        "payer": terms.payer,
        "recipient": terms.recipient,
        "amount": 10_000,
        "transaction_hash": TX_HASH,
        "block_number": 90,
        "block_hash": BLOCK_HASH,
        "log_index": 8,
    }
    return copy.deepcopy(
        {
            "profile": {
                "source": "offline_fixture",
                "network": "eip155:84532",
                "asset": TOKEN,
                "chain_id": 84532,
                "domain_name": "USDC",
                "domain_version": "2",
            },
            "finalized_head": head,
            "finalized_head_check": head,
            "nonce_state": {
                "asset": TOKEN,
                "payer": terms.payer,
                "nonce": terms.nonce,
                "block_number": 100,
                "block_hash": HEAD_HASH,
                "used": not unused,
            },
            "authorization_events": {
                "from_block": 0,
                "to_block": 100,
                "finalized_head_hash": HEAD_HASH,
                "complete": True,
                "events": [used],
            },
            "transaction": {
                "hash": TX_HASH,
                "to": TOKEN,
                "call": {
                    "method": "transferWithAuthorization",
                    "payer": terms.payer,
                    "recipient": terms.recipient,
                    "amount": 10_000,
                    "valid_after": 0,
                    "valid_before": NOW + 60,
                    "nonce": terms.nonce,
                },
            },
            "receipt": {
                "transaction_hash": TX_HASH,
                "block_number": 90,
                "block_hash": BLOCK_HASH,
                "success": True,
                "logs": [used, transfer],
            },
            "canonical_block": {"finalized_head_hash": HEAD_HASH, "block": block},
        }
    )


def source_for(data):
    return FixtureSource({name: json.dumps(value).encode() for name, value in data.items()})


def reconciler(store, data=None, *, unused=False, limits=None, source=None):
    if source is None:
        source = source_for(
            data or evidence_data(store.reconciliation_terms("task"), unused=unused)
        )
    return OfflineReconciler(store, OfflineEvidenceObserver(source, limits), lambda: NOW + 100)


def reconcile(store, data=None, **kwargs):
    runner = reconciler(store, data, **kwargs)
    result = asyncio.run(runner.reconcile("task"))
    return result, runner.observer.source


def paid(shop, *, lose_response=False):
    calls = []
    client = shop.client

    class Dispatch:
        mode = "offline_double"

        async def dispatch(self, **kwargs):
            calls.append("dispatch")  # No header/signature retention by instrumentation.
            result = await client.dispatch(**kwargs)
            if lose_response:
                raise RuntimeError("synthetic lost response")
            return result

    async def execute():
        context = shop.context()
        header = await client.challenge(context.purchase_id, shop.request)
        return await OfflineGate(
            shop.buyer, shop.signer, Dispatch(), lambda: NOW, identified=True
        ).run("task", header, resource=shop.buyer.policy.resource, method="POST")

    asyncio.run(execute())
    return calls


def terminal_events(store):
    with store.db.connect() as db:
        return db.execute(
            "SELECT count(*) FROM events WHERE event_type='offline_fixture_applied'"
        ).fetchone()[0]
