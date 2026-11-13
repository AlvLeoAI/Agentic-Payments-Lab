"""Production admission/serialization path, with network replaced under OS isolation."""

import asyncio
import hashlib
import json
import logging
import sqlite3
import subprocess
import sys
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx2
import pytest
from comparison_support import nested_reflection, payload, responders
from preflight_support import KEYS, reply
from test_comparison import counted
from test_comparison_recovery import payment_facts

from agentic_payments_lab.buyer_agent import comparison_live as live
from agentic_payments_lab.buyer_agent import comparison_preflight as preflight_module
from agentic_payments_lab.buyer_agent import comparison_wire as wire_module
from agentic_payments_lab.buyer_agent.call_budget import BudgetDenied
from agentic_payments_lab.buyer_agent.comparison import Comparison, initialize_comparison
from agentic_payments_lab.buyer_agent.comparison_preflight import (
    initialize_preflight,
    run_authenticated_preflight,
)
from agentic_payments_lab.buyer_agent.comparison_protocol import (
    COUNT_ENDPOINTS,
    ENDPOINTS,
    reservation_estimate,
)
from agentic_payments_lab.domain import digest


def prepare_review(tmp_path, monkeypatch):
    preflight = initialize_preflight(tmp_path / "preflight", mode="authenticated_preflight")

    def send(request):
        provider = "openai" if request.url.host == "api.openai.com" else "anthropic"
        return reply(provider, request)

    monkeypatch.setattr(preflight_module, "network_transport", lambda: httpx2.MockTransport(send))
    report = asyncio.run(
        run_authenticated_preflight(
            preflight,
            api_keys=KEYS,
            approved_policy_hash=preflight.policy_hash,
            reviewed_source_hash=digest(preflight.policy["source"]),
        )
    )
    assert report["state"]["status"] == "finished"
    return {
        "preflight_path": str(preflight.path),
        "preflight_policy_hash": preflight.policy_hash,
        "preflight_result_hash": digest(report),
        "reviewed_source_hash": digest(preflight.policy["source"]),
        "generation_authorization": "approve_fresh_matched_simulator_generation",
        "conditional_backstop_accepted": True,
        "account_pricing_scope_reviewed": True,
    }


@pytest.fixture
def enrolled(tmp_path, monkeypatch):
    review = prepare_review(tmp_path, monkeypatch)
    return asyncio.run(
        live.initialize_live_comparison(tmp_path / "paired", generation_review=review)
    )


def run(comparison):
    return asyncio.run(
        live.run_live_comparison(
            comparison, api_keys=KEYS, approved_enrollment_hash=comparison.policy_hash
        )
    )


def assert_no_keys(comparison, extra=""):
    markers = [key.get_secret_value().encode() for key in KEYS.values()]
    assert not any(m in extra.encode() for m in markers)
    for file in comparison.path.parent.rglob("*"):
        if file.is_file():
            assert not any(m in file.read_bytes() for m in markers), file.name


def scripted_wires(comparison, monkeypatch, wrap=None):
    calls, counts, requests = [], [], []
    factory = responders(calls, counts=counts)

    def construct():
        entry = comparison.policy["entries"][comparison.report()["campaign"]["next_slot"]]
        normal = factory(entry)

        async def send(request):
            records = comparison.report()["wire_attempts"]
            assert records[-1]["state"] == "dispatch_authorized"
            assert records[-1]["payload_hash"] == hashlib.sha256(request.content).hexdigest()
            name = "authorization" if entry["provider"] == "openai" else "x-api-key"
            assert request.headers[name].endswith(KEYS[entry["provider"]].get_secret_value())
            assert all(
                KEYS[p].get_secret_value() not in request.headers.get(name, "")
                for p in KEYS
                if p != entry["provider"]
            )
            requests.append((entry, str(request.url)))
            return await wrap(entry, request, normal) if wrap else await normal(request)

        return httpx2.MockTransport(send)

    monkeypatch.setattr(live, "network_transport", construct)
    return calls, counts, requests


