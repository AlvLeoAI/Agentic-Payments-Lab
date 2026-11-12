import asyncio
import hashlib
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import httpx2
import pytest
from comparison_support import count_payload, payload, responders
from pydantic import ValidationError

from agentic_payments_lab.buyer_agent import comparison as module
from agentic_payments_lab.buyer_agent import comparison_runner as runner
from agentic_payments_lab.buyer_agent.call_budget import BudgetDenied
from agentic_payments_lab.buyer_agent.comparison import (
    Comparison,
    ComparisonLimits,
    initialize_comparison,
)
from agentic_payments_lab.buyer_agent.comparison_protocol import (
    COUNT_ENDPOINTS,
    count_request_body,
    counted_estimate,
    parse_response,
    request_body,
    reservation_estimate,
)
from agentic_payments_lab.domain import canonical, digest


@pytest.fixture
def comparison(tmp_path):
    return asyncio.run(initialize_comparison(tmp_path / "paired"))


def active(comparison):
    comparison.activate(policy_hash=comparison.policy_hash)
    entry = comparison.policy["entries"][0]
    comparison.begin(entry)
    return entry


def counted(comparison, entry, transcript=None):
    transcript = transcript or [{"kind": "user", "text": "synthetic"}]
    generation = request_body(entry["provider"], transcript, comparison.limits)
    body = canonical(generation).encode()
    count = comparison.begin_count(
        entry, generation, canonical(count_request_body(entry["provider"], generation)).encode()
    )
    assert comparison.finish_count(entry, count, count_payload(entry["provider"]))
    return body, transcript, count


def test_full_paired_schedule_native_contracts_and_payment_authority(comparison, monkeypatch):
    calls, counts = [], []
    monkeypatch.setattr(
        httpx2.AsyncHTTPTransport,
        "handle_async_request",
        lambda *_: pytest.fail("No network transport"),
    )
    report = asyncio.run(runner.run_mock_comparison(comparison, responders(calls, counts=counts)))
    assert report["campaign"]["status"] == "finished", {
        k: v for k, v in report.items() if k not in ("requests", "tools")
    }
    assert len(calls) == len(counts) == 162
    assert report["request_attempts_reserved"] == report["verified_complete_responses"] == 162
    assert report["actual_generation_requests"] == 162 and report["unknown_actual_outcomes"] == 0
    assert len(report["episodes"]) == 48 and not report["unattempted"]
    assert len(report["count_attempts"]) == 162 and all(
        c["consumed"] == 1 for c in report["count_attempts"]
    )
    assert report["estimated_total_nano_usd"] == 81 * (8750 + 22500)
    assert report["unresolved_reservation_nano_usd"] == 0 and report["usage_complete"]
    assert report["human_review"] == "pending" and not report["model_quality_established"]
    assert len(comparison.policy["initial_states"]) == 42
    actual_order = list(dict.fromkeys(entry["id"] for entry, _, _ in calls))
    assert actual_order == [e["id"] for e in comparison.policy["entries"]]
    assert [e["provider"] for e in comparison.policy["entries"][:2]] == ["openai", "anthropic"]
    assert [e["provider"] for e in comparison.policy["entries"][16:18]] == ["anthropic", "openai"]
    for provider, arm in report["providers"].items():
        assert arm["request_attempts_reserved"] == arm["verified_complete_responses"] == 81
        assert arm["actual_generation_requests"] == 81
        assert arm["estimated_total_nano_usd"] == 81 * (8750 if provider == "openai" else 22500)
        results = [
            e["result"] for key, e in report["episodes"].items() if key.startswith(provider + ".")
        ]
        assert all(r["safety_passed"] and r["behavior_passed"] for r in results)
        assert sum(r["new_simulated_charges"] for r in results) == 6
        for trial in range(1, 4):

            def result(case, provider=provider, trial=trial):
                return report["episodes"][f"{provider}.trial-{trial}.{case}"]["result"]

            assert (
                result("paid")["after"] == result("replay")["before"] == result("replay")["after"]
            )
            assert result("replay")["new_simulated_charges"] == 0
            assert result("uncertain")["after"]["budget"]["reserved"] == 10000
            assert result("uncertain")["after"]["charges"] == 0
            assert result("failed_delivery")["after"]["charges"] == 1
            assert result("failed_delivery")["after"]["delivery_state"] == "failed"
            assert all(
                result(c)["before"]["binding_valid"] and result(c)["after"]["identity_valid"]
                for c in ("paid", "replay", "uncertain")
            )
    for (entry, generation, headers), (count_entry, count, _) in zip(calls, counts, strict=True):
        assert count_entry == entry and count == count_request_body(entry["provider"], generation)
        assert "authorization" not in headers and "x-api-key" not in headers
        if entry["provider"] == "anthropic":
            assert headers["anthropic-version"] == "2023-06-01"
    for row in report["requests"]:
        expected = request_body(row["provider"], row["transcript"], comparison.limits)
        observed = next(
            data
            for e, data, _ in calls
            if e["slot"] == row["slot"]
            and (
                (data["input"] if row["provider"] == "openai" else data["messages"])
                == (expected["input"] if row["provider"] == "openai" else expected["messages"])
            )
        )
        assert expected == observed
        assert row["reserved_nano"] == reservation_estimate(row["provider"], 10)
    with pytest.raises(BudgetDenied):
        asyncio.run(runner.run_mock_comparison(Comparison(comparison.path), responders([])))


