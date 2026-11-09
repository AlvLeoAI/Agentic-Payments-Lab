import json
import sqlite3
from contextlib import closing

import httpx2
from agents.strict_schema import ensure_strict_json_schema
from pilot_support import response
from pydantic import SecretStr

from agentic_payments_lab.buyer_agent.boundary import ARGUMENTS
from agentic_payments_lab.buyer_agent.contracts import Explanation
from agentic_payments_lab.buyer_agent.pilot_transport import ENDPOINT
from agentic_payments_lab.buyer_agent.scripted import ScriptedBuyer

KEY = SecretStr("synthetic-key-marker-DO-NOT-RETAIN")


def body():
    return {
        "model": "gpt-6-luna",
        "reasoning": {"effort": "none"},
        "service_tier": "default",
        "max_output_tokens": 512,
        "parallel_tool_calls": False,
        "store": False,
        "truncation": "disabled",
        "instructions": "Synthetic host constraints",
        "input": [{"role": "user", "content": "Synthetic request"}],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "final_output",
                "strict": True,
                "schema": ensure_strict_json_schema(Explanation.model_json_schema()),
            }
        },
        "tools": [
            {
                "type": "function",
                "name": n,
                "strict": True,
                "description": f"Bounded buyer operation: {n}.",
                "parameters": ensure_strict_json_schema(a.model_json_schema()),
            }
            for n, a in ARGUMENTS.items()
        ],
    }


def request(data=None, **changes):
    return httpx2.Request(
        "POST",
        changes.pop("url", ENDPOINT),
        content=json.dumps(body() if data is None else data).encode(),
        **changes,
    )


def authority(root):
    with closing(sqlite3.connect(root / "state/payments.sqlite3")) as db:
        executions = db.execute("SELECT count(*) FROM attempts").fetchone()[0]
        results = [json.loads(row[0]) for row in db.execute("SELECT result FROM authorizations")]
    charged = [row for row in results if row["status"] == "settled"]
    return executions, len(charged), sum(row["receipt"]["intent"]["amount"] for row in charged)


def scripted_wire(calls):
    scripted = ScriptedBuyer()

    async def respond(request):
        data = json.loads(request.content)
        calls.append(data)
        result = await scripted.get_response(input=data["input"])
        return httpx2.Response(200, json=response(len(calls), result.output))

    return httpx2.MockTransport(respond)
