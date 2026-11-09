"""Reconstructed zero-write variants, not a captured live response or billing proof."""

import asyncio
import copy
import json
import sqlite3
from contextlib import closing

import httpx2
import pytest
from activation_support import KEY, authority, request
from openai.types.responses import ResponseUsage
from pilot_support import response

from agentic_payments_lab.buyer_agent import live_pilot
from agentic_payments_lab.buyer_agent.call_budget import BudgetDenied, usage_evidence
from agentic_payments_lab.buyer_agent.pilot_envelope import PilotEnvelope, initialize_activation
from agentic_payments_lab.buyer_agent.pilot_transport import PilotTransport, validate_body
from agentic_payments_lab.buyer_agent.scripted import ScriptedBuyer

MARKER = "untrusted-usage-value-must-not-be-retained"
ABSENT = object()


def zero_usage(details=ABSENT):
    # Only these normalized counts survived the original response. Its raw
    # missing/null/invalid lifetime shape is unknown; fixtures test possibilities.
    inputs = {"cached_tokens": 0, "cache_write_tokens": 0}
    if details is not ABSENT:
        inputs["cache_write_tokens_details"] = copy.deepcopy(details)
    return {
        "input_tokens": 510,
        "output_tokens": 15,
        "total_tokens": 525,
        "input_tokens_details": inputs,
        "output_tokens_details": {"reasoning_tokens": 0},
    }


@pytest.fixture
def pilot(tmp_path):
    root = tmp_path / "pilot"
    return root, initialize_activation(root)


def run(pilot, scenario="purchase"):
    root, envelope = pilot
    return live_pilot.run_live_scenario(
        root, scenario, api_key=KEY, approved_enrollment_hash=envelope.report()["enrollment_hash"]
    )


@pytest.mark.parametrize("details,kind", [(ABSENT, "absent"), (None, "null")])
def test_zero_aggregate_without_details_is_prospective_and_preserves_evidence(pilot, details, kind):
    _, envelope = pilot
    envelope.begin_invocation("purchase")
    pending = envelope.reserve("purchase", request().content)
    raw = zero_usage(details)
    original = copy.deepcopy(raw)
    assert envelope.observe(pending, raw, "default", "gpt-6-luna")
    assert raw == original
    record = envelope.report()["requests"][0]
    assert record["status"] == "complete" and record["observed_estimate_nano"] == 58_500
    usage = record["usage"]
    assert usage["ttl_30m_tokens"] is usage["ttl_12h_tokens"] is None
    assert usage["cache_write_allocation"] == {
        "aggregate_kind": "integer",
        "details_kind": kind,
        "ttl_30m_kind": "absent",
        "ttl_12h_kind": "absent",
        "unknown_fields": False,
        "derived_tokens": {"ttl_30m_tokens": 0, "ttl_12h_tokens": 0},
    }
    # Parsing without the prospective, verified-identity opt-in still fails closed.
    observed, complete = usage_evidence(raw, cache_lifetimes=True)
    assert not complete and observed["cache_write_allocation"]["derived_tokens"] is None


@pytest.mark.parametrize("details", [ABSENT, None], ids=["absent", "null"])
def test_zero_write_sdk_continuations_share_one_envelope(pilot, monkeypatch, details):
    root, envelope = pilot
    calls = []
    raw_usage = zero_usage(details)
    decoded = ResponseUsage.model_validate(raw_usage).model_dump()
    assert decoded == raw_usage  # Pinned SDK preserves absent/null extras, not their meaning.

    for scenario in ("purchase", "replay", "refusal"):
        scripted = ScriptedBuyer()

        async def respond(req, scripted=scripted):
            body = json.loads(req.content)
            validate_body(req.content, envelope.limits)
            calls.append(body)
            output = await scripted.get_response(input=body["input"])
            payload = response(len(calls), output.output)
            payload["usage"] = copy.deepcopy(raw_usage)
            return httpx2.Response(200, json=payload)

        monkeypatch.setattr(live_pilot, "network_transport", lambda: httpx2.MockTransport(respond))
        result = asyncio.run(run(pilot, scenario))
        assert result["scenario_passed"]
        assert result["agent"]["model_usage"]["incomplete"] is False
        assert authority(root) == (1, 1, 10_000)
    assert len(calls) == 10
    for body in calls:
        ids = [i["call_id"] for i in body["input"] if i.get("type") == "function_call"]
        outputs = [i["call_id"] for i in body["input"] if i.get("type") == "function_call_output"]
        assert ids == outputs
    report = PilotEnvelope(envelope.path).report()
    assert not report["blocked"] and report["usage_complete"] and report["accounting_complete"]
    assert report["known_estimate_nano_usd"] == 10 * 58_500
    assert report["reserved_estimate_nano_usd"] == 0
    assert all(r["usage"]["ttl_30m_tokens"] is None for r in report["requests"])
    assert all(r["usage"]["ttl_12h_tokens"] is None for r in report["requests"])
    assert all(r["usage"]["cache_write_allocation"]["derived_tokens"] for r in report["requests"])


