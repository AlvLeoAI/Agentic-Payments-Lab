"""Live adapter contract through synthetic HTTP only, never real credentials/network."""

import asyncio
import hashlib
import json
import sqlite3

import httpx2
import pytest
from comparison_support import count_payload, payload
from pydantic import SecretStr

from agentic_payments_lab.buyer_agent import generation_probe_live as live
from agentic_payments_lab.buyer_agent.call_budget import BudgetDenied
from agentic_payments_lab.buyer_agent.campaign import source_witness
from agentic_payments_lab.buyer_agent.comparison_protocol import (
    COUNT_ENDPOINTS,
    ENDPOINTS,
    count_request_body,
    request_body,
)
from agentic_payments_lab.buyer_agent.generation_probe import LIMITS, TRANSCRIPT
from agentic_payments_lab.domain import canonical, digest

KEYS = {p: SecretStr("synthetic_" + p + "_not_a_real_key_123456") for p in ("openai", "anthropic")}


def inventory(tmp_path, known=8_619_300, unknown=1_239_000):
    source = tmp_path / "synthetic_inventory.json"
    source.write_text(json.dumps({"known": known, "unknown": unknown}))
    return [
        {
            "provider": "openai",
            "source_file": str(source),
            "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "known_estimated_nano": known,
            "unresolved_nano": unknown,
        }
    ]


def review(records):
    return {
        "generation_authorization": "approve_one_count_and_one_generation_per_provider",
        "reviewed_source_hash": digest(source_witness()),
        "reviewed_generation_hashes": live.payload_hashes(),
        "shared_authorization_id": "existing-envelope",
        "prior_inventory_hash": digest(records),
        "prior_inventory_complete_attested": True,
        "reported_rates_accepted": True,
        "margin_assumption_accepted": True,
        "uncached_mode_risk_accepted": True,
        "preflight_lineage_reviewed": True,
        "account_reports": live.ACCOUNT_REPORTS,
        "strict_quality_gate_accepted": True,
    }


def prepare(tmp_path, **kwargs):
    records = inventory(tmp_path, **kwargs)
    path = live.create_live_authority(
        tmp_path / "authority.sqlite3",
        authorization_id="existing-envelope",
        prior_inventory=records,
        execution_review=review(records),
    )
    authority = live.LiveProbe(path)
    return live.prepare_live_probe(
        path, authorization_id="existing-envelope", expected_authority_hash=authority.authority_hash
    )


def run(probe, monkeypatch, calls, mutation=None, close_error=False):
    class Wire(httpx2.MockTransport):
        async def aclose(self):
            if close_error:
                raise RuntimeError("synthetic-private-close-detail")
            await super().aclose()

    async def respond(request):
        provider = "openai" if request.url.host == "api.openai.com" else "anthropic"
        kind = "count" if str(request.url) == COUNT_ENDPOINTS[provider] else "generation"
        calls.append((provider, kind, json.loads(request.content), dict(request.headers)))
        data = count_payload(provider) if kind == "count" else payload(provider)
        if mutation:
            data = mutation(provider, kind, data)
        return httpx2.Response(200, json=data)

    monkeypatch.setattr(live, "network_transport", lambda: Wire(respond))
    return asyncio.run(
        live.run_live_probe(probe, api_keys=KEYS, approved_policy_hash=probe.policy_hash)
    )


def test_exact_four_requests_and_reported_rate_settlement(tmp_path, monkeypatch):
    probe = prepare(tmp_path)
    calls = []
    result = run(probe, monkeypatch, calls)
    assert result["overall_complete"]
    assert [(p, k) for p, k, _, _ in calls] == [
        ("openai", "count"),
        ("openai", "generation"),
        ("anthropic", "count"),
        ("anthropic", "generation"),
    ]
    for provider, kind, body, headers in calls:
        expected = request_body(provider, TRANSCRIPT, LIMITS)
        assert body == (count_request_body(provider, expected) if kind == "count" else expected)
        assert headers["authorization" if provider == "openai" else "x-api-key"] == (
            ("Bearer " if provider == "openai" else "") + KEYS[provider].get_secret_value()
        )
        assert "openai-project" not in headers and "openai-organization" not in headers
    report = result["later_report"]
    assert report["generation_attempts"] == 2 and report["tool_executions"] == 0
    assert len(report["wire_attempts"]) == 4
    assert [r["reserved_nano"] for r in report["generations"]] == [359400, 359400]
    assert [r["observed_nano"] for r in report["generations"]] == [3500, 3500]
    assert report["aggregate_conditional_commitment_nano"] == 9_865_300
    assert not report["account_reports_independently_verified"]
    assert all(k.get_secret_value() not in json.dumps(result) for k in KEYS.values())


