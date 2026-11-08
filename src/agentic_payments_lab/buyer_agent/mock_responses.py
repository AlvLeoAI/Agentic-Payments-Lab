"""Pinned Responses wire-path evaluation, exclusively through an in-memory transport.

No API key loading, live transport option or CLI activation. This is a testable
preparation path for a future separately reviewed pilot, not a live buyer backend.
"""

import asyncio
import json

import httpx2
from agents import OpenAIResponsesModel
from openai import AsyncOpenAI, DefaultAsyncHttpxClient

from ..coordinator import BuyerSession
from .boundary import ARGUMENTS
from .call_budget import BudgetDenied, CallBudget
from .contracts import Settings
from .pilot_pricing import LunaRates
from .runner import CLEANUP_TIMEOUT, run_buyer


def _unique_fields(pairs):
    data = {}
    for key, value in pairs:
        if key in data:
            raise ValueError("duplicate_response_field")
        data[key] = value
    return data


class BudgetedMockTransport(httpx2.AsyncBaseTransport):
    """Count actual serialized request bodies after SDK conversion, before dispatch.

    The injected responder is trusted test code. OS network isolation remains
    mandatory for validation, including any subprocesses spawned by that code.
    """

    def __init__(self, budget: CallBudget, invocation_id: str, responder):
        self.budget, self.invocation_id = budget, invocation_id
        self.mock = httpx2.MockTransport(responder)

    async def handle_async_request(self, request):
        if request.method != "POST" or str(request.url) != "https://responses.invalid/v1/responses":
            raise BudgetDenied("offline_responses_endpoint_required")
        body = await request.aread()
        limits = self.budget.limits
        if len(body) > limits.max_request_bytes:
            raise BudgetDenied("serialized_request_limit")
        try:
            data = json.loads(body, object_pairs_hook=_unique_fields)
            valid = (
                data["model"] == limits.model
                and data["reasoning"] == {"effort": limits.reasoning_effort}
                and data["service_tier"] == limits.service_tier
                and type(data["max_output_tokens"]) is int
                and data["max_output_tokens"] == limits.max_output_tokens
                and data["parallel_tool_calls"] is False
                and data["store"] is False
                and data["truncation"] == "disabled"
                and not data.get("stream", False)
                and data["text"]["format"]["strict"] is True
                and len(data["tools"]) == len(ARGUMENTS)
                and {tool["name"] for tool in data["tools"]} == set(ARGUMENTS)
                and all(t["type"] == "function" and t["strict"] is True for t in data["tools"])
            )
            if isinstance(limits.rates, LunaRates):
                # Reject options that can introduce unsupported pricing or hidden
                # server-side work. Cache defaults are documented 30m; reserve 12h.
                valid = valid and set(data) <= {
                    "input",
                    "instructions",
                    "model",
                    "reasoning",
                    "service_tier",
                    "max_output_tokens",
                    "parallel_tool_calls",
                    "store",
                    "truncation",
                    "stream",
                    "text",
                    "tools",
                    "include",
                    "prompt_cache_key",
                }
                valid = valid and data.get("include", []) in ([], ["reasoning.encrypted_content"])
        except (ValueError, KeyError, TypeError):
            valid = False
        if not valid:
            raise BudgetDenied("request_configuration_mismatch")
        # Includes instructions, strict schemas, all tools, model options and history.
        request_id = self.budget.reserve(self.invocation_id, body)
        try:
            response = await self.mock.handle_async_request(request)
            if asyncio.current_task().cancelling():
                raise asyncio.CancelledError
            raw = await response.aread()
            if asyncio.current_task().cancelling():
                raise asyncio.CancelledError
            if response.status_code != 200 or len(raw) > 65_536:
                raise ValueError
            payload = json.loads(raw, object_pairs_hook=_unique_fields)
            if not self.budget.observe(
                request_id, payload.get("usage"), payload.get("service_tier"), payload.get("model")
            ):
                raise BudgetDenied("request_accounting_incomplete")
            return response
        except asyncio.CancelledError:
            self._interrupt(request_id, "cancelled")
            raise asyncio.CancelledError("offline_mock_request_cancelled") from None
        except BudgetDenied:
            raise
        except Exception:
            # A commit failure may leave pending or complete state. Never resend.
            self._interrupt(request_id, "response_invalid")
            raise BudgetDenied("offline_mock_response_unavailable") from None

    def _interrupt(self, request_id, reason):
        try:
            self.budget.interrupt(request_id, reason)
        except Exception:
            pass  # The prior pending marker remains conservative if cleanup fails.


async def run_mock_buyer(session, metadata, goal, budget, invocation_id, responder):
    """One explicit, sequential mock invocation; never resume an existing claim."""
    if type(session) is not BuyerSession:
        raise ValueError("Responses mocks use the simulator; offline x402 remains scripted only")
    limits = budget.limits
    settings = Settings(
        max_turns=limits.requests_per_invocation,
        max_output_tokens=limits.max_output_tokens,
        tool_limit=limits.tool_attempts,
        timeout_seconds=limits.timeout_seconds,
        reasoning_effort=limits.reasoning_effort,
        service_tier=limits.service_tier,
    )
    budget.begin_invocation(invocation_id)
    client = None
    outcome = "stopped"
    try:
        client = AsyncOpenAI(
            api_key="offline-fixture-not-a-credential",
            base_url="https://responses.invalid/v1",
            max_retries=0,
            timeout=limits.timeout_seconds,
            http_client=DefaultAsyncHttpxClient(
                transport=BudgetedMockTransport(budget, invocation_id, responder),
                trust_env=False,
                follow_redirects=False,
                timeout=limits.timeout_seconds,
            ),
        )
        report = await run_buyer(
            session,
            metadata,
            goal,
            settings,
            model=OpenAIResponsesModel(model=limits.model, openai_client=client),
        )
        outcome = "completed" if report.termination == "completed" else "stopped"
        if report.termination == "timeout":
            outcome = "timeout"
        return report
    except asyncio.CancelledError:
        outcome = "cancelled"
        raise asyncio.CancelledError("offline_mock_invocation_cancelled") from None
    finally:
        try:
            try:
                budget.finish_invocation(invocation_id, outcome)
            except Exception:
                if outcome != "cancelled":
                    raise BudgetDenied("invocation_cleanup_incomplete") from None
        finally:
            if client:
                try:
                    async with asyncio.timeout(CLEANUP_TIMEOUT):
                        await client.close()
                except asyncio.CancelledError:
                    raise asyncio.CancelledError("offline_mock_cleanup_cancelled") from None
                except Exception:
                    pass  # No replay or financial cleanup on interruption.
