import asyncio
import copy
import json
import sqlite3
import subprocess
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx2
import pytest
from comparison_support import count_payload, payload, responders
from test_comparison import active, counted
from test_comparison import comparison as comparison

from agentic_payments_lab.buyer_agent.call_budget import BudgetDenied
from agentic_payments_lab.buyer_agent.comparison import Comparison
from agentic_payments_lab.buyer_agent.comparison_protocol import (
    COUNT_ENDPOINTS,
    parse_response,
    reservation_estimate,
)
from agentic_payments_lab.buyer_agent.comparison_runner import run_mock_comparison


def payment_facts(comparison, provider="openai"):
    root = comparison.path.parent / f"states/{provider}-trial-1-paid"
    with sqlite3.connect((root / "payments.sqlite3").as_uri() + "?mode=ro", uri=True) as db:
        attempts = db.execute("SELECT count(*) FROM attempts").fetchone()[0]
        outcomes = [json.loads(r[0]) for r in db.execute("SELECT result FROM authorizations")]
    return attempts, sum(r["status"] == "settled" for r in outcomes)


@pytest.mark.parametrize(
    "method,after",
    [
        ("begin_count", False),
        ("begin_count", True),
        ("finish_count", False),
        ("finish_count", True),
        ("reserve", True),
        ("observe", False),
        ("observe", True),
        ("tool_intent", True),
        ("tool_result", False),
        ("tool_result", True),
        ("finish", False),
        ("finish", True),
    ],
)
def test_persistence_failure_never_restarts_or_replaces(comparison, monkeypatch, method, after):
    original = getattr(comparison, method)

    def failure(*args, **kwargs):
        if method == "tool_result" and args[-1].get("payment_state") != "settled":
            return original(*args, **kwargs)
        if after:
            original(*args, **kwargs)
        raise sqlite3.OperationalError("PRIVATE_PERSISTENCE_SENTINEL")

    monkeypatch.setattr(comparison, method, failure)
    calls = []
    report = asyncio.run(run_mock_comparison(comparison, responders(calls)))
    assert report["campaign"]["status"] == "stopped"
    assert "PRIVATE_PERSISTENCE_SENTINEL" not in json.dumps(report)
    if method == "reserve":
        assert calls == [] and report["request_attempts_reserved"] == 1
        assert report["actual_generation_requests"] is None
        assert report["unresolved_reservation_nano_usd"] == reservation_estimate("openai", 10)
    if method == "observe":
        assert len(calls) == 1
        assert report["verified_complete_responses"] == int(after)
    if method == "tool_result":
        assert payment_facts(comparison) == (1, 1)
        result = report["episodes"]["openai.trial-1.paid"]["result"]
        assert (
            result["after"]["payment_state"] == "settled"
            and result["after"]["delivery_state"] == "delivered"
        )
    with pytest.raises(BudgetDenied):
        asyncio.run(
            run_mock_comparison(Comparison(comparison.path), lambda _: pytest.fail("No restart"))
        )


@pytest.mark.parametrize("point", ["before_response", "partial_usage", "post_payment"])
def test_external_cancellation_retains_usage_and_authority(comparison, point):
    calls, counts = [], []
    factory = responders(calls, counts=counts)

    async def exercise():
        reached = asyncio.Event()

        def wrapped(entry):
            normal = factory(entry)
            ordinal = 0

            async def respond(request):
                nonlocal ordinal
                if str(request.url) not in COUNT_ENDPOINTS.values():
                    ordinal += 1
                    hit = (
                        entry["slot"] == 0 and ordinal == (1 if point == "before_response" else 3)
                        if point != "post_payment"
                        else entry["id"] == "openai.trial-1.paid" and ordinal == 5
                    )
                    if hit:
                        reached.set()
                        await asyncio.Event().wait()
                return await normal(request)

            return respond

        task = asyncio.create_task(run_mock_comparison(comparison, wrapped))
        await asyncio.wait_for(reached.wait(), 25)
        started = time.monotonic()
        task.cancel("PRIVATE_CANCEL_SENTINEL")
        with pytest.raises(asyncio.CancelledError) as caught:
            await asyncio.wait_for(task, 3)
        assert time.monotonic() - started < 3
        assert "PRIVATE_CANCEL_SENTINEL" not in "".join(traceback.format_exception(caught.value))

    asyncio.run(exercise())
    report = Comparison(comparison.path).report()
    assert (
        report["campaign"]["status"] == "stopped" and report["actual_generation_requests"] is None
    )
    assert report["unknown_actual_outcomes"] == 1
    assert report["unresolved_reservation_nano_usd"] == reservation_estimate("openai", 10)
    key = "openai.trial-1.paid" if point == "post_payment" else "openai.trial-1.free"
    episode = report["episodes"][key]
    assert episode["result"]["termination"] == "cancelled"
    assert episode["known_partial_prompt_tokens"] == (
        40 if point == "post_payment" else 20 if point == "partial_usage" else 0
    )
    assert episode["estimated_total_nano_usd"] is None
    if point == "post_payment":
        assert payment_facts(comparison) == (1, 1)
        assert episode["result"]["after"]["payment_state"] == "settled"
        assert episode["result"]["after"]["delivery_state"] == "delivered"
    for _ in range(2):
        with pytest.raises(BudgetDenied):
            asyncio.run(
                run_mock_comparison(Comparison(comparison.path), lambda _: pytest.fail("No resend"))
            )