@pytest.mark.parametrize(
    "field,value",
    [
        ("requests_per_episode", 7),
        ("requests_per_provider", 145),
        ("requests_total", 289),
        ("tools_per_episode", 9),
        ("max_request_bytes", 32769),
        ("max_output_tokens", 513),
        ("timeout_seconds", 31),
        ("openai_ceiling_nano", 1500000001),
        ("anthropic_ceiling_nano", 1500000001),
        ("total_ceiling_nano", 3000000001),
        ("requests_total", True),
        ("execution_mode", "live"),
    ],
)
def test_policy_bounds(field, value):
    with pytest.raises(ValidationError):
        ComparisonLimits(**{field: value})


@pytest.mark.parametrize(
    "mutation",
    ["source", "schedule", "missing_metadata", "binding", "immutable_policy", "live", "wrong_hash"],
)
def test_tampering_and_live_activation_before_any_dispatch(comparison, monkeypatch, mutation):
    if mutation == "source":
        monkeypatch.setattr(module, "source_witness", lambda: {})
    elif mutation == "schedule":
        comparison.policy["entries"][0]["goal"]["quality"] = "premium"
    elif mutation == "missing_metadata":
        (comparison.path.parent / "states/openai-trial-1-free/agent.sqlite3").unlink()
    elif mutation == "binding":
        path = comparison.path.parent / "states/openai-trial-1-free/agent.sqlite3"
        with sqlite3.connect(path) as db:
            db.execute("DELETE FROM bindings")
    elif mutation == "immutable_policy":
        with pytest.raises(sqlite3.IntegrityError), comparison.connect(write=True) as db:
            db.execute("UPDATE policy SET data='{}'")
        return
    elif mutation in ("live", "wrong_hash"):
        with pytest.raises(BudgetDenied):
            comparison.activate(
                policy_hash="wrong" if mutation == "wrong_hash" else comparison.policy_hash,
                mode="live" if mutation == "live" else "http_mock_only",
            )
        assert not comparison.report()["requests"]
        return
    with pytest.raises(BudgetDenied):
        asyncio.run(
            runner.run_mock_comparison(comparison, lambda _: pytest.fail("before dispatch"))
        )
    assert not comparison.report()["requests"]


def test_100_concurrent_reservations_share_one_durable_slot(comparison):
    entry = active(comparison)
    body, transcript, count = counted(comparison, entry)

    def reserve(_):
        try:
            return comparison.reserve(entry, body, transcript, count)
        except (BudgetDenied, sqlite3.OperationalError):
            return None

    with ThreadPoolExecutor(max_workers=20) as pool:
        results = list(pool.map(reserve, range(100)))
    assert sum(r is not None for r in results) == 1
    report = comparison.report()
    assert report["request_attempts_reserved"] == 1 and report["verified_complete_responses"] == 0
    assert report["actual_generation_requests"] is None and report["unknown_actual_outcomes"] == 1
    assert report["unresolved_reservation_nano_usd"] == reservation_estimate("openai", 10)


@pytest.mark.parametrize("kind", ["changed_body", "reused", "foreign_id", "missing"])
def test_stale_or_mismatched_count_never_generates(comparison, kind):
    entry = active(comparison)
    body, transcript, count = counted(comparison, entry)
    if kind == "changed_body":
        data = json.loads(body)
        data["input"][0]["content"] = "changed history"
        body = canonical(data).encode()
    elif kind == "reused":
        request = comparison.reserve(entry, body, transcript, count)
        assert comparison.observe(entry, request, parse_response("openai", payload("openai")))
    else:
        count = "absent" if kind == "foreign_id" else None
    with pytest.raises(BudgetDenied):
        comparison.reserve(entry, body, transcript, count)
    assert comparison.report()["request_attempts_reserved"] == int(kind == "reused")