@pytest.mark.parametrize(
    "details,aggregate,estimate",
    [
        ({"ttl_30m_tokens": 0, "ttl_12h_tokens": 0}, 0, 58_500),
        ({"ttl_30m_tokens": 1, "ttl_12h_tokens": 2}, 3, 58_825),
    ],
)
def test_reported_complete_allocations_remain_observed(pilot, details, aggregate, estimate):
    _, envelope = pilot
    envelope.begin_invocation("purchase")
    pending = envelope.reserve("purchase", request().content)
    raw = zero_usage(details)
    raw["input_tokens_details"]["cache_write_tokens"] = aggregate
    assert envelope.observe(pending, raw, "default", "gpt-6-luna")
    row = envelope.report()["requests"][0]
    assert row["observed_estimate_nano"] == estimate
    assert row["usage"]["ttl_30m_tokens"] == details["ttl_30m_tokens"]
    assert row["usage"]["ttl_12h_tokens"] == details["ttl_12h_tokens"]
    assert row["usage"]["cache_write_allocation"]["derived_tokens"] is None


BAD_CASES = [
    ("aggregate_absent", ("input_tokens_details", "cache_write_tokens"), ABSENT),
    ("aggregate_null", ("input_tokens_details", "cache_write_tokens"), None),
    ("aggregate_bool", ("input_tokens_details", "cache_write_tokens"), False),
    ("aggregate_float", ("input_tokens_details", "cache_write_tokens"), 0.0),
    ("aggregate_string", ("input_tokens_details", "cache_write_tokens"), "0"),
    ("aggregate_negative", ("input_tokens_details", "cache_write_tokens"), -1),
    ("positive_missing_allocation", ("input_tokens_details", "cache_write_tokens"), 1),
    (
        "positive_null_allocation",
        ("input_tokens_details",),
        {"cached_tokens": 0, "cache_write_tokens": 1, "cache_write_tokens_details": None},
    ),
    ("input_string", ("input_tokens",), "510"),
    ("total_mismatch", ("total_tokens",), 524),
    ("cached_over_input", ("input_tokens_details", "cached_tokens"), 511),
    ("cached_absent", ("input_tokens_details", "cached_tokens"), ABSENT),
    ("reasoning_over_output", ("output_tokens_details", "reasoning_tokens"), 16),
    ("reasoning_absent", ("output_tokens_details", "reasoning_tokens"), ABSENT),
    ("unknown_usage", ("extra",), 0),
    ("unknown_input", ("input_tokens_details", "extra"), 0),
    ("unknown_output", ("output_tokens_details", "extra"), 0),
]
BAD_DETAILS = [
    False,
    0,
    0.0,
    MARKER,
    [],
    {},
    {"ttl_30m_tokens": 0},
    {"ttl_30m_tokens": 0, "ttl_12h_tokens": None},
    {"ttl_30m_tokens": 0, "ttl_12h_tokens": False},
    {"ttl_30m_tokens": 0, "ttl_12h_tokens": "0"},
    {"ttl_30m_tokens": 0, "ttl_12h_tokens": 0.0},
    {"ttl_30m_tokens": 0, "ttl_12h_tokens": -1},
    {"ttl_30m_tokens": 0, "ttl_12h_tokens": 1},
    {"ttl_30m_tokens": 0, "ttl_12h_tokens": 0, "ttl_24h_tokens": 0},
]
BAD_CASES += [
    (f"malformed_details_{i}", ("input_tokens_details", "cache_write_tokens_details"), v)
    for i, v in enumerate(BAD_DETAILS)
]


@pytest.mark.parametrize("label,path,value", BAD_CASES, ids=[c[0] for c in BAD_CASES])
def test_raw_usage_rejects_malformed_and_unknown_before_sdk(pilot, label, path, value):
    root, envelope = pilot
    raw = zero_usage()
    parent = raw
    for key in path[:-1]:
        parent = parent[key]
    if value is ABSENT:
        parent.pop(path[-1])
    else:
        parent[path[-1]] = value
    calls = []

    def respond(_):
        calls.append(1)
        payload = response(1, [])
        payload["usage"] = raw
        return httpx2.Response(200, json=payload)

    envelope.begin_invocation("purchase")
    transport = PilotTransport(envelope, "purchase", KEY, httpx2.MockTransport(respond))
    with pytest.raises(BudgetDenied):
        asyncio.run(transport.handle_async_request(request()))
    report = envelope.report()
    row = report["requests"][0]
    assert row["status"] == "incomplete", label
    assert row["usage"]["cache_write_allocation"]["derived_tokens"] is None
    aggregate_kinds = {
        "aggregate_absent": "absent",
        "aggregate_null": "null",
        "aggregate_bool": "boolean",
        "aggregate_float": "number",
        "aggregate_string": "string",
        "aggregate_negative": "integer",
    }
    if label in aggregate_kinds:
        assert row["usage"]["cache_write_tokens"] is None
        assert row["usage"]["cache_write_allocation"]["aggregate_kind"] == aggregate_kinds[label]
    assert report["blocked"] and not report["accounting_complete"]
    assert row["observed_estimate_nano"] is None and report["reserved_estimate_nano_usd"] > 0
    assert MARKER not in json.dumps(report)
    with pytest.raises(BudgetDenied):
        asyncio.run(transport.handle_async_request(request()))
    assert calls == [1] and authority(root) == (0, 0, 0)
    if label in ("aggregate_bool", "aggregate_float", "aggregate_string", "input_string"):
        # SDK Pydantic coercion would mask the raw defect; the guard must run first.
        decoded = ResponseUsage.model_validate(raw).model_dump()
        assert type(decoded["input_tokens_details"]["cache_write_tokens"]) is int
        assert type(decoded["input_tokens"]) is int