@pytest.mark.parametrize(
    "point",
    [
        "activation",
        "count_reservation",
        "count_observed",
        "reservation",
        "dispatch",
        "usage",
        "tool_intent",
        "post_payment",
        "result",
    ],
)
def test_abrupt_exit_fences_both_arms(comparison, point):
    child = subprocess.run(
        [
            sys.executable,
            "-B",
            str(Path(__file__).with_name("comparison_worker.py")),
            str(comparison.path.parent),
            point,
        ],
        capture_output=True,
        text=True,
        timeout=40,
    )
    assert child.returncode == 87, (child.stdout, child.stderr)
    report = Comparison(comparison.path).report()
    assert report["campaign"]["status"] == "running" and report["blocked"]
    if point in ("reservation", "dispatch"):
        assert (
            report["request_attempts_reserved"] == 1 and report["verified_complete_responses"] == 0
        )
        assert report["actual_generation_requests"] is None
        assert report["unresolved_reservation_nano_usd"] == reservation_estimate("openai", 10)
    if point in ("count_reservation", "count_observed"):
        assert len(report["count_attempts"]) == 1 and not report["requests"]
    if point == "post_payment":
        assert payment_facts(comparison) == (1, 1)
    with pytest.raises(BudgetDenied):
        asyncio.run(
            run_mock_comparison(Comparison(comparison.path), lambda _: pytest.fail("No recovery"))
        )


