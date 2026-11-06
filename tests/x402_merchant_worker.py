"""Isolated subprocess crash/replay driver. Only synthetic signature markers."""

import asyncio
import os
import sqlite3
import sys
import time
from pathlib import Path

from merchant_support import AuthorityDouble, Scenario
from test_x402_gate import MARKER, NOW, SigningDouble, policy_for_test

from agentic_payments_lab.domain import PurchaseRequest, digest
from agentic_payments_lab.x402_adapter.protocol import (
    OfflineSignature,
    required_data,
    transient_header,
)
from agentic_payments_lab.x402_contracts import SigningTerms
from agentic_payments_lab.x402_merchant_store import MerchantStore
from agentic_payments_lab.x402_provider import OfflineMerchant
from agentic_payments_lab.x402_store import OfflineGateStore

root, operation = Path(sys.argv[1]), sys.argv[2]
policy = policy_for_test()
buyer = OfflineGateStore(root / "buyer.sqlite3", policy)
authority = AuthorityDouble(root / "authority.sqlite3")
store = MerchantStore(
    root / "merchant.sqlite3", policy, digest(required_data(policy, identified=True))
)
merchant = OfflineMerchant(store, authority, lambda: NOW)
shop = Scenario(
    buyer,
    merchant,
    authority,
    SigningDouble(),
    PurchaseRequest(service_id="test-report", subject="synthetic", purpose="research"),
    [NOW],
)

if operation == "race":
    with buyer.db.connect() as db:
        terms = SigningTerms.model_validate_json(
            db.execute("SELECT terms FROM x402_attempts").fetchone()[0]
        )
    # Explicit adversarial wire replay fixture; production never reconstructs lost signatures.
    header = transient_header(policy, terms, OfflineSignature(MARKER), NOW, identified=True)
    (root / ("ready-" + sys.argv[3])).touch()
    deadline = time.monotonic() + 15
    while not (root / "go").exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    if not (root / "go").exists():
        raise SystemExit(2)
    reply = shop.send(terms.purchase_id, header)
    raise SystemExit(0 if reply.status_code in (200, 202) else 3)


def interrupt(method, when):
    original = getattr(store, method)

    def wrapper(*args, **kwargs):
        if when == "before":
            os._exit(73)
        result = original(*args, **kwargs)
        os._exit(73)
        return result  # Unreachable; crash occurs after the original commit.

    setattr(store, method, wrapper)


boundaries = {"claim": "claim", "observe": "observe", "cache": "prepare_delivery"}
if operation.startswith(("before_", "after_")) and operation.split("_", 1)[1] in boundaries:
    when, part = operation.split("_", 1)
    interrupt(boundaries[part], when)
else:
    original_settle = authority.settle

    async def settle(**kwargs):
        if operation == "settle_entry":
            auth = kwargs["authorization"]
            with sqlite3.connect(authority.path) as db:
                db.execute("INSERT INTO calls VALUES(?,?)", (auth.nonce, auth.amount))
            os._exit(73)
        await original_settle(**kwargs)
        os._exit(73)

    authority.settle = settle

asyncio.run(shop.run())
raise SystemExit("crash hook was not reached")
