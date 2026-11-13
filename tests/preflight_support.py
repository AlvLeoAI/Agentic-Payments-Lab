"""Synthetic keys and in-memory replies only; never valid account credentials."""

import httpx2
from pydantic import SecretStr

from agentic_payments_lab.buyer_agent.comparison_protocol import COUNT_ENDPOINTS, MODELS
from agentic_payments_lab.buyer_agent.comparison_wire import MODEL_ENDPOINTS

KEYS = {p: SecretStr("synthetic-" + p + "-only-marker-000000") for p in MODELS}


def reply(provider, request):
    if str(request.url) == MODEL_ENDPOINTS[provider]:
        data = (
            {"id": MODELS[provider], "object": "model", "created": 0, "owned_by": "openai"}
            if provider == "openai"
            else {
                "id": MODELS[provider],
                "type": "model",
                "display_name": "Synthetic",
                "created_at": "2026-10-07T00:00:00Z",
            }
        )
    else:
        assert str(request.url) == COUNT_ENDPOINTS[provider]
        data = {"input_tokens": 900}
        if provider == "openai":
            data["object"] = "response.input_tokens"
    return httpx2.Response(200, json=data)


def mock_factory(calls, *, mutate=None):
    def factory(provider):
        async def send(request):
            calls.append((provider, request.method, str(request.url), request.content))
            response = reply(provider, request)
            return mutate(provider, request, response) if mutate else response

        return httpx2.MockTransport(send)

    return factory