@pytest.mark.parametrize("value", [None, True, "10", -1, 1050001])
def test_bad_count_retains_attempt_and_stops_both_before_generation(comparison, value):
    entry = active(comparison)
    data = request_body("openai", [{"kind": "user", "text": "synthetic"}], comparison.limits)
    count = comparison.begin_count(
        entry, data, canonical(count_request_body("openai", data)).encode()
    )
    assert not comparison.finish_count(entry, count, count_payload("openai", value))
    report = comparison.report()
    assert report["campaign"]["status"] == "stopped" and report["requests"] == []
    assert report["count_attempts"][0]["status"] == "uncertain"
    with pytest.raises(BudgetDenied):
        comparison.begin(comparison.policy["entries"][1])


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
def test_unknown_usage_fences_both_and_retains_native_partial_totals(comparison, provider):
    calls = []

    def mutate(entry, request, data):
        if entry["provider"] == provider:
            data["usage"]["output_tokens"] = None

    report = asyncio.run(runner.run_mock_comparison(comparison, responders(calls, mutate)))
    assert report["campaign"]["status"] == "stopped"
    bad = [r for r in report["requests"] if r["status"] != "complete"]
    assert len(bad) == 1 and bad[0]["provider"] == provider
    assert report["actual_generation_requests"] is None and not report["usage_complete"]
    assert bad[0]["evidence"]["usage"]["native"]["input_tokens"] == 10
    assert bad[0]["evidence"]["usage"]["output_tokens"] is None
    assert report["unresolved_reservation_nano_usd"] == reservation_estimate(provider, 10)
    assert calls[-1][0]["provider"] == provider
    assert report["providers"][provider]["unknown_actual_outcomes"] == 1


def test_known_quality_failure_honest_missing_replay_prerequisite(comparison):
    def factory(entry):
        def respond(request):
            if str(request.url) in COUNT_ENDPOINTS.values():
                return httpx2.Response(200, json=count_payload(entry["provider"]))
            return httpx2.Response(
                200, json=payload(entry["provider"], explanation="I purchased everything.")
            )

        return respond

    report = asyncio.run(runner.run_mock_comparison(comparison, factory))
    assert report["campaign"]["status"] == "finished"
    for key, episode in report["episodes"].items():
        result = episode["result"]
        assert result["safety_passed"] and not result["behavior_passed"]
        assert result["human_explanation_review"] == "pending"
        if key.endswith(".replay"):
            assert result["termination"] == "not_applicable_paid_prerequisite_absent"
            assert episode["request_attempts_reserved"] == 0 and result["after"]["charges"] == 0


@pytest.mark.parametrize(
    "field,amount",
    [
        ("openai_ceiling_nano", reservation_estimate("openai", 10) - 1),
        ("anthropic_ceiling_nano", reservation_estimate("anthropic", 10) - 1),
        ("total_ceiling_nano", reservation_estimate("openai", 10) - 1),
    ],
)
def test_no_budget_transfer_and_counted_reservation_required_before_generation(
    tmp_path, field, amount
):
    comparison = asyncio.run(initialize_comparison(tmp_path / "limited", **{field: amount}))
    calls, counts = [], []
    report = asyncio.run(runner.run_mock_comparison(comparison, responders(calls, counts=counts)))
    assert report["campaign"]["status"] == "stopped"
    assert not [
        entry
        for entry, _, _ in calls
        if entry["provider"] == ("anthropic" if field.startswith("anthropic") else "openai")
    ]
    assert len(counts) == len(calls) + 1
    assert report["unresolved_reservation_nano_usd"] == 0


def test_counted_reservation_matches_exact_request_and_releases_on_usage(comparison):
    entry = active(comparison)
    body, transcript, count = counted(comparison, entry)
    request = comparison.reserve(entry, body, transcript, count)
    row = comparison.report()["count_attempts"][0]
    assert row["estimated_nano"] == counted_estimate("openai", 10) == 901000
    assert row["generation_hash"] == digest(json.loads(body))
    assert comparison.report()["requests"][0]["body_hash"] == hashlib.sha256(body).hexdigest()
    assert comparison.report()["unresolved_reservation_nano_usd"] == reservation_estimate(
        "openai", 10
    )
    assert comparison.observe(entry, request, parse_response("openai", payload("openai")))
    report = comparison.report()
    assert (
        report["estimated_total_nano_usd"] == 8750
        and report["unresolved_reservation_nano_usd"] == 0
    )


def test_counted_admission_needs_no_context_window_floor(tmp_path):
    comparison = asyncio.run(
        initialize_comparison(
            tmp_path / "counted-affordable",
            openai_ceiling_nano=10000000,
            anthropic_ceiling_nano=10000000,
            total_ceiling_nano=20000000,
        )
    )
    calls, counts = [], []
    report = asyncio.run(runner.run_mock_comparison(comparison, responders(calls, counts=counts)))
    assert report["campaign"]["status"] == "finished"
    assert len(calls) == len(counts) == 162
    assert report["unresolved_reservation_nano_usd"] == 0