def test_processes_compete_for_shared_owner(comparison):
    root = comparison.path.parent
    workers = [
        subprocess.Popen(
            [
                sys.executable,
                "-B",
                str(Path(__file__).with_name("comparison_worker.py")),
                str(root),
                "compete",
                str(i),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for i in range(4)
    ]
    try:
        deadline = time.monotonic() + 25
        while not all((root / f"ready-{i}").exists() for i in range(4)):
            assert time.monotonic() < deadline and all(p.poll() is None for p in workers)
            time.sleep(0.02)
        (root / "go").touch()
        outputs = [p.communicate(timeout=25) for p in workers]
        assert all(p.returncode == 0 for p in workers), outputs
        assert sum(json.loads(out)["claimed"] for out, _ in outputs) == 1
        assert Comparison(comparison.path).report()["blocked"]
    finally:
        for p in workers:
            if p.poll() is None:
                p.kill()
            p.wait(timeout=5)


def test_100_identical_usage_settlements_are_idempotent_and_conflict_fences(comparison):
    entry = active(comparison)
    body, transcript, count = counted(comparison, entry)
    request_id = comparison.reserve(entry, body, transcript, count)
    evidence = parse_response("openai", payload("openai"))

    def observe(_):
        return comparison.observe(entry, request_id, copy.deepcopy(evidence))

    with ThreadPoolExecutor(max_workers=8) as pool:
        assert all(pool.map(observe, range(100)))
    report = comparison.report()
    assert report["request_attempts_reserved"] == report["verified_complete_responses"] == 1
    assert (
        report["estimated_total_nano_usd"] == 8750
        and report["unresolved_reservation_nano_usd"] == 0
    )
    before = report["requests"]
    conflicting = copy.deepcopy(evidence)
    conflicting["usage"]["native"]["output_tokens"] = 6
    with pytest.raises(BudgetDenied):
        comparison.observe(entry, request_id, conflicting)
    assert comparison.report()["campaign"]["status"] == "stopped"
    assert comparison.report()["requests"] == before
    assert comparison.report()["estimated_total_nano_usd"] == 8750
    assert comparison.observe(entry, request_id, evidence)
    assert comparison.report()["campaign"]["status"] == "stopped"


@pytest.mark.parametrize("stop", ["refusal", "truncated"])
def test_refusal_or_truncation_settles_actual_usage_and_never_retries(comparison, stop):
    calls = []

    def factory(entry):
        def respond(request):
            provider = entry["provider"]
            if str(request.url) in COUNT_ENDPOINTS.values():
                return httpx2.Response(200, json=count_payload(provider))
            calls.append(entry["id"])
            result = payload(provider)
            if provider == "anthropic":
                result["stop_reason"] = "refusal" if stop == "refusal" else "max_tokens"
                result["content"][0]["text"] = "Refused" if stop == "refusal" else '{"explanation":'
            elif stop == "refusal":
                result["output"][0]["content"] = [{"type": "refusal", "refusal": "No."}]
            else:
                result["status"] = "incomplete"
                result["incomplete_details"] = {"reason": "max_output_tokens"}
                result["output"][0]["content"][0]["text"] = '{"explanation":'
            return httpx2.Response(200, json=result)

        return respond

    report = asyncio.run(run_mock_comparison(comparison, factory))
    assert report["campaign"]["status"] == "finished"
    assert len(calls) == len(set(calls)) == 42  # Six replay prerequisites are honestly absent.
    assert report["estimated_total_nano_usd"] == 21 * (8750 + 22500)
    assert report["known_partial_output_tokens"] == 42 * 5  # Never assume all 512 were billed.
    assert report["unresolved_reservation_nano_usd"] == 0
    for key, episode in report["episodes"].items():
        if not key.endswith(".replay"):
            assert episode["result"]["termination"] == stop
            assert episode["verified_complete_responses"] == 1


def test_control_error_preserves_complete_usage_and_unresolved_exposure(comparison):
    entry = active(comparison)
    body, transcript, count = counted(comparison, entry)
    request = comparison.reserve(entry, body, transcript, count)
    data = payload("openai")
    data["output"].append({"type": "server_tool_use"})
    assert not comparison.observe(entry, request, parse_response("openai", data))
    report = comparison.report()
    assert report["usage_complete"] and report["pricing_identity_verified"]
    assert not report["responses_valid"] and not report["exposure_resolved"]
    assert report["known_observed_estimate_nano_usd"] == 8750
    assert report["estimated_total_nano_usd"] is None
    assert report["unresolved_exposure_nano_usd"] == reservation_estimate("openai", 10)
    assert report["verified_complete_responses"] == 0


def test_observed_overrun_never_understates_outstanding_exposure(comparison):
    entry = active(comparison)
    body, transcript, count = counted(comparison, entry)
    request = comparison.reserve(entry, body, transcript, count)
    evidence = parse_response("openai", payload("openai"))
    evidence["usage"]["prompt_tokens"] = 2000
    observed = 2000 * 500 + 5 * 750
    assert not comparison.observe(entry, request, evidence)
    report = comparison.report()
    assert report["campaign"]["status"] == "stopped"
    assert report["unresolved_reservation_nano_usd"] == reservation_estimate("openai", 10)
    assert report["unresolved_exposure_nano_usd"] == observed
    assert report["known_observed_excess_nano_usd"] == observed - reservation_estimate("openai", 10)


def test_timeout_retains_generation_hold_and_no_retry(tmp_path):
    # Lower a valid policy at fresh initialization through the public constructor.
    from agentic_payments_lab.buyer_agent.comparison import initialize_comparison

    limited = asyncio.run(initialize_comparison(tmp_path / "timeout", timeout_seconds=1))
    calls = []

    def factory(entry):
        async def respond(request):
            if str(request.url) in COUNT_ENDPOINTS.values():
                return httpx2.Response(200, json=count_payload(entry["provider"]))
            calls.append(1)
            await asyncio.Event().wait()

        return respond

    report = asyncio.run(run_mock_comparison(limited, factory))
    assert calls == [1] and report["campaign"]["status"] == "stopped"
    assert report["episodes"]["openai.trial-1.free"]["result"]["termination"] == "timeout"
    assert report["unresolved_exposure_nano_usd"] == reservation_estimate("openai", 10)


def test_exception_and_model_secret_markers_never_persist(comparison, capsys):
    marker = "PRIVATE_TRANSPORT_SENTINEL"

    def factory(entry):
        def respond(request):
            if str(request.url) in COUNT_ENDPOINTS.values():
                return httpx2.Response(200, json=count_payload(entry["provider"]))
            raise RuntimeError(marker)

        return respond

    report = asyncio.run(run_mock_comparison(comparison, factory))
    assert marker not in json.dumps(report)
    captured = capsys.readouterr()
    assert marker not in captured.out + captured.err
    for path in comparison.path.parent.rglob("*"):
        if path.is_file():
            assert marker.encode() not in path.read_bytes()
