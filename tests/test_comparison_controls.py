import asyncio
import copy
from types import SimpleNamespace

import httpx2
import pytest
from comparison_support import count_payload, payload

from agentic_payments_lab.buyer_agent.call_budget import BudgetDenied
from agentic_payments_lab.buyer_agent.comparison import (
    Comparison,
    ComparisonLimits,
    initialize_comparison,
    paired_plan,
)
from agentic_payments_lab.buyer_agent.comparison_protocol import (
    COUNT_ENDPOINTS,
    ENDPOINTS,
    count_request_body,
    request_body,
)
from agentic_payments_lab.buyer_agent.comparison_runner import MockExchange, run_mock_comparison
from agentic_payments_lab.domain import canonical


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
@pytest.mark.parametrize(
    "mutation",
    [
        "endpoint",
        "method",
        "model",
        "output_limit",
        "schema",
        "parallel",
        "server_tool",
        "extra_field",
        "oversize",
    ],
)
def test_wire_mutation_rejected_before_count_or_generation(provider, mutation):
    entry = next(e for e in paired_plan() if e["provider"] == provider)
    limits = ComparisonLimits()
    transcript = [{"kind": "user", "text": "synthetic"}]
    generation = request_body(provider, transcript, limits)
    expected = copy.deepcopy(generation)
    uri, method = ENDPOINTS[provider], "POST"
    if mutation == "endpoint":
        uri += "?redirect=other"
    elif mutation == "method":
        method = "GET"
    elif mutation == "model":
        generation["model"] = "wrong"
    elif mutation == "output_limit":
        generation["max_output_tokens" if provider == "openai" else "max_tokens"] = 513
    elif mutation == "schema":
        generation["tools"][0]["strict"] = False
    elif mutation == "parallel":
        if provider == "openai":
            generation["parallel_tool_calls"] = True
        else:
            generation["tool_choice"]["disable_parallel_tool_use"] = False
    elif mutation == "server_tool":
        generation["tools"].append({"type": "web_search"})
    elif mutation == "extra_field":
        generation["previous_response_id"] = "old"
    else:
        generation["extra"] = "x" * 32768
    fake = SimpleNamespace(limits=limits, reserve=lambda *_: pytest.fail("Before reservation"))
    exchange = MockExchange(fake, entry, lambda _: pytest.fail("Before dispatch"))
    exchange.expected, exchange.transcript = expected, transcript
    with pytest.raises(BudgetDenied):
        asyncio.run(
            exchange.handle_async_request(
                httpx2.Request(method, uri, content=canonical(generation))
            )
        )


def coerce_json_type(body, provider, field):
    if field == "strict":
        body["tools"][0]["strict"] = 1
    elif field == "parallel":
        if provider == "openai":
            body["parallel_tool_calls"] = 0
        else:
            body["tool_choice"]["disable_parallel_tool_use"] = 1
    else:
        body["max_output_tokens" if provider == "openai" else "max_tokens"] = 512.0


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
@pytest.mark.parametrize(
    ("target", "field"),
    [
        ("generation", "strict"),
        ("generation", "parallel"),
        ("generation", "output"),
        ("count", "strict"),
        ("count", "parallel"),
        ("expected", "strict"),
    ],
)
def test_wire_json_types_rejected_before_side_effects(provider, target, field):
    entry = next(e for e in paired_plan() if e["provider"] == provider)
    limits = ComparisonLimits()
    transcript = [{"kind": "user", "text": "synthetic"}]
    generation = request_body(provider, transcript, limits)
    expected = copy.deepcopy(generation)
    counting = target == "count"
    body = count_request_body(provider, generation) if counting else copy.deepcopy(generation)
    coerce_json_type(expected if target == "expected" else body, provider, field)

    def unexpected(*_):
        pytest.fail("Type mismatch must be rejected before persistence or dispatch")

    fake = SimpleNamespace(limits=limits, reserve=unexpected, begin_count=unexpected)
    exchange = MockExchange(fake, entry, unexpected)
    exchange.expected, exchange.transcript, exchange.counting = expected, transcript, counting
    uri = (COUNT_ENDPOINTS if counting else ENDPOINTS)[provider]
    with pytest.raises(BudgetDenied, match="comparison_request_contract"):
        asyncio.run(
            exchange.handle_async_request(httpx2.Request("POST", uri, content=canonical(body)))
        )


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
@pytest.mark.parametrize("field", ["strict", "parallel"])
def test_count_json_types_rejected_before_persistence(provider, field):
    entry = next(e for e in paired_plan() if e["provider"] == provider)
    limits = ComparisonLimits()
    generation = request_body(provider, [{"kind": "user", "text": "synthetic"}], limits)
    body = copy.deepcopy(count_request_body(provider, generation))
    coerce_json_type(body, provider, field)

    def unexpected(**_):
        pytest.fail("Type mismatch must be rejected before persistence")

    fake = SimpleNamespace(limits=limits, verify_source=lambda: None, connect=unexpected)
    with pytest.raises(BudgetDenied, match="comparison_count_payload_mismatch"):
        Comparison.begin_count(fake, entry, generation, canonical(body).encode())


