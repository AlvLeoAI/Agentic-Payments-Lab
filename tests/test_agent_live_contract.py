"""Responses wire-contract tests with a local mock; these make no API calls."""

import asyncio
import json

import httpx2
import pytest
from conftest import authorize
from pydantic import ValidationError

from agentic_payments_lab import cli
from agentic_payments_lab.buyer_agent import runner
from agentic_payments_lab.buyer_agent.contracts import AgentReport, Goal, Settings
from agentic_payments_lab.buyer_agent.scripted import ScriptedBuyer
from agentic_payments_lab.buyer_agent.store import AgentStore


def test_live_requires_explicit_activation_model_and_credentials(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    for settings in (
        Settings(),
        Settings(mode="live"),
        Settings(mode="live", model="explicit-model"),
    ):
        with pytest.raises(ValueError):
            runner.live_client(settings)


def test_live_transport_is_bounded_and_ignores_proxy_and_base_url(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-only-not-a-real-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://untrusted.invalid")
    monkeypatch.setenv("HTTPS_PROXY", "http://untrusted.invalid")
    client = runner.live_client(Settings(mode="live", model="explicit-model", timeout_seconds=7))
    assert str(client.base_url) == "https://api.openai.com/v1/"
    assert client.max_retries == 0
    assert client.timeout == 7
    assert not client._client.trust_env
    assert not client._client.follow_redirects
    asyncio.run(client.close())


def mock_transport(monkeypatch, respond):
    # Keep the production client, endpoint, timeout and retry configuration.
    factory = runner.DefaultAsyncHttpxClient
    monkeypatch.setenv("OPENAI_API_KEY", "test-only-not-a-real-key")
    monkeypatch.setattr(
        runner,
        "DefaultAsyncHttpxClient",
        lambda **kwargs: factory(transport=httpx2.MockTransport(respond), **kwargs),
    )


@pytest.mark.parametrize("include_usage", [True, False])
@pytest.mark.parametrize("effort,tier", [(None, None), ("none", "default"), ("low", "flex")])
def test_real_responses_adapter_uses_configured_model_and_strict_tools(
    runtime, tmp_path, monkeypatch, include_usage, effort, tier
):
    authorize(runtime)
    store = AgentStore(tmp_path / "agent.sqlite3")
    store.initialize(
        runtime.ledger, "task", Goal(subject="test-report", purpose="research", quality="standard")
    )
    scripted = ScriptedBuyer()
    requests = []

    async def respond(request):
        assert str(request.url) == "https://api.openai.com/v1/responses"
        body = json.loads(request.content)
        requests.append(body)
        response = await scripted.get_response(input=body["input"])
        return httpx2.Response(
            200,
            json={
                "id": f"response-{len(requests)}",
                "object": "response",
                "created_at": 0,
                "status": "completed",
                "model": "gpt-6-luna",
                "parallel_tool_calls": False,
                "output": [item.model_dump(mode="json") for item in response.output],
                "usage": {
                    "input_tokens": 10,
                    "output_tokens": 5,
                    "total_tokens": 15,
                    "input_tokens_details": {"cached_tokens": 0},
                    "output_tokens_details": {"reasoning_tokens": 0},
                }
                if include_usage
                else None,
            },
        )

    mock_transport(monkeypatch, respond)
    command = [
        "--state-dir",
        str(tmp_path),
        "agent",
        "task",
        "--subject",
        "test-report",
        "--purpose",
        "research",
        "--quality",
        "standard",
        "--mode",
        "live",
        "--model",
        "gpt-6-luna",
        "--max-turns",
        "6",
        "--max-output-tokens",
        "512",
        "--timeout",
        "30",
    ]
    if effort is not None:
        command += ["--reasoning-effort", effort, "--service-tier", tier]
    result = AgentReport.model_validate(asyncio.run(cli.run(cli.parser().parse_args(command))))
    assert result.termination == "completed"
    assert result.purchase.payment_state == "settled"
    assert len(requests) == 5
    assert result.model_usage.input_tokens == (50 if include_usage else 0)
    assert result.model_usage.output_tokens == (25 if include_usage else 0)
    assert result.model_usage.incomplete == (not include_usage)
    for body in requests:
        assert body["model"] == "gpt-6-luna"
        if effort is None:
            assert "reasoning" not in body and "service_tier" not in body
        else:
            assert body["reasoning"] == {"effort": effort}
            assert body["service_tier"] == tier
        assert body["parallel_tool_calls"] is False
        assert body["max_output_tokens"] == 512
        assert body["store"] is False
        assert body["truncation"] == "disabled"
        assert body["text"]["format"]["strict"] is True
        assert all(tool["type"] == "function" and tool["strict"] for tool in body["tools"])
    # Every continuation carries the prior tool's result linked to its call ID.
    for body in requests[1:]:
        calls = [item for item in body["input"] if item.get("type") == "function_call"]
        outputs = [item for item in body["input"] if item.get("type") == "function_call_output"]
        assert len(calls) == len(outputs)
        assert [item["call_id"] for item in calls] == [item["call_id"] for item in outputs]
    from test_buyer_agent import authority

    assert authority(runtime) == (1, 1, 10_000)


@pytest.mark.parametrize("fail_on,status", [(None, 200), (1, 429), (1, 500), (6, 429), (6, 500)])
def test_six_request_envelope_and_no_retries(runtime, tmp_path, monkeypatch, fail_on, status):
    authorize(runtime, budget=0, cap=0, services=("report-free",))
    goal = Goal(subject="phase2-live-smoke", purpose="research", quality="basic")
    store = AgentStore(tmp_path / "agent.sqlite3")
    store.initialize(runtime.ledger, "task", goal)
    requests = []
    active = 0

    async def respond(request):
        nonlocal active
        active += 1
        assert active == 1
        try:
            await asyncio.sleep(0)
            body = json.loads(request.content)
            requests.append(body)
            number = len(requests)
            assert number <= 6
            assert body["model"] == "gpt-6-luna"
            assert body["reasoning"] == {"effort": "none"}
            assert body["service_tier"] == "default"
            assert body["max_output_tokens"] == 512
            assert body["parallel_tool_calls"] is False
            assert body["text"]["format"]["strict"] is True
            assert len(body["tools"]) == 4 and all(t["strict"] for t in body["tools"])
            if number > 1:
                outputs = [i for i in body["input"] if i.get("type") == "function_call_output"]
                assert outputs[-1]["call_id"] == f"call_{number - 1}"
            if number == fail_on:
                return httpx2.Response(status, json={"error": {"message": "mock failure"}})
            return httpx2.Response(
                200,
                json={
                    "id": f"resp_{number}",
                    "object": "response",
                    "created_at": 0,
                    "status": "completed",
                    "model": "gpt-6-luna",
                    "service_tier": "default",
                    "parallel_tool_calls": False,
                    "output": [
                        {
                            "id": f"fc_{number}",
                            "type": "function_call",
                            "name": "get_purchase_status",
                            "call_id": f"call_{number}",
                            "arguments": "{}",
                            "status": "completed",
                        }
                    ],
                    "usage": {
                        "input_tokens": 10,
                        "output_tokens": 5,
                        "total_tokens": 15,
                        "input_tokens_details": {"cached_tokens": 0},
                        "output_tokens_details": {"reasoning_tokens": 0},
                    },
                },
            )
        finally:
            active -= 1

    mock_transport(monkeypatch, respond)
    result = asyncio.run(
        runner.run_buyer(
            runtime.buyer("task"),
            store,
            goal,
            Settings(
                mode="live",
                model="gpt-6-luna",
                reasoning_effort="none",
                service_tier="default",
                max_turns=6,
                max_output_tokens=512,
                timeout_seconds=30,
            ),
        )
    )
    expected = fail_on or 6
    assert len(requests) == result.model_usage.requests_started == expected
    assert result.termination == ("model_error" if fail_on else "turn_limit")
    assert result.tool_attempts == expected - bool(fail_on)
    assert result.model_usage.incomplete == bool(fail_on)
    assert result.model_usage.unresolved_requests == bool(fail_on)
    from test_buyer_agent import authority

    assert authority(runtime) == (0, 0, 0)


@pytest.mark.parametrize(
    "field,value", [("reasoning_effort", "unbounded"), ("service_tier", "standard")]
)
def test_model_options_reject_invalid_configuration(field, value):
    with pytest.raises(ValidationError):
        Settings(**{field: value})
    with pytest.raises(SystemExit):
        cli.parser().parse_args(
            [
                "agent",
                "task",
                "--subject",
                "test",
                "--purpose",
                "research",
                "--quality",
                "basic",
                "--" + field.replace("_", "-"),
                value,
            ]
        )