def test_full_live_path_uses_same_frozen_schedule_and_direct_authority(enrolled, monkeypatch):
    calls, counts, requests = scripted_wires(enrolled, monkeypatch)
    report = run(enrolled)
    assert report["mode"] == "live_simulator" and report["run_complete"]
    assert len(calls) == len(counts) == report["verified_complete_responses"] == 162
    assert len(requests) == len(report["wire_attempts"]) == 324
    assert (
        report["estimated_total_nano_usd"] == 81 * (8750 + 22500)
        and report["unresolved_exposure_nano_usd"] == 0
    )
    for provider in KEYS:
        assert payment_facts(enrolled, provider) == (1, 1)
        arm = report["providers"][provider]
        assert arm["request_attempts_reserved"] == 81
        for trial in (1, 2, 3):
            paid = report["episodes"][f"{provider}.trial-{trial}.paid"]["result"]
            replay = report["episodes"][f"{provider}.trial-{trial}.replay"]["result"]
            assert paid["after"] == replay["after"] and replay["new_simulated_charges"] == 0
    assert_no_keys(enrolled, json.dumps(report))


@pytest.mark.parametrize("kind", ["400", "redirect", "partial", "secret", "exception", "close"])
def test_failed_generation_never_retries_or_releases_unknown_exposure(enrolled, monkeypatch, kind):
    class Stream(httpx2.AsyncByteStream):
        async def __aiter__(self):
            if kind == "partial":
                yield b'{"usage":'
                raise RuntimeError(KEYS["openai"].get_secret_value())
            yield json.dumps(payload("openai")).encode()

        async def aclose(self):
            if kind == "close":
                raise RuntimeError(KEYS["openai"].get_secret_value())

    async def mutate(entry, request, normal):
        if str(request.url) in COUNT_ENDPOINTS.values():
            return await normal(request)
        if kind == "exception":
            raise RuntimeError(KEYS["openai"].get_secret_value())
        if kind in ("partial", "close"):
            return httpx2.Response(200, stream=Stream())
        if kind == "secret":
            return httpx2.Response(
                200, json=payload("openai", explanation=KEYS["openai"].get_secret_value())
            )
        return httpx2.Response(
            400 if kind == "400" else 307,
            headers={"location": ENDPOINTS["anthropic"]},
            json={"error": "input too long"},
        )

    _, _, requests = scripted_wires(enrolled, monkeypatch, mutate)
    report = run(enrolled)
    assert len(requests) == 2 and report["campaign"]["status"] == "stopped"
    assert report["request_attempts_reserved"] == 1
    assert report["unresolved_exposure_nano_usd"] == (
        0 if kind == "close" else reservation_estimate("openai", 10)
    )
    assert report["verified_complete_responses"] == int(kind == "close")
    assert payment_facts(enrolled) == (0, 0)
    with pytest.raises(BudgetDenied):
        run(Comparison(enrolled.path))
    assert len(requests) == 2
    assert_no_keys(enrolled, json.dumps(report))


@pytest.mark.parametrize(
    "provider,arguments", [("openai", False), ("openai", True), ("anthropic", False)]
)
def test_nested_json_credential_reflection_stops_before_retention(
    enrolled, monkeypatch, provider, arguments
):
    secret = KEYS[provider].get_secret_value()
    data = nested_reflection(provider, secret, arguments=arguments)

    async def mutate(entry, request, normal):
        if entry["provider"] != provider or str(request.url) in COUNT_ENDPOINTS.values():
            return await normal(request)
        return httpx2.Response(200, json=data)

    _, _, requests = scripted_wires(enrolled, monkeypatch, mutate)
    report = run(enrolled)
    generations = [entry["provider"] for entry, url in requests if url in ENDPOINTS.values()]
    assert generations.count(provider) == 1 and generations[-1] == provider
    assert report["campaign"]["status"] == "stopped"
    assert report["verified_complete_responses"] == len(generations) - 1
    assert report["unresolved_exposure_nano_usd"] == reservation_estimate(provider, 10)
    with pytest.raises(BudgetDenied):
        run(Comparison(enrolled.path))
    assert_no_keys(enrolled, json.dumps(report))


