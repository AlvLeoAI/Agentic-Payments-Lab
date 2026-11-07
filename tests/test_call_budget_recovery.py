import asyncio
import json
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx2
import pytest
from call_budget_support import initialized, payload, usage
from test_call_budget import bound

from agentic_payments_lab.buyer_agent.call_budget import BudgetDenied, CallBudget
from agentic_payments_lab.buyer_agent.mock_responses import run_mock_buyer
from agentic_payments_lab.buyer_agent.scripted import tool_response


@pytest.mark.parametrize(
    "boundary", ["uncommitted_invocation", "invocation", "reservation", "dispatch", "usage"]
)
def test_abrupt_exit_never_resumes_or_releases_uncertain_exposure(tmp_path, boundary):
    budget = initialized(tmp_path)
    command = [
        sys.executable,
        str(Path(__file__).with_name("call_budget_worker.py")),
        str(tmp_path),
        boundary,
    ]
    stdout, stderr = tmp_path / "worker.stdout", tmp_path / "worker.stderr"
    try:
        with stdout.open("w") as out, stderr.open("w") as err:
            result = subprocess.run(command, stdout=out, stderr=err, timeout=30)
        failure = None if result.returncode == 79 else f"Worker exit {result.returncode}"
    except subprocess.TimeoutExpired:
        failure = "Worker exceeded its bounded subprocess timeout"
    if failure:
        pytest.fail(
            f"{failure}\nSTDOUT:\n{stdout.read_text()}\nSTDERR:\n{stderr.read_text()}",
            pytrace=False,
        )
    reopened = CallBudget(budget.path)
    record = reopened.report()
    if boundary == "uncommitted_invocation":
        assert record["invocations"] == record["requests"] == []
        reopened.begin_invocation("explicit-fresh-attempt")
        return
    assert len(record["invocations"]) == 1 and record["blocked"]
    assert len(record["requests"]) == int(boundary != "invocation")
    assert (tmp_path / "dispatch.txt").exists() == (boundary in ("dispatch", "usage"))
    if boundary in ("reservation", "dispatch"):
        assert record["reserved_estimate_nano_usd"] > 0
        assert record["estimated_total_nano_usd"] is None
    if boundary == "usage":
        assert record["usage_complete"] and record["known_estimate_nano_usd"] == 62
    with pytest.raises(BudgetDenied):
        reopened.begin_invocation("new")
    # Reopening cannot recover another process's invocation capability.
    with pytest.raises(BudgetDenied):
        reopened.reserve("worker", b"{}")
    with pytest.raises(BudgetDenied):
        reopened.finish_invocation("worker", "completed")
    if record["requests"]:
        with pytest.raises(BudgetDenied):
            reopened.observe(record["requests"][0]["id"], usage(), "default", "gpt-6-luna")


@pytest.mark.parametrize("missing", [True, False])
def test_missing_or_unsupported_budget_history_rejects_without_initialization(tmp_path, missing):
    path = tmp_path / "lost.sqlite3"
    if not missing:
        with sqlite3.connect(path) as db:
            db.execute("PRAGMA user_version=99")
    before = path.read_bytes() if path.exists() else None
    with pytest.raises(BudgetDenied):
        CallBudget(path)
    assert (path.read_bytes() if path.exists() else None) == before


@pytest.mark.parametrize("change", ["output", "tier", "estimate"])
def test_observed_assumption_overrun_freezes_future_calls(tmp_path, change):
    budget = initialized(tmp_path)
    budget.begin_invocation("first")
    request = budget.reserve("first", b"{}")
    raw, tier = usage(), "default"
    if change == "output":
        raw.update(output_tokens=513, total_tokens=523)
    elif change == "tier":
        tier = "unrecognized-tier"
    else:
        raw.update(input_tokens=1_000_000, total_tokens=1_000_005)
    assert not budget.observe(request, raw, tier, "gpt-6-luna")
    record = budget.report()
    assert record["blocked"] and record["usage_complete"]
    assert not record["accounting_complete"] and record["reserved_estimate_nano_usd"] > 0
    with pytest.raises(BudgetDenied):
        budget.reserve("first", b"{}")


