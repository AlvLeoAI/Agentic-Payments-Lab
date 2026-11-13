"""Synthetic rehearsal only. No credentials, native network or authority tools."""

import asyncio
import hashlib
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import httpx2
import pytest
from comparison_support import count_payload, payload

from agentic_payments_lab.buyer_agent.call_budget import BudgetDenied
from agentic_payments_lab.buyer_agent.comparison_protocol import COUNT_ENDPOINTS, counted_estimate
from agentic_payments_lab.buyer_agent.generation_probe import (
    Probe,
    create_mock_authority,
    prepare_mock_probe,
    run_mock_probe,
)


def inventory(tmp_path, known=8_619_300, unresolved=1_239_000):
    path = tmp_path / "synthetic-history.json"
    path.write_text(json.dumps({"known_estimate": known, "unresolved": unresolved}))
    return [
        {
            "provider": "openai",
            "source_file": str(path),
            "source_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "known_estimated_nano": known,
            "unresolved_nano": unresolved,
        }
    ]


def authority(tmp_path, **kwargs):
    records = inventory(tmp_path, **kwargs)
    path = create_mock_authority(
        tmp_path / "shared.sqlite3", authorization_id="existing-envelope", prior_inventory=records
    )
    return Probe(path)


def prepare(tmp_path, **kwargs):
    a = authority(tmp_path, **kwargs)
    return prepare_mock_probe(
        a.path, authorization_id="existing-envelope", expected_authority_hash=a.authority_hash
    )


def transports(calls, mutation=None):
    result = {}
    for provider in ("openai", "anthropic"):

        async def respond(request, provider=provider):
            kind = "count" if str(request.url) in COUNT_ENDPOINTS.values() else "generation"
            calls.append((provider, kind, json.loads(request.content)))
            data = (
                count_payload(provider)
                if kind == "count"
                else payload(provider, tool="get_purchase_status", arguments={})
            )
            if mutation:
                data = mutation(provider, kind, data)
            return httpx2.Response(200, json=data)

        result[provider] = httpx2.MockTransport(respond)
    return result


def run(p, calls, mutation=None):
    return asyncio.run(
        run_mock_probe(
            p, transports=transports(calls, mutation), approved_policy_hash=p.policy_hash
        )
    )


def test_two_generations_no_tools_and_shared_prior_exposure(tmp_path):
    p = prepare(tmp_path)
    before = p.report()
    assert before["aggregate_conditional_commitment_nano"] == 9_858_300
    calls = []
    result = run(p, calls)
    assert result["overall_complete"]
    report = result["caller"]["report"]
    assert report == result["later_report"]
    assert [(p, k) for p, k, _ in calls] == [
        ("openai", "count"),
        ("openai", "generation"),
        ("anthropic", "count"),
        ("anthropic", "generation"),
    ]
    assert report["generation_attempts"] == 2 and report["tool_executions"] == 0
    assert all(
        r["evidence"]["call"]["name"] == "get_purchase_status" for r in report["generations"]
    )
    assert report["prior_exposure_nano"] == {"openai": 9_858_300, "anthropic": 0}
    assert report["aggregate_conditional_commitment_nano"] == 9_889_550
    assert not report["live_generation_ready"] and not report["billing_verified"]
    for provider, kind, body in calls:
        if kind == "generation":
            assert body["max_output_tokens" if provider == "openai" else "max_tokens"] == 512
    assert len(list(tmp_path.glob("*.sqlite3"))) == 1


@pytest.mark.parametrize(
    "failure",
    ["missing_usage", "wrong_model", "wrong_tier", "over_cap", "bad_count", "cache_regime"],
)
def test_failure_retains_exposure_and_stops_other_arm(tmp_path, failure):
    p = prepare(tmp_path)
    calls = []

    def mutate(provider, kind, data):
        if provider != "openai":
            return data
        if kind == "count" and failure == "bad_count":
            return {"input_tokens": True, "object": "response.input_tokens"}
        if kind == "generation":
            if failure == "missing_usage":
                data.pop("usage")
            elif failure == "wrong_model":
                data["model"] = "unexpected-model"
            elif failure == "wrong_tier":
                data["service_tier"] = "priority"
            elif failure == "over_cap":
                data["usage"]["output_tokens"] = 513
                data["usage"]["total_tokens"] = 523
            elif failure == "cache_regime":
                data["usage"]["output_tokens_details"]["reasoning_tokens"] = 1
        return data

    result = run(p, calls, mutate)
    assert not result["overall_complete"] and result["caller"]["outcome"] == "incomplete"
    assert all(provider == "openai" for provider, _, _ in calls)
    report = result["later_report"]
    assert report["state"]["status"] == "stopped"
    assert report["retained_probe_allocation_nano"]["openai"] >= (
        counted_estimate("openai", 10) if report["generation_attempts"] else 0
    )
    assert report["retained_probe_allocation_nano"]["anthropic"] == 0
    assert report["tool_executions"] == 0
    with pytest.raises(BudgetDenied):
        run(Probe(p.path), [])


def test_claimed_authority_cannot_enroll_again_or_reset(tmp_path):
    p = prepare(tmp_path)
    with pytest.raises(BudgetDenied):
        prepare_mock_probe(
            p.path, authorization_id="existing-envelope", expected_authority_hash=p.authority_hash
        )
    with pytest.raises(FileExistsError):
        create_mock_authority(
            p.path, authorization_id="another-envelope", prior_inventory=p.policy["prior_inventory"]
        )
    with p.connect(write=True) as db:
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("DELETE FROM allocation")


