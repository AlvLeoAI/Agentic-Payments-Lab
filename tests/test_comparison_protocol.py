import copy
import json

import pytest
from comparison_support import payload, usage

from agentic_payments_lab.buyer_agent.boundary import ARGUMENTS
from agentic_payments_lab.buyer_agent.call_budget import BudgetDenied
from agentic_payments_lab.buyer_agent.comparison import ComparisonLimits, paired_plan
from agentic_payments_lab.buyer_agent.comparison_protocol import (
    MODELS,
    native_usage,
    parse_response,
    request_body,
    semantic_input,
)


def test_same_semantics_supported_schemas_and_sequential_correlation():
    transcript = semantic_input(
        paired_plan()[0]["goal"], {"services": [], "reason": "no_suitable_service"}
    )
    transcript += [
        {"kind": "assistant_text", "text": "Checking status."},
        {"kind": "tool_call", "call_id": "c1", "name": "get_purchase_status", "arguments": "{}"},
        {"kind": "tool_result", "call_id": "c1", "content": '{"payment_state":"not_started"}'},
    ]
    a, b = [request_body(p, transcript, ComparisonLimits()) for p in MODELS]
    assert a["instructions"] == b["system"]
    assert a["input"][0]["content"] == b["messages"][0]["content"][0]["text"]
    assert a["reasoning"] == {"effort": "none"} and a["service_tier"] == "default"
    assert b["thinking"] == {"type": "disabled"} and b["output_config"]["effort"] == "medium"
    assert b["service_tier"] == "standard_only" and b["inference_geo"] == "global"
    assert a["max_output_tokens"] == b["max_tokens"] == 512
    assert (
        a["parallel_tool_calls"] is False and b["tool_choice"]["disable_parallel_tool_use"] is True
    )
    assert len(a["tools"]) == len(b["tools"]) == 4
    for one, two in zip(a["tools"], b["tools"], strict=True):
        assert one["name"] == two["name"] in ARGUMENTS
        assert one["parameters"] == two["input_schema"]
        assert one["description"] == two["description"] and one["strict"] is two["strict"] is True
        assert one["parameters"]["additionalProperties"] is False
        assert not {"minLength", "maxLength", "pattern", "maximum", "minimum"} & set(
            json.dumps(one["parameters"]).split('"')
        )
    assert a["text"]["format"]["schema"] == b["output_config"]["format"]["schema"]
    assert b["messages"][-1]["role"] == "user"
    assert (
        b["messages"][-1]["content"][0]["tool_use_id"]
        == b["messages"][-2]["content"][1]["id"]
        == "c1"
    )
    assert "cache_control" not in json.dumps(b)
    assert all(key not in b for key in ("temperature", "top_p", "top_k", "betas", "mcp_servers"))


@pytest.mark.parametrize(
    "mutation", ["orphan", "duplicate", "missing_result", "unknown_tool", "wrong_id"]
)
def test_continuation_correlation_rejects_before_request(mutation):
    transcript = [
        {"kind": "user", "text": "synthetic"},
        {"kind": "tool_call", "call_id": "c", "name": "list_services", "arguments": "{}"},
        {"kind": "tool_result", "call_id": "c", "content": "{}"},
    ]
    if mutation == "orphan":
        del transcript[1]
    elif mutation == "duplicate":
        transcript += copy.deepcopy(transcript[1:])
    elif mutation == "missing_result":
        transcript.pop()
    elif mutation == "unknown_tool":
        transcript[1]["name"] = "reconcile"
    else:
        transcript[2]["call_id"] = "other"
    for provider in MODELS:
        with pytest.raises(BudgetDenied):
            request_body(provider, transcript, ComparisonLimits())


@pytest.mark.parametrize("provider", MODELS)
def test_complete_native_usage_and_explicit_zero_ttl_derivation(provider):
    evidence = native_usage(provider, usage(provider))
    assert evidence["complete"] and evidence["regime_ok"]
    assert evidence["estimated_nano_usd"] == 3500
    assert evidence["prompt_tokens"] == 10 and evidence["output_tokens"] == 5
    if provider == "anthropic":
        assert evidence["native"]["cache_creation"] == {
            "ephemeral_5m_input_tokens": None,
            "ephemeral_1h_input_tokens": None,
        }
        assert evidence["native"]["cache_allocation_provenance"] == {
            "details_kind": "absent",
            "derived_zero": {"ephemeral_5m_input_tokens": 0, "ephemeral_1h_input_tokens": 0},
        }