def test_repeated_cancellation_and_persistence_failure_preserve_pending_marker(
    runtime, tmp_path, monkeypatch
):
    session, store, goal = bound(runtime, tmp_path)
    budget = initialized(tmp_path)

    def failed_cleanup(*args):
        raise sqlite3.OperationalError("PRIVATE_STORAGE_MARKER")

    monkeypatch.setattr(budget, "interrupt", failed_cleanup)
    monkeypatch.setattr(budget, "finish_invocation", failed_cleanup)

    async def exercise():
        entered = asyncio.Event()

        async def respond(request):
            entered.set()
            await asyncio.Event().wait()

        task = asyncio.create_task(run_mock_buyer(session, store, goal, budget, "cancel", respond))
        await asyncio.wait_for(entered.wait(), 10)
        task.cancel("PRIVATE_CANCEL_MARKER")
        task.cancel("PRIVATE_SECOND_MARKER")
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(exercise())
    result = CallBudget(budget.path).report()
    assert result["requests"][0]["status"] == "pending"
    assert result["blocked"] and result["estimated_total_nano_usd"] is None
    assert result["reserved_estimate_nano_usd"] > 0
    assert b"PRIVATE" not in budget.path.read_bytes()


def test_tool_attempt_limit_still_rejects_before_any_tool(runtime, tmp_path):
    session, store, goal = bound(runtime, tmp_path)
    budget = initialized(tmp_path, tool_attempts=2)

    async def respond(request):
        output = []
        for number in range(3):
            output.extend(tool_response("get_purchase_status", {}, f"call-{number}").output)
        return httpx2.Response(200, json=payload(1, output))

    report = asyncio.run(run_mock_buyer(session, store, goal, budget, "limited", respond))
    assert report.termination == "tool_limit" and report.tool_attempts == 3
    assert len(budget.report()["requests"]) == 1
    assert report.events[0].outcome == "tool_limit"
    with sqlite3.connect(tmp_path / "payments.sqlite3") as db:
        assert db.execute("SELECT count(*) FROM attempts").fetchone()[0] == 0


def test_no_raw_request_response_or_exception_is_persisted(runtime, tmp_path):
    session, store, goal = bound(runtime, tmp_path)
    budget = initialized(tmp_path)

    async def respond(request):
        raise RuntimeError("PRIVATE_EXCEPTION_WITH_PAYLOAD")

    asyncio.run(run_mock_buyer(session, store, goal, budget, "sanitized", respond))
    data = json.dumps(budget.report())
    assert "PRIVATE_EXCEPTION" not in data and "instructions" not in data and "tools" not in data
    assert b"PRIVATE_EXCEPTION" not in budget.path.read_bytes()
    assert "body_hash" in data and "body_bytes" in data


@pytest.mark.parametrize(
    "field,value",
    [
        ("model", None),
        ("model", "other-model"),
        ("model", ["malformed"]),
        ("service_tier", None),
        ("service_tier", "priority"),
        ("service_tier", "unknown"),
    ],
)
def test_missing_or_mismatched_response_identity_cannot_release_or_execute_tools(
    runtime, tmp_path, field, value
):
    session, store, goal = bound(runtime, tmp_path)
    budget = initialized(tmp_path)
    calls = []

    async def respond(request):
        calls.append(1)
        data = payload(1, tool_response("get_purchase_status", {}, "first").output)
        if value is None:
            del data[field]
        else:
            data[field] = value
        return httpx2.Response(200, json=data)

    report = asyncio.run(run_mock_buyer(session, store, goal, budget, "unverified", respond))
    assert report.termination == "model_error" and report.tool_attempts == 0
    result = CallBudget(budget.path).report()
    assert result["usage_complete"] and not result["accounting_complete"]
    assert result["requests"][0]["status"] == "unverified"
    assert result["requests"][0]["usage"]["input_tokens"] == 10
    assert result["reserved_estimate_nano_usd"] > 0
    assert result["known_estimate_nano_usd"] is result["estimated_total_nano_usd"] is None
    with pytest.raises(BudgetDenied):
        CallBudget(budget.path).begin_invocation("again")
    assert calls == [1]


