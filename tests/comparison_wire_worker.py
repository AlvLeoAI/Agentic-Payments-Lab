"""Abrupt exits on injected wires; inherited OS isolation, no provider requests."""

import asyncio
import os
import sys
from pathlib import Path

import httpx2
from comparison_support import responders
from preflight_support import KEYS, reply

from agentic_payments_lab.buyer_agent import comparison_live as live
from agentic_payments_lab.buyer_agent.comparison import Comparison
from agentic_payments_lab.buyer_agent.comparison_preflight import Preflight, run_mock_preflight
from agentic_payments_lab.buyer_agent.comparison_protocol import ENDPOINTS
from agentic_payments_lab.domain import digest

root, kind, point = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
authority = (
    Preflight(root / "preflight.sqlite3")
    if kind == "preflight"
    else Comparison(root / "comparison.sqlite3")
)
methods = {
    "reserve": "reserve",
    "permit": "permit",
    "headers": "wire_status",
    "observe": "observe",
    "claim": "claim_wire",
    "usage": "observe",
    "post_payment": "tool_result",
}
if point in methods:
    original = getattr(authority, methods[point])

    def stop_after(*args, **kwargs):
        result = original(*args, **kwargs)
        hit = True
        if kind == "live" and point == "claim":
            hit = str(args[-1].url) in ENDPOINTS.values()
        elif kind == "live" and point == "headers":
            hit = authority.report()["wire_attempts"][-1]["kind"] == "generation"
        elif point == "post_payment":
            hit = args[-1].get("payment_state") == "settled"
        if hit:
            os._exit(89)
        return result

    setattr(authority, methods[point], stop_after)


def mark(request):
    with (root / "independent-dispatches.txt").open("a") as handle:
        handle.write(str(request.url) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    if point == "dispatch":
        os._exit(89)


if kind == "preflight":

    def factory(provider):
        def send(request):
            mark(request)
            return reply(provider, request)

        return httpx2.MockTransport(send)

    asyncio.run(
        run_mock_preflight(
            authority,
            api_keys=KEYS,
            approved_policy_hash=authority.policy_hash,
            reviewed_source_hash=digest(authority.policy["source"]),
            transports={p: factory(p) for p in KEYS},
        )
    )
else:
    factory = responders([])

    def construct():
        entry = authority.policy["entries"][authority.report()["campaign"]["next_slot"]]
        normal = factory(entry)

        async def send(request):
            if str(request.url) in ENDPOINTS.values():
                mark(request)
            return await normal(request)

        return httpx2.MockTransport(send)

    live.network_transport = construct
    asyncio.run(
        live.run_live_comparison(
            authority, api_keys=KEYS, approved_enrollment_hash=authority.policy_hash
        )
    )
raise SystemExit("Expected crash boundary was not reached")
