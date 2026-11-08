"""Abrupt exits around the real guarded in-memory transport, no network implementation."""

import asyncio
import json
import os
import sqlite3
import sys
from pathlib import Path

import httpx2
from pilot_support import response

from agentic_payments_lab.buyer_agent.boundary import ARGUMENTS
from agentic_payments_lab.buyer_agent.call_budget import CallBudget
from agentic_payments_lab.buyer_agent.mock_responses import BudgetedMockTransport
from agentic_payments_lab.runtime_guard import require_pilot_runtime

require_pilot_runtime()
path, boundary = Path(sys.argv[1]), sys.argv[2]
with sqlite3.connect(path / "transport.sqlite3") as db:
    db.execute("CREATE TABLE calls (id INTEGER PRIMARY KEY)")
budget = CallBudget(path / "pilot-budget.sqlite3")
budget.begin_invocation("worker")
if boundary == "invocation":
    os._exit(83)
original_reserve, original_observe = budget.reserve, budget.observe


def reserve(*args):
    result = original_reserve(*args)
    if boundary == "reservation":
        os._exit(83)
    return result


def observe(*args):
    result = original_observe(*args)
    if boundary == "usage":
        os._exit(83)
    return result


async def responder(request):
    with sqlite3.connect(path / "transport.sqlite3") as db:
        db.execute("INSERT INTO calls DEFAULT VALUES")
    if boundary == "dispatch":
        os._exit(83)
    return httpx2.Response(200, json=response(1, []))


budget.reserve, budget.observe = reserve, observe
body = {
    "model": "gpt-6-luna",
    "reasoning": {"effort": "none"},
    "service_tier": "default",
    "max_output_tokens": 512,
    "parallel_tool_calls": False,
    "store": False,
    "truncation": "disabled",
    "text": {"format": {"strict": True}},
    "tools": [{"name": name, "type": "function", "strict": True} for name in ARGUMENTS],
}
transport = BudgetedMockTransport(budget, "worker", responder)
asyncio.run(
    transport.handle_async_request(
        httpx2.Request(
            "POST", "https://responses.invalid/v1/responses", content=json.dumps(body).encode()
        )
    )
)
raise AssertionError("Crash boundary was not reached")