@pytest.mark.parametrize("provider", MODELS)
@pytest.mark.parametrize("value", [None, "10", True, -1, 1.5, 10**10])
def test_malformed_native_usage_preserves_other_observed_counts(provider, value):
    raw = usage(provider)
    raw["input_tokens"] = value
    parsed = native_usage(provider, raw)
    assert not parsed["complete"] and parsed["estimated_nano_usd"] is None
    assert parsed["native"]["input_tokens"] is None and parsed["output_tokens"] == 5


@pytest.mark.parametrize(
    "field",
    ["input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"],
)
def test_missing_native_counter_not_zero(field):
    raw = usage("anthropic")
    del raw[field]
    result = native_usage("anthropic", raw)
    assert not result["complete"] and result["native"][field] is None


def test_native_cache_allocations_disjoint_and_nonzero_stops_uncached_regime():
    raw = usage("anthropic")
    raw.update(
        cache_creation_input_tokens=30,
        cache_read_input_tokens=40,
        cache_creation={"ephemeral_5m_input_tokens": 10, "ephemeral_1h_input_tokens": 20},
    )
    result = native_usage("anthropic", raw)
    assert result["complete"] and not result["regime_ok"]
    assert result["prompt_tokens"] == 80  # 10+30+40; TTLs are allocations, not extra input.
    assert result["estimated_nano_usd"] == 9150
    assert result["native"]["cache_creation_input_tokens"] == 30


def test_cache_input_crosses_long_context_threshold_without_double_counting_ttls():
    raw = usage("anthropic")
    raw.update(
        input_tokens=99990,
        cache_creation_input_tokens=10,
        cache_read_input_tokens=1,
        cache_creation={"ephemeral_5m_input_tokens": 6, "ephemeral_1h_input_tokens": 4},
    )
    result = native_usage("anthropic", raw)
    assert result["prompt_tokens"] == 100001
    assert result["estimated_nano_usd"] == 99990 * 500 + 6 * 625 + 4 * 1000 + 50 + 5 * 2500
    assert result["complete"] and result["pricing_verified"] and not result["regime_ok"]


def test_native_context_window_stop_retains_usage_and_never_executes_tool():
    raw = payload(
        "anthropic",
        tool="purchase",
        arguments={"quote_handle": "partial"},
        stop="model_context_window_exceeded",
    )
    result = parse_response("anthropic", raw, set())
    assert result["termination"] == "truncated" and result["call"] is None
    assert result["usage"]["complete"] and result["usage"]["estimated_nano_usd"] == 3500
    assert result["stop_reason"] == "model_context_window_exceeded"


@pytest.mark.parametrize(
    "details",
    [
        None,
        {},
        {"ephemeral_5m_input_tokens": 30},
        {"ephemeral_5m_input_tokens": 10, "ephemeral_1h_input_tokens": 19},
        {"ephemeral_5m_input_tokens": True, "ephemeral_1h_input_tokens": 29},
    ],
)
def test_nonzero_cache_needs_exact_valid_ttl_allocation(details):
    raw = usage("anthropic")
    raw.update(cache_creation_input_tokens=30, cache_creation=details)
    result = native_usage("anthropic", raw)
    assert not result["complete"] and result["estimated_nano_usd"] is None
    assert result["native"]["cache_creation_input_tokens"] == 30


@pytest.mark.parametrize(
    "field,value",
    [
        ("input_tokens", 1000001),
        ("service_tier", "priority"),
        ("inference_geo", "us"),
        ("speed", "fast"),
        ("output_tokens_details", {"thinking_tokens": 1}),
        ("server_tool_use", {"web_fetch_requests": 1, "web_search_requests": 0}),
    ],
)
def test_unexpected_native_pricing_or_thinking_stops(field, value):
    raw = usage("anthropic")
    raw[field] = value
    assert not native_usage("anthropic", raw)["regime_ok"]


