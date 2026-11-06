"""Subprocess helper: durable independent call markers, then uncatchable exit."""

import asyncio
import os
import sys
from pathlib import Path

from test_x402_gate import MARKER, NOW, header_for, policy_for_test

from agentic_payments_lab.x402_adapter.gate import OfflineGate
from agentic_payments_lab.x402_adapter.protocol import OfflineSignature
from agentic_payments_lab.x402_store import OfflineGateStore

state, counts, boundary = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]


def observed(name):
    with counts.open("ab", buffering=0) as file:
        file.write(name.encode() + b"\n")
        os.fsync(file.fileno())


class Signer:
    mode = "offline_double"

    async def sign(self, terms):
        observed("signer")
        marker = OfflineSignature(MARKER)
        if boundary == "signature_lost":
            os._exit(91)
        return marker


class Dispatcher:
    mode = "offline_double"

    async def dispatch(self, **kwargs):
        observed("dispatcher")
        if boundary == "dispatch_entered":
            os._exit(91)
        return {"success": True}


store = OfflineGateStore(state, policy_for_test())
if boundary == "cancel_before_claim":
    store.cancel("task", NOW)
    os._exit(91)
elif boundary == "migrate_v1":
    raise SystemExit(0)
elif boundary == "dispatch_committed":
    original = store.authorize_dispatch

    def crash(*args):
        original(*args)
        os._exit(91)

    store.authorize_dispatch = crash
elif boundary == "response_lost":
    store.inconclusive = lambda *args: os._exit(91)

gate = OfflineGate(store, Signer(), Dispatcher(), lambda: NOW)
asyncio.run(
    gate.run("task", header_for(), resource=store.policy.resource, method=store.policy.method)
)
raise SystemExit("Expected a forced exit")