@pytest.mark.parametrize("point", ["before_response", "partial_usage", "post_payment"])
def test_live_cancellation_retains_usage_finance_and_shared_stop(enrolled, monkeypatch, point):
    async def scenario():
        reached = asyncio.Event()
        ordinals = {}

        async def mutate(entry, request, normal):
            if str(request.url) in ENDPOINTS.values():
                ordinal = ordinals[entry["id"]] = ordinals.get(entry["id"], 0) + 1
                hit = (
                    (entry["id"] == "openai.trial-1.paid" and ordinal == 5)
                    if point == "post_payment"
                    else (
                        entry["slot"] == 0 and ordinal == (1 if point == "before_response" else 3)
                    )
                )
                if hit:
                    reached.set()
                    await asyncio.Event().wait()
            return await normal(request)

        scripted_wires(enrolled, monkeypatch, mutate)
        task = asyncio.create_task(
            live.run_live_comparison(
                enrolled, api_keys=KEYS, approved_enrollment_hash=enrolled.policy_hash
            )
        )
        await asyncio.wait_for(reached.wait(), 25)
        task.cancel(KEYS["openai"].get_secret_value())
        with pytest.raises(asyncio.CancelledError) as error:
            await asyncio.wait_for(task, 3)
        assert_no_keys(enrolled, "".join(traceback.format_exception(error.value)))

    asyncio.run(scenario())
    report = Comparison(enrolled.path).report()
    assert report["campaign"]["status"] == "stopped" and report["unknown_actual_outcomes"] == 1
    assert report["unresolved_exposure_nano_usd"] == reservation_estimate("openai", 10)
    key = "openai.trial-1.paid" if point == "post_payment" else "openai.trial-1.free"
    episode = report["episodes"][key]
    assert episode["known_partial_prompt_tokens"] == (
        40 if point == "post_payment" else 20 if point == "partial_usage" else 0
    )
    assert not episode["usage_complete"] and episode["estimated_total_nano_usd"] is None
    if point == "post_payment":
        assert payment_facts(enrolled) == (1, 1)
        assert episode["result"]["after"]["payment_state"] == "settled"
        assert episode["result"]["after"]["delivery_state"] == "delivered"
    with pytest.raises(BudgetDenied):
        run(Comparison(enrolled.path))


def test_one_durable_dispatch_permission_under_concurrency(enrolled):
    enrolled.activate(policy_hash=enrolled.policy_hash, mode="live_simulator")
    entry = enrolled.policy["entries"][0]
    enrolled.begin(entry)
    body, transcript, count = counted(enrolled, entry)
    enrolled.reserve(entry, body, transcript, count)
    request = httpx2.Request("POST", ENDPOINTS["openai"], content=body)

    def claim(_):
        try:
            return enrolled.claim_wire("openai", request)
        except (BudgetDenied, sqlite3.IntegrityError, sqlite3.OperationalError):
            return None

    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(x is not None for x in pool.map(claim, range(20))) == 1
    assert len(enrolled.report()["wire_attempts"]) == 1
    with pytest.raises(BudgetDenied):
        enrolled.claim_wire("openai", httpx2.Request("POST", ENDPOINTS["openai"], content=b"{}"))
    with pytest.raises(BudgetDenied):
        Comparison(enrolled.path).wire_failure(
            enrolled.report()["wire_attempts"][0]["id"], "cancelled"
        )


def test_no_live_enrollment_without_separate_complete_review(tmp_path, monkeypatch):
    mock = asyncio.run(initialize_comparison(tmp_path / "mock"))
    monkeypatch.setattr(live, "network_transport", lambda: pytest.fail("No construction"))
    with pytest.raises(BudgetDenied):
        run(mock)
    with pytest.raises(BudgetDenied):
        asyncio.run(live.initialize_live_comparison(tmp_path / "live", generation_review={}))
    assert not (tmp_path / "live").exists() and mock.report()["campaign"]["status"] == "prepared"