def test_supported_tool_call_is_observed_not_executed(tmp_path, monkeypatch):
    probe = prepare(tmp_path)

    def mutation(provider, kind, data):
        return payload(provider, tool="get_purchase_status") if kind == "generation" else data

    result = run(probe, monkeypatch, [], mutation)
    assert result["overall_complete"] and result["later_report"]["tool_executions"] == 0
    assert all(
        r["evidence"]["schema_outcome"] == "valid_tool_arguments"
        for r in result["later_report"]["generations"]
    )


@pytest.mark.parametrize(
    "case", ["refusal", "truncated", "invalid_final", "invalid_arguments", "unavailable_tool"]
)
def test_quality_gate_stops_before_provider2_without_reversing_accounting(
    tmp_path, monkeypatch, case
):
    probe = prepare(tmp_path)
    calls = []

    def mutation(provider, kind, data):
        if kind != "generation":
            return data
        if case == "refusal":
            data["output"][0]["content"] = [{"type": "refusal", "refusal": "Synthetic refusal."}]
        elif case == "truncated":
            data["status"] = "incomplete"
            data["incomplete_details"] = {"reason": "max_output_tokens"}
        elif case == "invalid_final":
            data["output"][0]["content"][0]["text"] = "not JSON"
        else:
            data = payload(
                provider,
                tool="missing_tool" if case == "unavailable_tool" else "get_purchase_status",
                arguments={"unexpected": True},
            )
        return data

    result = run(probe, monkeypatch, calls, mutation)
    report = result["later_report"]
    assert not result["overall_complete"]
    assert [(p, k) for p, k, _, _ in calls] == [("openai", "count"), ("openai", "generation")]
    assert report["state"]["status"] == "stopped"
    assert report["generations"][0]["status"] == "complete"
    assert report["retained_probe_allocation_nano"] == {"openai": 3500, "anthropic": 0}


@pytest.mark.parametrize(
    "case",
    [
        "missing_usage",
        "cache_write",
        "wrong_model",
        "wrong_tier",
        "output_cap",
        "reasoning",
        "reflection",
    ],
)
def test_uncertainty_stops_without_retry_and_keeps_reservation(tmp_path, monkeypatch, case):
    probe = prepare(tmp_path)
    calls = []

    def mutation(provider, kind, data):
        if kind != "generation":
            return data
        if case == "missing_usage":
            data.pop("usage")
        elif case == "cache_write":
            data["usage"]["input_tokens_details"]["cache_write_tokens"] = 1
        elif case == "wrong_model":
            data["model"] = "another-model"
        elif case == "wrong_tier":
            data["service_tier"] = "priority"
        elif case == "output_cap":
            data["usage"]["output_tokens"] = 513
            data["usage"]["total_tokens"] = 523
        elif case == "reasoning":
            data["usage"]["output_tokens_details"]["reasoning_tokens"] = 1
        else:
            data["output"][0]["content"][0]["text"] = KEYS[provider].get_secret_value()
        return data

    result = run(probe, monkeypatch, calls, mutation)
    report = result["later_report"]
    assert not result["overall_complete"]
    assert len(calls) == 2 and report["state"]["status"] == "stopped"
    assert report["retained_probe_allocation_nano"]["openai"] >= 359400
    assert all(k.get_secret_value() not in json.dumps(result) for k in KEYS.values())


@pytest.mark.parametrize("provider,ceiling", [("openai", 272000), ("anthropic", 100000)])
def test_short_regime_count_margin_boundary(provider, ceiling):
    assert live.LiveProbe.estimate(provider, ceiling - 1024) == ceiling * 100 + 512 * 500
    for count in [ceiling - 1023, None, True, -1]:
        with pytest.raises(BudgetDenied):
            live.LiveProbe.estimate(provider, count)


def test_long_count_stops_before_any_generation(tmp_path, monkeypatch):
    p = prepare(tmp_path)
    calls = []
    result = run(
        p,
        monkeypatch,
        calls,
        lambda provider, kind, data: count_payload(provider, 272000) if kind == "count" else data,
    )
    assert not result["overall_complete"]
    assert len(calls) == 1 and result["later_report"]["generation_attempts"] == 0


