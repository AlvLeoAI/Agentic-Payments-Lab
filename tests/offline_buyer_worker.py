"""Abrupt process boundaries over pre-enrolled, disposable bridge fixtures."""

import json
import os
import sys
import time
from pathlib import Path

from merchant_support import AuthorityDouble, Scenario
from offline_buyer_support import configured, drive
from test_x402_gate import NOW, SigningDouble, policy_for_test

from agentic_payments_lab.buyer_agent.offline_x402 import OfflineBoundary
from agentic_payments_lab.domain import PurchaseRequest, digest
from agentic_payments_lab.x402_adapter.protocol import required_data
from agentic_payments_lab.x402_merchant_store import MerchantStore
from agentic_payments_lab.x402_provider import OfflineMerchant
from agentic_payments_lab.x402_store import OfflineGateStore

path, boundary = Path(sys.argv[1]), sys.argv[2]
policy = policy_for_test()
authority = AuthorityDouble(path / "authority.sqlite3")
shop = Scenario(
    OfflineGateStore(path / "buyer.sqlite3", policy),
    OfflineMerchant(
        MerchantStore(
            path / "merchant.sqlite3", policy, digest(required_data(policy, identified=True))
        ),
        authority,
        lambda: NOW,
    ),
    authority,
    SigningDouble(),
    PurchaseRequest(service_id="test-report", subject="synthetic", purpose="research"),
    [NOW],
)
buyer, metadata, goal = configured(shop, path, initialize=False)

if boundary == "after_quote":
    original_quote = OfflineBoundary.quote

    async def quote(self, service_id):
        await original_quote(self, service_id)
        os._exit(95)

    OfflineBoundary.quote = quote
elif boundary == "after_claim":
    original_claim = shop.buyer.claim

    def claim(*args, **kwargs):
        assert original_claim(*args, **kwargs) is not None
        os._exit(95)

    shop.buyer.claim = claim
elif boundary == "after_dispatch":
    original_dispatch = buyer.client.dispatch

    async def dispatch(**kwargs):
        await original_dispatch(**kwargs)
        os._exit(95)

    buyer.client.dispatch = dispatch
elif boundary == "after_finish":
    original_finish = metadata.finish

    def finish(report):
        original_finish(report)
        os._exit(95)

    metadata.finish = finish
elif boundary == "concurrent":
    original_run = buyer.gate.run

    async def synchronized(*args, **kwargs):
        (path / f"ready-{os.getpid()}").touch()
        deadline = time.monotonic() + 10
        while not (path / "release-workers").exists():
            if time.monotonic() >= deadline:
                raise AssertionError("Process barrier timed out")
            time.sleep(0.01)
        return await original_run(*args, **kwargs)

    buyer.gate.run = synchronized
report = drive(buyer, metadata, goal)
if boundary == "concurrent":
    assert report.termination == "completed", report.model_dump_json()
    print(
        json.dumps(
            {"purchase_id": report.purchase.purchase_id, "phase": report.purchase.payment_state}
        )
    )
    raise SystemExit(0)
raise AssertionError("Expected forced termination")
