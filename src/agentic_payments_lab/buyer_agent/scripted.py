"""Deterministic SDK Model, not evidence about a real model's behavior."""

import json

from agents import Model, ModelResponse
from agents.usage import Usage
from openai.types.responses import (
    ResponseFunctionToolCall,
    ResponseOutputMessage,
    ResponseOutputText,
)


def tool_response(name: str, arguments: dict | str, call_id: str) -> ModelResponse:
    return ModelResponse(
        output=[
            ResponseFunctionToolCall(
                type="function_call",
                name=name,
                arguments=arguments if isinstance(arguments, str) else json.dumps(arguments),
                call_id=call_id,
            )
        ],
        usage=Usage(),
        response_id=None,
    )


def final_response(text: str) -> ModelResponse:
    return ModelResponse(
        output=[
            ResponseOutputMessage(
                id="scripted-message",
                type="message",
                role="assistant",
                status="completed",
                content=[
                    ResponseOutputText(
                        type="output_text", text=json.dumps({"explanation": text}), annotations=[]
                    )
                ],
            )
        ],
        usage=Usage(),
        response_id=None,
    )


class ScriptedBuyer(Model):
    """Exercises the real runner and its four tools using Responses-format items."""

    def __init__(self):
        self.step = 0

    async def get_response(self, **kwargs) -> ModelResponse:
        self.step += 1
        outputs = [
            item
            for item in kwargs["input"]
            if isinstance(item, dict) and item.get("type") == "function_call_output"
        ]
        last = json.loads(outputs[-1]["output"]) if outputs else None
        if self.step == 1:
            name, args = "get_purchase_status", {}
        elif self.step == 2:
            if last["payment_state"] != "not_started":
                return final_response(
                    "Existing purchase retained. Use explicit operator reconciliation if required."
                )
            name, args = "list_services", {}
        elif self.step == 3:
            if not last["recommended_service_id"]:
                return final_response(
                    "No eligible service meets the required quality and grant constraints; "
                    "no purchase requested."
                )
            name, args = "get_quote", {"service_id": last["recommended_service_id"]}
        elif self.step == 4 and "quote_id" in last:
            name, args = "purchase", {"quote_id": last["quote_id"]}
        else:
            return final_response(
                "Consult the authoritative application state. "
                "Uncertain payment or failed delivery requires the operator."
            )
        return tool_response(name, args, f"scripted-{self.step}")

    async def stream_response(self, *args, **kwargs):
        raise NotImplementedError("Streaming is not enabled")
        yield  # Makes this an async iterator implementing the SDK interface.