@pytest.mark.parametrize("point", ["before_observe", "after_observe"])
def test_interruption_accounting_distinguishes_pending_from_observed(tmp_path, monkeypatch, point):
    p = prepare(tmp_path)

    def mutation(provider, kind, data):
        if kind == "generation" and point == "before_observe":
            raise asyncio.CancelledError("private synthetic detail")
        return data

    result = run(p, monkeypatch, [], mutation, close_error=point == "after_observe")
    report = result["later_report"]
    assert not result["overall_complete"] and report["state"]["status"] == "stopped"
    assert report["retained_probe_allocation_nano"]["openai"] == (
        359400 if point == "before_observe" else 3500
    )
    assert report["generations"][0]["status"] == (
        "pending" if point == "before_observe" else "complete"
    )
    assert "private" not in json.dumps(result)


@pytest.mark.parametrize(
    "field",
    [
        "generation_authorization",
        "reviewed_source_hash",
        "reviewed_generation_hashes",
        "prior_inventory_hash",
        "uncached_mode_risk_accepted",
        "reported_rates_accepted",
        "strict_quality_gate_accepted",
    ],
)
def test_review_binding_required_before_creating_authority(tmp_path, field):
    records = inventory(tmp_path)
    value = review(records)
    value[field] = False
    with pytest.raises(BudgetDenied):
        live.create_live_authority(
            tmp_path / "denied.sqlite3",
            authorization_id="existing-envelope",
            prior_inventory=records,
            execution_review=value,
        )
    assert not (tmp_path / "denied.sqlite3").exists()


@pytest.mark.parametrize("known,unknown", [(0, 1239000), (8619300, 0), (1500000000, 1239000)])
def test_prior_inventory_cannot_reset_or_drop_unknown_hold(tmp_path, known, unknown):
    records = inventory(tmp_path, known, unknown)
    with pytest.raises(BudgetDenied):
        live.create_live_authority(
            tmp_path / "denied.sqlite3",
            authorization_id="existing-envelope",
            prior_inventory=records,
            execution_review=review(records),
        )


def test_reenrollment_and_restart_do_not_reset_authority(tmp_path, monkeypatch):
    p = prepare(tmp_path)
    with pytest.raises(BudgetDenied):
        live.prepare_live_probe(
            p.path, authorization_id="existing-envelope", expected_authority_hash=p.authority_hash
        )
    assert run(p, monkeypatch, [])["overall_complete"]
    with pytest.raises(BudgetDenied):
        run(live.LiveProbe(p.path), monkeypatch, [])


def test_wire_claim_rejects_get_changed_body_and_duplicate(tmp_path):
    p = prepare(tmp_path)
    p.activate(p.policy_hash)
    entry = {"slot": 0, "provider": "openai"}
    generation = request_body("openai", TRANSCRIPT, LIMITS)
    body = canonical(count_request_body("openai", generation)).encode()
    p.begin_count(entry, generation, body)
    for request in [
        httpx2.Request("GET", COUNT_ENDPOINTS["openai"], content=body),
        httpx2.Request("POST", COUNT_ENDPOINTS["openai"], content=b"{}"),
        httpx2.Request("POST", ENDPOINTS["openai"], content=body),
    ]:
        with pytest.raises(BudgetDenied):
            p.claim_wire("openai", request)
    req = httpx2.Request("POST", COUNT_ENDPOINTS["openai"], content=body)
    p.claim_wire("openai", req)
    with pytest.raises(sqlite3.IntegrityError):
        p.claim_wire("openai", req)
    assert len(p.report()["wire_attempts"]) == 1


@pytest.mark.parametrize("case", ["wrong_geo", "cache_write", "wrong_tier", "output_cap"])
def test_anthropic_regime_uncertainty_keeps_second_hold(tmp_path, monkeypatch, case):
    p = prepare(tmp_path)
    calls = []

    def mutation(provider, kind, data):
        if provider == "anthropic" and kind == "generation":
            if case == "wrong_geo":
                data["usage"]["inference_geo"] = "us"
            elif case == "cache_write":
                data["usage"]["cache_creation_input_tokens"] = 1
            elif case == "wrong_tier":
                data["usage"]["service_tier"] = "priority"
            else:
                data["usage"]["output_tokens"] = 513
        return data

    result = run(p, monkeypatch, calls, mutation)
    assert not result["overall_complete"] and len(calls) == 4
    assert result["later_report"]["retained_probe_allocation_nano"] == {
        "openai": 3500,
        "anthropic": 359400,
    }
