import asyncio
import json
import os
import sys
import time
from pathlib import Path

import httpx2
from activation_support import KEY, request
from pilot_support import response

from agentic_payments_lab.buyer_agent import live_pilot
from agentic_payments_lab.buyer_agent.pilot_envelope import PilotEnvelope
from agentic_payments_lab.buyer_agent.pilot_transport import PilotTransport
from agentic_payments_lab.buyer_agent.scripted import tool_response

root, boundary = Path(sys.argv[1]), sys.argv[2]
budget = PilotEnvelope(root / "envelope.sqlite3")
if boundary == "claim":
    (root / f"ready-{sys.argv[3]}").touch()
    deadline = time.monotonic() + 30
    while not (root / "go").exists():
        if time.monotonic() > deadline:
            raise TimeoutError("cohort release")
        time.sleep(0.02)
    try:
        budget.begin_invocation("purchase")
        claimed = True
    except Exception:
        claimed = False
    print(json.dumps({"claimed": claimed}))
    raise SystemExit(0)


if boundary == "post_payment":
    calls = []

    def respond(req):
        calls.append(json.loads(req.content))
        if len(calls) == 3:
            os._exit(87)
        if len(calls) == 1:
            output = tool_response("get_quote", {"service_id": "report-standard"}, "q").output
        else:
            output = [i for i in calls[-1]["input"] if i.get("type") == "function_call_output"][-1]
            output = tool_response(
                "purchase", {"quote_id": json.loads(output["output"])["quote_id"]}, "p"
            ).output
        return httpx2.Response(200, json=response(len(calls), output))

    live_pilot.network_transport = lambda: httpx2.MockTransport(respond)
    asyncio.run(
        live_pilot.run_live_scenario(
            root,
            "purchase",
            api_key=KEY,
            approved_enrollment_hash=budget.report()["enrollment_hash"],
        )
    )
    raise AssertionError("post-payment exit not reached")

budget.begin_invocation("purchase")
if boundary == "invocation":
    os._exit(87)
original_reserve, original_observe = budget.reserve, budget.observe


def reserve(*args):
    result = original_reserve(*args)
    if boundary == "reservation":
        os._exit(87)
    return result


def observe(*args):
    result = original_observe(*args)
    if boundary == "usage":
        os._exit(87)
    return result


def respond(req):
    if boundary == "dispatch":
        os._exit(87)
    return httpx2.Response(200, json=response(1, []))


budget.reserve, budget.observe = reserve, observe
asyncio.run(
    PilotTransport(budget, "purchase", KEY, httpx2.MockTransport(respond)).handle_async_request(
        request()
    )
)
raise AssertionError("exit boundary not reached")