def test_overlapping_cache_categories_are_unknown_not_discounted(tmp_path):
    budget = initialized(tmp_path)
    budget.begin_invocation("cache")
    request = budget.reserve("cache", b"{}")
    raw = usage()
    raw["input_tokens_details"] = {"cached_tokens": 6, "cache_write_tokens": 5}
    assert not budget.observe(request, raw, "default", "gpt-6-luna")
    result = budget.report()
    assert result["blocked"] and not result["usage_complete"]
    assert result["estimated_total_nano_usd"] is None
    assert result["cache_write_lifetime"] == "unknown_in_pinned_usage_schema"


def test_responses_mock_cannot_activate_offline_x402_buyer(tmp_path):
    from offline_buyer_support import counters, setup

    shop, buyer, metadata, goal = setup(tmp_path)
    budget = initialized(tmp_path)

    async def respond(request):
        raise AssertionError("must not dispatch")

    with pytest.raises(ValueError, match="scripted only"):
        asyncio.run(run_mock_buyer(buyer, metadata, goal, budget, "blocked", respond))
    assert budget.report()["invocations"] == []
    assert counters(shop) == (0, 0, 0, 0, 0)


def test_separate_processes_claim_one_invocation_with_full_diagnostics(tmp_path):
    budget = initialized(tmp_path)
    workers, handles, failure = [], [], None
    try:
        for number in range(4):
            out = (tmp_path / f"worker-{number}.stdout").open("w")
            err = (tmp_path / f"worker-{number}.stderr").open("w")
            handles.extend((out, err))
            workers.append(
                subprocess.Popen(
                    [
                        sys.executable,
                        str(Path(__file__).with_name("call_budget_worker.py")),
                        str(tmp_path),
                        "claim",
                        str(number),
                    ],
                    stdout=out,
                    stderr=err,
                )
            )
        deadline = time.monotonic() + 30
        while True:
            assert all(p.poll() is None for p in workers), "worker exited before cohort readiness"
            ready = [tmp_path / f"ready-{number}.json" for number in range(4)]
            if all(p.exists() for p in ready):
                assert [json.loads(p.read_text())["pid"] for p in ready] == [p.pid for p in workers]
                break
            assert time.monotonic() < deadline, "cohort startup deadline exceeded"
            time.sleep(0.02)
        assert budget.report()["invocations"] == []
        (tmp_path / "go").touch()
        for worker in workers:
            assert worker.wait(timeout=10) == 0, "worker failed after common release"
        outcomes = [
            json.loads((tmp_path / f"worker-{number}.stdout").read_text()) for number in range(4)
        ]
        assert sum(outcome["claimed"] for outcome in outcomes) == 1
        assert all(
            outcome.get("reason") == "invocation_incomplete"
            for outcome in outcomes
            if not outcome["claimed"]
        )
        record = CallBudget(budget.path).report()
        assert len(record["invocations"]) == 1 and record["requests"] == []
    except BaseException as error:
        failure = f"{type(error).__name__}: {error}"
    finally:
        for worker in workers:
            if worker.poll() is None:
                worker.kill()
        for worker in workers:
            worker.wait(timeout=5)
        for handle in handles:
            handle.close()
    if failure:
        retained = Path(tempfile.mkdtemp(prefix="payments-budget-workers-failure-"))
        shutil.copytree(tmp_path, retained / "state")
        diagnostics = "\n".join(
            f"{p.name}:\n{p.read_text()}" for p in sorted(tmp_path.glob("worker-*.*"))
        )
        pytest.fail(f"{failure}\nRetained: {retained}\n{diagnostics}", pytrace=False)