@pytest.mark.parametrize("provider", MODELS)
@pytest.mark.parametrize(
    "kind", ["final", "refusal", "truncated", "bad_json", "bad_arguments", "unavailable_tool"]
)
def test_known_quality_failures_are_not_unknown_billing(provider, kind):
    data = payload(provider)
    if kind == "refusal":
        if provider == "anthropic":
            data["stop_reason"] = "refusal"
        else:
            data["output"][0]["content"] = [{"type": "refusal", "refusal": "No."}]
    elif kind == "truncated":
        data["stop_reason" if provider == "anthropic" else "status"] = (
            "max_tokens" if provider == "anthropic" else "incomplete"
        )
        if provider == "openai":
            data["incomplete_details"] = {"reason": "max_output_tokens"}
    elif kind == "bad_json":
        (data["content"][0] if provider == "anthropic" else data["output"][0]["content"][0])[
            "text"
        ] = "Not JSON"
    elif kind in ("bad_arguments", "unavailable_tool"):
        data = payload(
            provider,
            tool="purchase" if kind == "bad_arguments" else "reconcile",
            arguments={"task_id": "other"},
        )
    result = parse_response(provider, data)
    expected = {
        "final": "completed",
        "bad_json": "invalid_model_output",
        "bad_arguments": "invalid_arguments",
    }.get(kind, kind)
    assert result["termination"] == expected and result["control_error"] is None
    assert result["usage"]["complete"] and result["usage"]["estimated_nano_usd"] == 3500


@pytest.mark.parametrize("provider", MODELS)
@pytest.mark.parametrize(
    "kind", ["model", "usage", "duplicate", "parallel", "hidden_tool", "unknown_stop"]
)
def test_uncertain_contract_and_authority_fail_closed(provider, kind):
    data = payload(provider, tool="get_purchase_status")
    if kind == "model":
        data["model"] = "unexpected"
    elif kind == "usage":
        del data["usage"]
    elif kind == "parallel":
        data["content" if provider == "anthropic" else "output"] *= 2
    elif kind == "hidden_tool":
        data["content" if provider == "anthropic" else "output"].append({"type": "server_tool_use"})
    elif kind == "unknown_stop":
        data["stop_reason" if provider == "anthropic" else "status"] = "pause_turn"
    result = parse_response(provider, data, seen_ids={"call-1"} if kind == "duplicate" else ())
    assert result["control_error"] is not None
    if kind in ("duplicate", "parallel", "hidden_tool", "unknown_stop"):
        assert result["call"] is None


@pytest.mark.parametrize("provider", MODELS)
def test_explanation_redaction_with_provenance(provider):
    data = payload(
        provider, explanation="untrusted sk-PRIVATE123 https://private.invalid name@example.invalid"
    )
    result = parse_response(provider, data)
    serialized = json.dumps(result)
    assert all(x not in serialized for x in ("PRIVATE123", "private.invalid", "name@example"))
    assert result["text_sanitization"]["redacted_or_normalized"]


@pytest.mark.parametrize("provider", MODELS)
def test_partial_tool_on_truncation_retains_usage_without_dispatch(provider):
    data = payload(provider, tool="purchase", arguments={"quote_id": "partial"})
    if provider == "anthropic":
        data["stop_reason"] = "max_tokens"
        data["content"][0]["input"] = '{"quote_id":'
    else:
        data["status"] = "incomplete"
        data["incomplete_details"] = {"reason": "max_output_tokens"}
        data["output"][0]["arguments"] = '{"quote_id":'
    evidence = parse_response(provider, data)
    assert evidence["termination"] == "truncated"
    assert evidence["schema_outcome"] == "truncated_unvalidated"
    assert evidence["usage"]["complete"] and evidence["usage"]["estimated_nano_usd"] == 3500
    assert evidence["response_valid"] and evidence["control_error"] is None
    assert evidence["call"] is None and evidence["tool_attempts"] == 1


@pytest.mark.parametrize(
    "reason,termination",
    [("content_filter", "content_filter"), ("max_messages", None), (None, None), ("steered", None)],
)
def test_incomplete_reasons_are_not_all_truncation(reason, termination):
    data = payload("openai", stop="incomplete")
    data["incomplete_details"] = {"reason": reason}
    evidence = parse_response("openai", data)
    assert evidence["termination"] == termination
    assert (evidence["control_error"] is None) == (termination is not None)
    assert evidence["usage"]["complete"] and evidence["usage_pricing_verified"]