@pytest.mark.parametrize("kind", ["request_bound", "tool_bound", "invalid_arguments"])
def test_requests_tools_and_invalid_attempts_are_bounded(tmp_path, kind):
    limits = (
        {"requests_total": 6}
        if kind == "request_bound"
        else {"tools_per_episode": 2}
        if kind == "tool_bound"
        else {"requests_total": 1}
    )
    comparison = asyncio.run(initialize_comparison(tmp_path / kind, **limits))
    calls = []

    def factory(entry):
        def respond(request):
            if str(request.url) in COUNT_ENDPOINTS.values():
                return httpx2.Response(200, json=count_payload(entry["provider"]))
            calls.append(entry["id"])
            return httpx2.Response(
                200,
                json=payload(
                    entry["provider"],
                    tool="get_purchase_status",
                    arguments={"task_id": "wrong"} if kind == "invalid_arguments" else {},
                    call_id=f"c{len(calls)}",
                ),
            )

        return respond

    report = asyncio.run(run_mock_comparison(comparison, factory))
    expected = {"request_bound": 6, "tool_bound": 3, "invalid_arguments": 1}[kind]
    assert len(calls) == expected
    assert report["request_attempts_reserved"] == expected
    assert sum(r["tool_attempts"] for r in report["requests"]) == expected
    assert (
        len(report["tools"]) == {"request_bound": 6, "tool_bound": 2, "invalid_arguments": 0}[kind]
    )
    assert report["campaign"]["status"] == "stopped"
    result = report["episodes"]["openai.trial-1.free"]["result"]
    assert result["after"]["executions"] == result["after"]["charges"] == 0
    if kind == "request_bound":
        assert result["termination"] == "request_limit"
    if kind == "invalid_arguments":
        assert result["termination"] == "invalid_arguments"


@pytest.mark.parametrize("kind", ["429", "redirect", "oversize", "malformed"])
def test_sdk_never_retries_failed_generation(tmp_path, kind):
    comparison = asyncio.run(initialize_comparison(tmp_path / kind))
    calls = []

    def factory(entry):
        def respond(request):
            if str(request.url) in COUNT_ENDPOINTS.values():
                return httpx2.Response(200, json=count_payload(entry["provider"]))
            calls.append(1)
            if kind == "429":
                return httpx2.Response(429, headers={"retry-after": "0"})
            if kind == "redirect":
                return httpx2.Response(307, headers={"location": "https://untrusted.invalid"})
            return httpx2.Response(
                200, content=b"x" * 65537 if kind == "oversize" else b'{"usage":'
            )

        return respond

    report = asyncio.run(run_mock_comparison(comparison, factory))
    assert calls == [1] and report["request_attempts_reserved"] == 1
    assert (
        report["actual_generation_requests"] is None and report["unresolved_exposure_nano_usd"] > 0
    )
    assert not report["usage_complete"]
    with pytest.raises(BudgetDenied):
        asyncio.run(run_mock_comparison(Comparison(comparison.path), factory))


def test_complete_usage_exceeding_counted_reservation_stops_and_retains_exposure(tmp_path):
    comparison = asyncio.run(initialize_comparison(tmp_path / "high-spend"))
    calls = []

    def factory(entry):
        def respond(request):
            if str(request.url) in COUNT_ENDPOINTS.values():
                return httpx2.Response(200, json=count_payload(entry["provider"], 10))
            calls.append(entry["provider"])
            data = payload(entry["provider"])
            if entry["provider"] == "anthropic":
                data["usage"]["input_tokens"] = 1_000_000
            return httpx2.Response(200, json=data)

        return respond

    report = asyncio.run(run_mock_comparison(comparison, factory))
    assert report["campaign"]["status"] == "stopped"
    assert calls.count("anthropic") == 1  # Counted allowance was exceeded; never continue.
    assert report["providers"]["anthropic"]["estimated_total_nano_usd"] is None
    assert report["unresolved_exposure_nano_usd"] == 1000012500
    assert report["unresolved_reservation_nano_usd"] == 2314000
    assert report["providers"]["openai"]["estimated_total_nano_usd"] < 1_500_000_000
