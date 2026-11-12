"""Native HTTP doubles, not real-model behavior or billable usage."""

import json

import httpx2

from agentic_payments_lab.buyer_agent.comparison_protocol import COUNT_ENDPOINTS, MODELS
from agentic_payments_lab.buyer_agent.scripted import ScriptedBuyer


def usage(provider):
    if provider == "openai":
        return {
            "input_tokens": 10,
            "output_tokens": 5,
            "total_tokens": 15,
            "input_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 0},
            "output_tokens_details": {"reasoning_tokens": 0},
        }
    return {
        "input_tokens": 10,
        "output_tokens": 5,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
        "service_tier": "standard",
        "inference_geo": "global",
    }


def payload(
    provider,
    *,
    tool=None,
    arguments=None,
    call_id="call-1",
    explanation="Synthetic completion.",
    stop=None,
):
    if provider == "openai":
        output = (
            [
                {
                    "type": "function_call",
                    "id": "fc-1",
                    "call_id": call_id,
                    "name": tool,
                    "arguments": json.dumps(arguments or {}),
                    "status": "completed",
                }
            ]
            if tool
            else [
                {
                    "type": "message",
                    "id": "msg-1",
                    "role": "assistant",
                    "status": "completed",
                    "content": [
                        {
                            "type": "output_text",
                            "text": json.dumps({"explanation": explanation}),
                            "annotations": [],
                            "logprobs": [],
                        }
                    ],
                }
            ]
        )
        return {
            "id": "resp-1",
            "object": "response",
            "created_at": 0,
            "model": MODELS[provider],
            "service_tier": "default",
            "status": stop or "completed",
            "parallel_tool_calls": False,
            "output": output,
            "usage": usage(provider),
            "tools": [],
            "tool_choice": "auto",
        }
    return {
        "id": "msg-1",
        "type": "message",
        "role": "assistant",
        "model": MODELS[provider],
        "stop_sequence": None,
        "stop_reason": stop or ("tool_use" if tool else "end_turn"),
        "content": (
            [{"type": "tool_use", "id": call_id, "name": tool, "input": arguments or {}}]
            if tool
            else [{"type": "text", "text": json.dumps({"explanation": explanation})}]
        ),
        "usage": usage(provider),
    }


def responders(calls, mutation=None, counts=None):
    def factory(entry):
        script = ScriptedBuyer()

        async def respond(request):
            data = json.loads(request.content)
            if str(request.url) in COUNT_ENDPOINTS.values():
                if counts is not None:
                    counts.append((entry, data, dict(request.headers)))
                return httpx2.Response(200, json=count_payload(entry["provider"]))
            calls.append((entry, data, dict(request.headers)))
            if entry["provider"] == "openai":
                inputs = data["input"]
            else:
                inputs = []
                for message in data["messages"]:
                    for block in message["content"]:
                        if block["type"] == "tool_result":
                            inputs.append(
                                {
                                    "type": "function_call_output",
                                    "call_id": block["tool_use_id"],
                                    "output": block["content"],
                                }
                            )
            response = await script.get_response(input=inputs)
            output = response.output[0]
            result = (
                payload(
                    entry["provider"],
                    tool=output.name,
                    arguments=json.loads(output.arguments),
                    call_id=output.call_id,
                )
                if output.type == "function_call"
                else payload(
                    entry["provider"], explanation=json.loads(output.content[0].text)["explanation"]
                )
            )
            if mutation:
                mutation(entry, data, result)
            return httpx2.Response(200, json=result)

        return respond

    return factory


def count_payload(provider, count=10):
    return {
        "input_tokens": count,
        **({"object": "response.input_tokens"} if provider == "openai" else {}),
    }
