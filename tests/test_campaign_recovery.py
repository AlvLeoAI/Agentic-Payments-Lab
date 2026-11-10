import asyncio
import json
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import httpx2
import pytest
from campaign_support import responders
from pilot_support import response

from agentic_payments_lab.buyer_agent import campaign_runner
from agentic_payments_lab.buyer_agent.call_budget import BudgetDenied
from agentic_payments_lab.buyer_agent.campaign import Campaign, initialize_campaign, state_witness
from agentic_payments_lab.buyer_agent.campaign_cases import frozen_plan
from agentic_payments_lab.buyer_agent.campaign_runner import financial_facts, run_mock_campaign
from agentic_payments_lab.buyer_agent.scripted import final_response, tool_response
from agentic_payments_lab.runtime import Runtime


@pytest.fixture
def campaign(tmp_path):
    return asyncio.run(initialize_campaign(tmp_path / "campaign"))


@pytest.mark.parametrize(
    "boundary",
    [
        "activation",
        "entry",
        "reservation",
        "dispatch",
        "usage",
        "post_payment",
        "result",
    ],
)
def test_abrupt_process_loss_never_recovers_or_resends(campaign, boundary):
    child = subprocess.run(
        [
            sys.executable,
            "-B",
            str(Path(__file__).with_name("campaign_worker.py")),
            str(campaign.path.parent),
            boundary,
        ],
        capture_output=True,
        text=True,
        timeout=40,
    )
    assert child.returncode == 87, (child.stdout, child.stderr)
    record = Campaign(campaign.path).report()
    assert record["campaign"]["status"] == "running" and record["blocked"]
    reserved, complete, actual = {
        "activation": (0, 0, 0),
        "entry": (0, 0, 0),
        "reservation": (1, 0, None),
        "dispatch": (1, 0, None),
        "usage": (1, 1, 1),
        "post_payment": (10, 9, None),
        "result": (5, 5, 5),
    }[boundary]
    assert record["request_attempts_reserved"] == reserved
    assert record["known_complete_responses"] == complete
    assert record["actual_responses_requests"] == actual
    with pytest.raises(BudgetDenied):
        asyncio.run(run_mock_campaign(Campaign(campaign.path), lambda _: pytest.fail("No resend")))
    if boundary in ("reservation", "dispatch", "post_payment"):
        assert not record["usage_complete"]
        assert record["reserved_estimate_nano_usd"] > 0
        assert record["actual_responses_requests"] is None
    if boundary == "post_payment":
        with sqlite3.connect(campaign.path.parent / "states/trial-1-paid/payments.sqlite3") as db:
            rows = [json.loads(r[0]) for r in db.execute("SELECT result FROM authorizations")]
            assert db.execute("SELECT count(*) FROM attempts").fetchone()[0] == 1
        assert len(rows) == 1 and rows[0]["status"] == "settled"
        assert record["known_estimate_nano_usd"] == 9 * 3645
    if boundary == "result":
        assert len(record["results"]) == 1 and record["results"]["trial-1.free"]["safety_passed"]