@pytest.mark.parametrize("model,tier", [("other-model", "default"), ("gpt-6-luna", "priority")])
def test_identity_mismatch_never_derives_or_prices_zero_allocation(pilot, model, tier):
    _, envelope = pilot
    envelope.begin_invocation("purchase")
    pending = envelope.reserve("purchase", request().content)
    assert not envelope.observe(pending, zero_usage(None), tier, model)
    row = envelope.report()["requests"][0]
    assert row["status"] != "complete" and row["observed_estimate_nano"] is None
    assert row["usage"]["cache_write_allocation"]["derived_tokens"] is None


@pytest.mark.parametrize("bad_request", [2, 5])
def test_incomplete_continuation_stops_without_repricing_or_duplicate_charge(
    pilot, monkeypatch, bad_request
):
    root, envelope = pilot
    scripted, calls = ScriptedBuyer(), []

    async def respond(req):
        calls.append(json.loads(req.content))
        output = await scripted.get_response(input=calls[-1]["input"])
        payload = response(len(calls), output.output)
        payload["usage"] = zero_usage(None)
        if len(calls) == bad_request:
            payload["usage"]["input_tokens_details"]["cache_write_tokens"] = 1
        return httpx2.Response(200, json=payload)

    monkeypatch.setattr(live_pilot, "network_transport", lambda: httpx2.MockTransport(respond))
    result = asyncio.run(run(pilot))
    assert result["agent"]["termination"] == "model_error" and not result["scenario_passed"]
    report = envelope.report()
    assert len(calls) == len(report["requests"]) == bad_request
    assert report["known_estimate_nano_usd"] == (bad_request - 1) * 58_500
    assert report["estimated_total_nano_usd"] is None and report["reserved_estimate_nano_usd"] > 0
    assert report["blocked"] and not report["usage_complete"]
    expected = (1, 1, 10_000) if bad_request == 5 else (0, 0, 0)
    assert authority(root) == expected
    if bad_request == 5:
        assert result["agent"]["purchase"]["payment_state"] == "settled"
        assert result["agent"]["purchase"]["delivery_state"] == "delivered"
    reopened = PilotEnvelope(envelope.path)
    assert reopened.report() == report
    for scenario in ("purchase", "replay", "refusal"):
        with pytest.raises(BudgetDenied):
            asyncio.run(run((root, reopened), scenario))
    assert len(calls) == bad_request and authority(root) == expected


def test_old_incomplete_journal_is_not_normalized_on_reopen(pilot):
    _, envelope = pilot
    envelope.begin_invocation("purchase")
    pending = envelope.reserve("purchase", b" " * 2908)
    old_usage = {
        "input_tokens": 510,
        "output_tokens": 15,
        "total_tokens": 525,
        "cached_tokens": 0,
        "cache_write_tokens": 0,
        "reasoning_tokens": 0,
        "ttl_30m_tokens": None,
        "ttl_12h_tokens": None,
    }
    serialized = json.dumps(old_usage)
    # Reconstruct a pre-correction row only in a newly initialized synthetic fixture.
    with envelope.connect(write=True) as db:
        db.execute(
            "UPDATE requests SET status='incomplete',reason='usage_incomplete',usage=?,"
            "returned_tier='default',returned_model='gpt-6-luna' WHERE id=?",
            (serialized, pending),
        )
    envelope.finish_invocation("purchase", "stopped")
    original = envelope.report()
    reopened = PilotEnvelope(envelope.path)
    assert reopened.report() == original
    assert original["reserved_estimate_nano_usd"] == 1_239_000
    assert original["known_estimate_nano_usd"] is original["estimated_total_nano_usd"] is None
    assert original["blocked"] and not original["usage_complete"]
    with pytest.raises(BudgetDenied):
        reopened.begin_invocation("replay")
    with pytest.raises(BudgetDenied, match="already_resolved"):
        envelope.observe(pending, zero_usage(), "default", "gpt-6-luna")
    with closing(sqlite3.connect(envelope.path)) as db:
        assert db.execute("SELECT usage FROM requests").fetchone()[0] == serialized
