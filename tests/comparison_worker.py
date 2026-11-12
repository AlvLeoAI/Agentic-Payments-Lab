"""Isolated subprocess probes; exact exits never run cleanup or retry."""

import asyncio
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

from comparison_support import count_payload, responders

from agentic_payments_lab.buyer_agent.comparison import Comparison
from agentic_payments_lab.buyer_agent.comparison_protocol import (
    COUNT_ENDPOINTS,
    count_request_body,
    request_body,
)
from agentic_payments_lab.buyer_agent.comparison_runner import run_mock_comparison
from agentic_payments_lab.domain import canonical

root, mode = Path(sys.argv[1]), sys.argv[2]
comparison = Comparison(root / "comparison.sqlite3")
if mode == "compete":
    (root / ("ready-" + sys.argv[3])).touch()
    deadline = time.monotonic() + 25
    while not (root / "go").exists():
        if time.monotonic() > deadline:
            raise SystemExit(2)
        time.sleep(0.01)
    try:
        comparison.activate(policy_hash=comparison.policy_hash)
        claimed = True
    except (RuntimeError, sqlite3.Error):
        claimed = False
    print(json.dumps({"claimed": claimed}))
elif mode in ("activation", "count_reservation", "count_observed", "reservation"):
    comparison.activate(policy_hash=comparison.policy_hash)
    if mode != "activation":
        entry = comparison.policy["entries"][0]
        comparison.begin(entry)
        transcript = [{"kind": "user", "text": "synthetic"}]
        generation = request_body("openai", transcript, comparison.limits)
        count_id = comparison.begin_count(
            entry, generation, canonical(count_request_body("openai", generation)).encode()
        )
        if mode != "count_reservation":
            comparison.finish_count(entry, count_id, count_payload("openai"))
        if mode == "reservation":
            comparison.reserve(entry, canonical(generation).encode(), transcript, count_id)
    os._exit(87)
else:
    assert mode in ("dispatch", "usage", "tool_intent", "post_payment", "result")
    method = {
        "usage": "observe",
        "tool_intent": "tool_intent",
        "post_payment": "tool_result",
        "result": "finish",
    }.get(mode)
    if method:
        original = getattr(comparison, method)

        def crash(*args, **kwargs):
            result = original(*args, **kwargs)
            if mode != "post_payment" or args[-1].get("payment_state") == "settled":
                os._exit(87)
            return result

        setattr(comparison, method, crash)
    factory = responders([])

    def wrapped(entry):
        normal = factory(entry)

        async def respond(request):
            if mode == "dispatch" and str(request.url) not in COUNT_ENDPOINTS.values():
                os._exit(87)
            return await normal(request)

        return respond

    asyncio.run(run_mock_comparison(comparison, wrapped))