def test_tls_proxy_retry_options_and_overlapping_log_suppression(monkeypatch):
    seen = []
    monkeypatch.setattr(httpx2, "AsyncHTTPTransport", lambda **kw: seen.append(kw))
    wire_module.network_transport()
    assert {k: v for k, v in seen[0].items() if k != "limits"} == {
        "verify": True,
        "trust_env": False,
        "proxy": None,
        "retries": 0,
        "http1": True,
        "http2": False,
    }

    async def scenario():
        first, second, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

        @wire_module.quiet_wire
        async def quiet(started, wait):
            started.set()
            await wait.wait()

        previous = logging.root.manager.disable
        one = asyncio.create_task(quiet(first, second))
        await first.wait()
        two = asyncio.create_task(quiet(second, release))
        await one
        assert logging.root.manager.disable >= logging.CRITICAL
        release.set()
        await two
        assert logging.root.manager.disable == previous

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "point", ["reserve", "claim", "dispatch", "headers", "usage", "post_payment"]
)
def test_abrupt_live_wire_exit_preserves_exposure_and_fences_restart(enrolled, point):
    root = enrolled.path.parent
    child = subprocess.run(
        [
            sys.executable,
            "-B",
            str(Path(__file__).with_name("comparison_wire_worker.py")),
            str(root),
            "live",
            point,
        ],
        capture_output=True,
        text=True,
        timeout=40,
    )
    assert child.returncode == 89, (child.stdout, child.stderr)
    report = Comparison(enrolled.path).report()
    assert report["campaign"]["status"] == "running" and report["blocked"]
    marker = root / "independent-dispatches.txt"
    actual = marker.read_text().splitlines() if marker.exists() else []
    if point != "post_payment":
        assert len(actual) == int(point not in ("reserve", "claim"))
        assert report["request_attempts_reserved"] == 1
        assert report["verified_complete_responses"] == int(point == "usage")
        assert report["unresolved_exposure_nano_usd"] == (
            0 if point == "usage" else reservation_estimate("openai", 10)
        )
    else:
        assert payment_facts(enrolled) == (1, 1)
        assert report["unknown_actual_outcomes"] == 0
    with pytest.raises(BudgetDenied):
        run(Comparison(enrolled.path))
    assert Comparison(enrolled.path).report() == report
    assert_no_keys(enrolled, child.stdout + child.stderr)


def test_live_timeout_keeps_pending_attempt_and_stops_other_arm(tmp_path, monkeypatch):
    review = prepare_review(tmp_path, monkeypatch)
    comparison = asyncio.run(
        live.initialize_live_comparison(
            tmp_path / "paired", generation_review=review, timeout_seconds=1
        )
    )

    async def wait(entry, request, normal):
        if str(request.url) in COUNT_ENDPOINTS.values():
            return await normal(request)
        await asyncio.Event().wait()

    _, _, requests = scripted_wires(comparison, monkeypatch, wait)
    report = run(comparison)
    assert len(requests) == 2 and report["request_attempts_reserved"] == 1
    assert report["campaign"]["status"] == "stopped"
    assert report["episodes"]["openai.trial-1.free"]["result"]["termination"] == "timeout"
    assert report["unresolved_exposure_nano_usd"] == reservation_estimate("openai", 10)


@pytest.mark.parametrize(
    "change", ["authorization", "backstop", "account", "result_hash", "source_hash"]
)
def test_generation_review_requires_every_independent_attestation(tmp_path, monkeypatch, change):
    review = prepare_review(tmp_path, monkeypatch)
    field, value = {
        "authorization": ("generation_authorization", "count_only"),
        "backstop": ("conditional_backstop_accepted", False),
        "account": ("account_pricing_scope_reviewed", False),
        "result_hash": ("preflight_result_hash", "0" * 64),
        "source_hash": ("reviewed_source_hash", "0" * 64),
    }[change]
    review[field] = value
    with pytest.raises(BudgetDenied, match="generation_review_required"):
        asyncio.run(live.initialize_live_comparison(tmp_path / "live", generation_review=review))
    assert not (tmp_path / "live").exists()


def test_old_comparison_schema_is_rejected_without_migration(tmp_path):
    comparison = asyncio.run(initialize_comparison(tmp_path / "old"))
    with sqlite3.connect(comparison.path) as db:
        db.execute("DROP TABLE wire_attempts")
        db.execute("PRAGMA user_version=1")
    before = comparison.path.read_bytes()
    with pytest.raises(BudgetDenied):
        Comparison(comparison.path)
    assert comparison.path.read_bytes() == before
