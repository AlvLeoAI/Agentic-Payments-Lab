"""Deterministic HTTP fixtures: exercise mechanics, never claim model quality."""

import json

import httpx2
from pilot_support import response

from agentic_payments_lab.buyer_agent.scripted import ScriptedBuyer


def responders(calls):
    def factory(entry):
        model = ScriptedBuyer()

        async def respond(request):
            data = json.loads(request.content)
            calls.append((entry["id"], data))
            result = await model.get_response(input=data["input"])
            return httpx2.Response(200, json=response(len(calls), result.output))

        return respond

    return factory