def test_long_context_rates_and_conditional_backstops():
    from agentic_payments_lab.buyer_agent.comparison_protocol import backstop

    raw = usage("anthropic")
    raw["input_tokens"] = 100001
    result = native_usage("anthropic", raw)
    assert result["regime_ok"] and result["estimated_nano_usd"] == 100001 * 500 + 5 * 2500
    raw = usage("openai")
    raw.update(input_tokens=272001, total_tokens=272006)
    result = native_usage("openai", raw)
    assert result["estimated_nano_usd"] == 272001 * 200 + 5 * 750
    assert backstop("openai") == 525384000 and backstop("anthropic") == 1001280000


@pytest.mark.parametrize(
    "field,value",
    [
        ("output_tokens_details", {"thinking_tokens": False}),
        ("server_tool_use", {"web_fetch_requests": False, "web_search_requests": 0}),
    ],
)
def test_native_optional_counters_do_not_coerce_boolean_zero(field, value):
    raw = usage("anthropic")
    raw[field] = value
    assert not native_usage("anthropic", raw)["regime_ok"]


@pytest.mark.parametrize("provider", MODELS)
def test_capitalized_tool_name_is_quality_failure_not_normalized(provider):
    from agentic_payments_lab.buyer_agent.comparison_protocol import final_schema, tool_schemas

    schemas = json.dumps([tool_schemas(), final_schema()])
    assert '"enum"' not in schemas and '"const"' not in schemas
    evidence = parse_response(provider, payload(provider, tool="GET_PURCHASE_STATUS"))
    assert evidence["termination"] == "unavailable_tool" and evidence["call"] is None
    assert evidence["tool_attempts"] == 1 and evidence["usage"]["complete"]
    assert evidence["usage"]["estimated_nano_usd"] == 3500 and evidence["control_error"] is None


@pytest.mark.parametrize("provider", MODELS)
def test_capitalized_service_identifier_never_changes_authority(runtime, provider):
    import asyncio

    from conftest import authorize

    from agentic_payments_lab.buyer_agent.boundary import Boundary
    from agentic_payments_lab.buyer_agent.contracts import Goal
    from agentic_payments_lab.buyer_agent.store import AgentStore

    authorize(runtime)
    metadata = AgentStore(runtime.ledger.db.path.parent / "agent.sqlite3")
    goal = Goal(subject="synthetic", purpose="research", quality="standard")
    metadata.initialize(runtime.ledger, "task", goal)
    boundary = Boundary(runtime.buyer("task"), metadata, goal)
    evidence = parse_response(
        provider, payload(provider, tool="get_quote", arguments={"service_id": "REPORT-STANDARD"})
    )
    assert evidence["usage"]["complete"] and evidence["usage"]["estimated_nano_usd"] == 3500
    call = evidence["call"]
    assert call is not None  # Syntactically valid identifier; exact eligibility still rejects it.
    assert json.loads(call["arguments"])["service_id"] == "REPORT-STANDARD"
    result = asyncio.run(boundary.invoke(call["name"], call["arguments"]))
    assert result.error == "service_not_eligible"
    with runtime.payments.db.connect() as db:
        assert db.execute("SELECT count(*) FROM attempts").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM authorizations").fetchone()[0] == 0
    assert boundary.status().payment_state == "not_started"


@pytest.mark.parametrize(
    "input_tokens,accepted", [(922001, True), (1050000, True), (1050001, False)]
)
def test_luna_full_context_allowance_preserves_usage_beyond_old_assumption(input_tokens, accepted):
    raw = usage("openai")
    raw.update(input_tokens=input_tokens, total_tokens=input_tokens + 5)
    result = native_usage("openai", raw)
    assert result["complete"]
    assert result["native"]["input_tokens"] == input_tokens
    assert result["pricing_verified"] is accepted
    assert result["regime_ok"] is accepted
    assert result["estimated_nano_usd"] == input_tokens * 200 + 5 * 750