def test_simultaneous_processes_can_claim_only_one_campaign(campaign):
    root = campaign.path.parent
    workers = [
        subprocess.Popen(
            [
                sys.executable,
                "-B",
                str(Path(__file__).with_name("campaign_worker.py")),
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
        deadline = time.monotonic() + 20
        while not all((root / f"ready-{i}").exists() for i in range(4)):
            assert time.monotonic() < deadline and all(p.poll() is None for p in workers)
            time.sleep(0.02)
        (root / "go").touch()
        results = [p.communicate(timeout=20) for p in workers]
        assert all(p.returncode == 0 for p in workers), results
        assert sum(json.loads(out)["claimed"] for out, _ in results) == 1
        record = Campaign(campaign.path).report()
        assert record["blocked"] and record["requests"] == []
    finally:
        for process in workers:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)


@pytest.mark.parametrize(
    "boundary", ["reservation_ack", "usage_before", "usage_after", "result_before", "result_after"]
)
def test_persistence_failure_stops_without_replacement(campaign, monkeypatch, boundary):
    calls = []
    if boundary == "reservation_ack":
        original = campaign.reserve

        def fail(*args):
            original(*args)
            raise sqlite3.OperationalError("PRIVATE_PERSISTENCE_SENTINEL")

        monkeypatch.setattr(campaign, "reserve", fail)
    elif boundary.startswith("usage"):
        original = campaign.observe

        def fail(*args):
            if boundary.endswith("after"):
                original(*args)
            raise sqlite3.OperationalError("PRIVATE_PERSISTENCE_SENTINEL")

        monkeypatch.setattr(campaign, "observe", fail)
    else:
        original = campaign.record_entry

        def fail(*args, **kwargs):
            if boundary.endswith("after"):
                original(*args, **kwargs)
            raise sqlite3.OperationalError("PRIVATE_PERSISTENCE_SENTINEL")

        monkeypatch.setattr(campaign, "record_entry", fail)
    try:
        asyncio.run(run_mock_campaign(campaign, responders(calls)))
    except BudgetDenied as exc:
        assert "PRIVATE" not in str(exc)
    record = Campaign(campaign.path).report()
    assert record["campaign"]["status"] == "stopped" and record["blocked"]
    assert len(calls) == (
        0 if boundary == "reservation_ack" else 5 if boundary.startswith("result") else 1
    )
    reserved = 5 if boundary.startswith("result") else 1
    complete = 0 if boundary in ("reservation_ack", "usage_before") else len(calls)
    actual = reserved if reserved == complete else None
    assert record["request_attempts_reserved"] == reserved
    assert record["known_complete_responses"] == complete
    assert record["actual_responses_requests"] == actual
    if boundary != "result_before":
        first = record["results"]["trial-1.free"]
        assert first["request_attempts_reserved"] == reserved
        assert first["known_complete_responses"] == complete
        assert first["actual_responses_requests"] == actual
        assert "actual_requests" not in first
    else:
        assert record["results"] == {}  # Do not manufacture a missing result.
    if not complete:
        assert not record["usage_complete"] and not record["accounting_complete"]
        assert record["known_estimate_nano_usd"] is None
        assert record["estimated_total_nano_usd"] is None
        assert record["reserved_estimate_nano_usd"] == record["requests"][0]["reserved_nano"]
        assert record["requests"][0]["usage"] is None
    else:
        assert record["known_estimate_nano_usd"] == complete * 3645
    with sqlite3.connect(
        f"file:{campaign.path.parent / 'states/trial-1-free/payments.sqlite3'}?mode=ro", uri=True
    ) as db:
        assert db.execute("SELECT count(*) FROM attempts").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM authorizations").fetchone()[0] == 0
    assert "PRIVATE_PERSISTENCE_SENTINEL" not in json.dumps(record)
    with pytest.raises(BudgetDenied):
        asyncio.run(run_mock_campaign(Campaign(campaign.path), responders([])))


def test_control_violation_stops_after_one_response(campaign):
    calls = []

    def factory(_):
        def respond(req):
            calls.append(1)
            output = [
                tool_response("purchase", {"quote_id": "invented"}, f"p-{i}").output[0]
                for i in range(9)
            ]
            return httpx2.Response(200, json=response(1, output))

        return respond

    report = asyncio.run(run_mock_campaign(campaign, factory))
    assert calls == [1]
    assert report["campaign"]["status"] == "stopped"
    first = report["results"]["trial-1.free"]
    assert first["termination"] == "tool_limit" and not first["safety_passed"]
    assert first["after"]["executions"] == first["after"]["charges"] == 0


def test_turn_bound_six_serialized_requests_no_retry(tmp_path):
    campaign = asyncio.run(initialize_campaign(tmp_path / "limited", max_requests=6))
    calls = []

    def factory(_):
        def respond(req):
            calls.append(1)
            return httpx2.Response(
                200,
                json=response(
                    len(calls),
                    tool_response("get_purchase_status", {}, f"s-{len(calls)}").output,
                ),
            )

        return respond

    report = asyncio.run(run_mock_campaign(campaign, factory))
    assert len(calls) == 6 and len(report["requests"]) == 6
    assert report["results"]["trial-1.free"]["termination"] == "turn_limit"
    assert report["results"]["trial-1.free"]["safety_passed"]
    assert not report["results"]["trial-1.free"]["behavior_passed"]
    assert report["campaign"]["status"] == "stopped"


def test_financial_violation_stops_even_with_complete_usage(campaign):
    def factory(_):
        def respond(req):
            with sqlite3.connect(
                campaign.path.parent / "states/trial-1-free/payments.sqlite3"
            ) as db:
                db.execute(
                    "INSERT INTO attempts(authorization_id,recorded_at) VALUES('unexpected',0)"
                )
            return httpx2.Response(200, json=response(1, final_response("Success").output))

        return respond

    report = asyncio.run(run_mock_campaign(campaign, factory))
    assert report["usage_complete"] and len(report["requests"]) == 1
    assert report["campaign"]["status"] == "stopped"
    assert not report["results"]["trial-1.free"]["safety_passed"]


def test_live_entrypoint_requires_separate_mode_and_exact_hash_before_wire(campaign, monkeypatch):
    from pydantic import SecretStr

    monkeypatch.setattr(
        campaign_runner, "network_transport", lambda: pytest.fail("No wire construction")
    )
    with pytest.raises(BudgetDenied):
        asyncio.run(
            campaign_runner.run_live_campaign(
                campaign.path.parent,
                api_key=SecretStr("synthetic-fixture-not-a-real-key"),
                approved_enrollment_hash=campaign.enrollment_hash,
            )
        )
    assert campaign.report()["campaign"]["status"] == "prepared"


@pytest.mark.parametrize("change", ["grant", "metadata"])
def test_mid_run_authority_or_binding_loss_is_not_accepted(campaign, change):
    def factory(_):
        def respond(req):
            directory = campaign.path.parent / "states/trial-1-free"
            with sqlite3.connect(
                directory / ("ledger.sqlite3" if change == "grant" else "agent.sqlite3")
            ) as db:
                if change == "grant":
                    data = json.loads(db.execute("SELECT data FROM grants").fetchone()[0])
                    data["operator_id"] = "changed-operator"
                    db.execute("UPDATE grants SET data=?", (json.dumps(data),))
                else:
                    db.execute("DELETE FROM bindings")
            return httpx2.Response(
                200, json=response(1, final_response("Everything succeeded").output)
            )

        return respond

    report = asyncio.run(run_mock_campaign(campaign, factory))
    assert report["campaign"]["status"] == "stopped"
    assert not report["results"]["trial-1.free"]["safety_passed"]
    assert len(report["requests"]) == 1


def test_timeout_and_late_cancelled_response_keep_unknown_usage(tmp_path):
    campaign = asyncio.run(initialize_campaign(tmp_path / "timeout", timeout_seconds=1))
    calls = []

    def factory(_):
        async def respond(req):
            calls.append(1)
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                return httpx2.Response(200, json=response(1, final_response("Late success").output))

        return respond

    report = asyncio.run(run_mock_campaign(campaign, factory))
    assert calls == [1] and report["campaign"]["status"] == "stopped"
    assert not report["usage_complete"] and report["estimated_total_nano_usd"] is None
    assert report["results"]["trial-1.free"]["after"]["charges"] == 0


def test_repeated_cancellation_during_cleanup_keeps_durable_stop(campaign, monkeypatch):
    from openai import AsyncOpenAI

    async def exercise():
        entered = asyncio.Event()
        closing = asyncio.Event()
        original_close = AsyncOpenAI.close

        async def close(client):
            closing.set()
            try:
                await asyncio.Event().wait()
            finally:
                await original_close(client)

        def factory(_):
            async def respond(req):
                entered.set()
                await asyncio.Event().wait()

            return respond

        monkeypatch.setattr(AsyncOpenAI, "close", close)
        task = asyncio.create_task(run_mock_campaign(campaign, factory))
        await asyncio.wait_for(entered.wait(), 10)
        task.cancel("FIRST_PRIVATE_CANCEL")
        await asyncio.wait_for(closing.wait(), 10)
        task.cancel("SECOND_PRIVATE_CANCEL")
        with pytest.raises(asyncio.CancelledError, match="^campaign_cancelled$"):
            await task

    asyncio.run(exercise())
    report = Campaign(campaign.path).report()
    assert report["campaign"]["status"] == "stopped"
    assert report["results"]["trial-1.free"]["termination"] == "cancelled"
    assert report["reserved_estimate_nano_usd"] > 0
    assert not report["usage_complete"] and report["estimated_total_nano_usd"] is None
    assert "PRIVATE_CANCEL" not in json.dumps(report)


@pytest.mark.parametrize("database", ["ledger", "payments", "provider", "agent"])
def test_snapshot_lock_waits_are_short_and_never_fabricate_zero(campaign, database):
    from contextlib import closing

    entry = frozen_plan()[0]
    directory = campaign.path.parent / "states" / entry["fixture"]
    runtime = Runtime(directory)
    with closing(sqlite3.connect(directory / f"{database}.sqlite3")) as locked:
        locked.execute("PRAGMA locking_mode=EXCLUSIVE")
        locked.execute("BEGIN EXCLUSIVE")
        start = time.monotonic()
        with pytest.raises(sqlite3.OperationalError):
            financial_facts(runtime, entry)
        assert time.monotonic() - start < 1.5
        start = time.monotonic()
        with pytest.raises(BudgetDenied, match="campaign_fixture_history_unavailable"):
            state_witness(directory)
        assert time.monotonic() - start < 1.5


def test_external_cancellation_with_locked_financial_store_propagates(campaign):
    from contextlib import closing

    async def exercise():
        entered = asyncio.Event()

        def factory(_):
            async def respond(req):
                entered.set()
                await asyncio.Event().wait()

            return respond

        task = asyncio.create_task(run_mock_campaign(campaign, factory))
        await asyncio.wait_for(entered.wait(), 10)
        directory = campaign.path.parent / "states/trial-1-free"
        with closing(sqlite3.connect(directory / "ledger.sqlite3")) as locked:
            locked.execute("PRAGMA locking_mode=EXCLUSIVE")
            locked.execute("BEGIN EXCLUSIVE")
            start = time.monotonic()
            task.cancel("PRIVATE_CANCEL_WHILE_LOCKED")
            with pytest.raises(asyncio.CancelledError, match="^campaign_cancelled$"):
                await task
            assert time.monotonic() - start < 1.5

    asyncio.run(exercise())
    report = Campaign(campaign.path).report()
    assert report["campaign"] == {"status": "stopped", "reason": "cancelled"}
    assert not report["usage_complete"] and report["reserved_estimate_nano_usd"] > 0
    assert report["results"] == {}  # Failed snapshot is missing, never a fabricated zero.
    assert report["invocations"][0]["status"] == "running"
    assert "PRIVATE_CANCEL" not in json.dumps(report)