def test_mismatched_authority_identity_hash_rejected(tmp_path):
    p = authority(tmp_path)
    for identity, witness in [("wrong", p.authority_hash), ("existing-envelope", "0" * 64)]:
        with pytest.raises(BudgetDenied):
            prepare_mock_probe(p.path, authorization_id=identity, expected_authority_hash=witness)
    with p.connect() as db:
        assert db.execute("SELECT count(*) FROM allocation").fetchone()[0] == 0


@pytest.mark.parametrize("value", [None, [], [{"provider": "openai"}]])
def test_missing_inventory_rejected(tmp_path, value):
    with pytest.raises(BudgetDenied):
        create_mock_authority(tmp_path / "a.sqlite3", authorization_id="a", prior_inventory=value)
    assert not (tmp_path / "a.sqlite3").exists()


@pytest.mark.parametrize(
    "field,value",
    [
        ("known_estimated_nano", True),
        ("unresolved_nano", -1),
        ("known_estimated_nano", 1_500_000_001),
        ("source_sha256", "0" * 64),
    ],
)
def test_invalid_or_unaffordable_inventory_rejected(tmp_path, field, value):
    records = inventory(tmp_path)
    records[0][field] = value
    with pytest.raises(BudgetDenied):
        create_mock_authority(tmp_path / "a.sqlite3", authorization_id="a", prior_inventory=records)


def test_changed_inventory_fails_before_attempt(tmp_path):
    p = prepare(tmp_path)
    (tmp_path / "synthetic-history.json").write_text("{}")
    calls = []
    with pytest.raises(BudgetDenied):
        run(p, calls)
    assert calls == []


def test_exact_mock_transport_required_before_owner_claim(tmp_path):
    p = prepare(tmp_path)
    with pytest.raises(BudgetDenied):
        asyncio.run(
            run_mock_probe(
                p,
                transports={"openai": object(), "anthropic": object()},
                approved_policy_hash=p.policy_hash,
            )
        )
    assert p.report()["state"]["status"] == "prepared"


def test_only_one_concurrent_authority_allocation(tmp_path):
    a = authority(tmp_path)

    failures = []

    def claim(_):
        try:
            prepare_mock_probe(
                a.path,
                authorization_id="existing-envelope",
                expected_authority_hash=a.authority_hash,
            )
            return True
        except (BudgetDenied, sqlite3.OperationalError) as error:
            failures.append(type(error).__name__ + ": " + str(error))
            return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(claim, range(2)))
    assert sum(outcomes) == 1, failures


def test_caller_failure_after_ack_cannot_be_replaced_by_later_report(tmp_path, monkeypatch):
    p = prepare(tmp_path)
    original = p.report
    count = 0

    def fail_once():
        nonlocal count
        count += 1
        if count == 1:
            raise OSError("synthetic-private-exception")
        return original()

    monkeypatch.setattr(p, "report", fail_once)
    result = run(p, [])
    assert result["later_report"]["run_complete"]
    assert not result["overall_complete"]
    assert result["caller"] == {"outcome": "incomplete", "reason": "failed", "report": None}
    assert "synthetic-private-exception" not in json.dumps(result)


def test_transport_cancellation_retains_generation_hold(tmp_path):
    p = prepare(tmp_path)

    def mutation(provider, kind, data):
        if kind == "generation":
            raise asyncio.CancelledError("synthetic-secret")
        return data

    result = run(p, [], mutation)
    assert result["caller"]["reason"] == "cancelled"
    assert result["later_report"]["retained_probe_allocation_nano"]["openai"] == counted_estimate(
        "openai", 10
    )
    assert "synthetic-secret" not in json.dumps(result)


def test_counted_hold_not_context_floor_and_prior_cannot_reset(tmp_path):
    p = prepare(tmp_path, known=1_499_500_000, unresolved=0)
    calls = []
    result = run(p, calls)
    assert not result["overall_complete"]
    assert [(provider, kind) for provider, kind, _ in calls] == [("openai", "count")]
    assert result["later_report"]["generation_attempts"] == 0
    assert result["later_report"]["aggregate_conditional_commitment_nano"] == 1_499_500_000


def test_small_count_admitted_even_when_context_backstop_would_not_fit(tmp_path):
    p = prepare(tmp_path, known=1_498_000_000, unresolved=0)
    assert run(p, [])["overall_complete"]


def test_old_authority_version_rejected_without_migration(tmp_path):
    p = authority(tmp_path)
    with sqlite3.connect(p.path) as db:
        db.execute("PRAGMA user_version=1")
    before = p.path.read_bytes()
    with pytest.raises(BudgetDenied):
        Probe(p.path)
    assert p.path.read_bytes() == before


@pytest.mark.parametrize("target", ["openai", "anthropic"])
def test_total_output_above_512_stops_with_counted_hold(tmp_path, target):
    p = prepare(tmp_path)

    def mutation(provider, kind, data):
        if provider == target and kind == "generation":
            data["usage"]["output_tokens"] = 513
            if provider == "openai":
                data["usage"]["total_tokens"] = 523
        return data

    result = run(p, [], mutation)
    assert not result["overall_complete"]
    report = result["later_report"]
    assert report["state"]["status"] == "stopped"
    assert report["retained_probe_allocation_nano"][target] >= counted_estimate(target, 10)
