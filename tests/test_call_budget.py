import asyncio
import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, nullcontext

import httpx2
import pytest
from call_budget_support import initialized, limits, payload, usage
from conftest import authorize
from test_buyer_agent import authority

from agentic_payments_lab.buyer_agent.call_budget import BudgetDenied, CallBudget
from agentic_payments_lab.buyer_agent.contracts import Goal
from agentic_payments_lab.buyer_agent.mock_responses import run_mock_buyer
from agentic_payments_lab.buyer_agent.scripted import ScriptedBuyer, tool_response
from agentic_payments_lab.buyer_agent.store import AgentStore


def bound(runtime, tmp_path, *, free=True):
    authorize(
        runtime,
        budget=0 if free else 10_000,
        cap=0 if free else 10_000,
        services=("report-free",) if free else ("report-standard",),
    )
    goal = Goal(
        subject="offline-call-budget", purpose="research", quality="basic" if free else "standard"
    )
    store = AgentStore(tmp_path / "agent.sqlite3")
    store.initialize(runtime.ledger, "task", goal)
    return runtime.buyer("task"), store, goal


def test_wire_requests_include_schemas_tools_and_history(runtime, tmp_path):
    session, store, goal = bound(runtime, tmp_path)
    budget = initialized(tmp_path)
    scripted = ScriptedBuyer()
    bodies = []

    async def respond(request):
        bodies.append(request.content)
        # The durable reservation exists before every mocked dispatch.
        pending = budget.report()["requests"][-1]
        assert pending["status"] == "pending" and pending["body_bytes"] == len(request.content)
        body = json.loads(request.content)
        response = await scripted.get_response(input=body["input"])
        return httpx2.Response(200, json=payload(len(bodies), response.output))

    report = asyncio.run(run_mock_buyer(session, store, goal, budget, "first", respond))
    assert report.termination == "completed"
    recorded = CallBudget(budget.path).report()
    assert len(bodies) == len(recorded["requests"]) == 5
    assert recorded["usage_complete"] and not recorded["blocked"]
    assert recorded["reserved_estimate_nano_usd"] == 0
    # Cached and cache-write categories are separate; reasoning is included once in output.
    assert recorded["known_estimate_nano_usd"] == 5 * (8 * 3 + 2 + 3 * 2 + 5 * 6)
    for raw, row in zip(bodies, recorded["requests"], strict=True):
        body = json.loads(raw)
        assert row["body_bytes"] == len(raw)
        assert len(raw) > len(json.dumps(body["input"]).encode()) + len(
            body["instructions"].encode()
        )
        assert body["model"] == "gpt-6-luna"
        assert body["reasoning"] == {"effort": "none"} and body["service_tier"] == "default"
        assert body["max_output_tokens"] == 512 and body["parallel_tool_calls"] is False
        assert body["text"]["format"]["strict"] is True
        assert len(body["tools"]) == 4 and all(t["strict"] for t in body["tools"])
        assert row["usage"]["cached_tokens"] == 2 and row["usage"]["cache_write_tokens"] == 3
    for raw in bodies[1:]:
        items = json.loads(raw)["input"]
        assert [i["call_id"] for i in items if i.get("type") == "function_call"] == [
            i["call_id"] for i in items if i.get("type") == "function_call_output"
        ]
    assert authority(runtime) == (0, 0, 0)


@pytest.mark.parametrize(
    "mutation",
    [
        "missing",
        "negative",
        "string",
        "boolean",
        "total",
        "cached",
        "reasoning",
        "cache_write",
        "extra",
    ],
)
def test_invalid_usage_freezes_with_partial_evidence(runtime, tmp_path, mutation):
    session, store, goal = bound(runtime, tmp_path)
    budget = initialized(tmp_path)
    calls = []
    raw = usage()
    if mutation == "missing":
        raw = {"input_tokens": 10}
    elif mutation == "negative":
        raw["output_tokens"] = -1
    elif mutation == "string":
        raw["output_tokens"] = "5"
    elif mutation == "boolean":
        raw["output_tokens"] = True
    elif mutation == "total":
        raw["total_tokens"] = 99
    elif mutation == "cached":
        raw["input_tokens_details"]["cached_tokens"] = 11
    elif mutation == "reasoning":
        raw["output_tokens_details"]["reasoning_tokens"] = 6
    elif mutation == "cache_write":
        del raw["input_tokens_details"]["cache_write_tokens"]
    else:
        raw["unsupported_usage_category"] = 1

    async def respond(request):
        calls.append(1)
        return httpx2.Response(
            200,
            json=payload(1, tool_response("get_purchase_status", {}, "one").output, raw_usage=raw),
        )

    report = asyncio.run(run_mock_buyer(session, store, goal, budget, "first", respond))
    assert report.termination == "model_error" and report.tool_attempts == 0
    record = CallBudget(budget.path).report()
    assert record["blocked"] and not record["usage_complete"]
    assert record["requests"][0]["usage"]["input_tokens"] == 10
    assert record["estimated_total_nano_usd"] is None and record["reserved_estimate_nano_usd"] > 0
    with pytest.raises(BudgetDenied, match="accounting_incomplete"):
        asyncio.run(
            run_mock_buyer(session, store, goal, CallBudget(budget.path), "second", respond)
        )
    assert calls == [1] and authority(runtime) == (0, 0, 0)


@pytest.mark.parametrize("status", [200, 429, 500])
def test_six_requests_sequential_no_api_retries(runtime, tmp_path, status):
    session, store, goal = bound(runtime, tmp_path)
    budget = initialized(tmp_path)
    calls, active = [], 0

    async def respond(request):
        nonlocal active
        active += 1
        assert active == 1
        try:
            await asyncio.sleep(0)
            calls.append(json.loads(request.content))
            return httpx2.Response(
                status,
                json=payload(
                    len(calls),
                    tool_response("get_purchase_status", {}, f"call-{len(calls)}").output,
                ),
            )
        finally:
            active -= 1

    report = asyncio.run(run_mock_buyer(session, store, goal, budget, "first", respond))
    assert len(calls) == (6 if status == 200 else 1)
    assert report.termination == ("turn_limit" if status == 200 else "model_error")
    assert len(budget.report()["requests"]) == len(calls)
    assert authority(runtime) == (0, 0, 0)


def test_request_invocation_and_planning_limits_survive_restart(tmp_path):
    budget = initialized(tmp_path, max_requests=3, requests_per_invocation=2)
    for invocation, count in (("one", 2), ("two", 1)):
        budget.begin_invocation(invocation)
        for _ in range(count):
            request = budget.reserve(invocation, b"{}")
            assert budget.observe(request, usage(), "default", "gpt-6-luna")
            with pytest.raises(BudgetDenied):
                budget.observe(request, usage(), "default", "gpt-6-luna")
        with pytest.raises(BudgetDenied, match="request_limit"):
            budget.reserve(invocation, b"{}")
        budget.finish_invocation(invocation, "completed")
        budget = CallBudget(budget.path)
    with pytest.raises(BudgetDenied, match="already_claimed"):
        budget.begin_invocation("one")
    budget.begin_invocation("three")
    budget.finish_invocation("three", "stopped")
    with pytest.raises(BudgetDenied, match="invocation_limit"):
        budget.begin_invocation("four")
    assert budget.report()["known_estimate_nano_usd"] == 3 * 62
    path = tmp_path / "empty-budget.sqlite3"
    zero = CallBudget.initialize(path, limits().model_copy(update={"estimate_ceiling_nano_usd": 0}))
    zero.begin_invocation("zero")
    with pytest.raises(BudgetDenied, match="planning_budget_exhausted"):
        zero.reserve("zero", b"{}")
    assert zero.report()["requests"] == []


def test_overlapping_claims_and_incomplete_invocation_never_resume(tmp_path):
    budget = initialized(tmp_path)
    ready = threading.Barrier(4)

    def claim(number):
        instance = CallBudget(budget.path)
        ready.wait(timeout=5)
        try:
            instance.begin_invocation(f"run-{number}")
            return True
        except BudgetDenied:
            return False

    with ThreadPoolExecutor(max_workers=4) as pool:
        assert sum(pool.map(claim, range(4))) == 1
    reopened = CallBudget(budget.path)
    with pytest.raises(BudgetDenied, match="invocation_incomplete"):
        reopened.begin_invocation("restart")
    with pytest.raises(FileExistsError):
        CallBudget.initialize(budget.path, limits())
    missing = tmp_path / "absent.sqlite3"
    with pytest.raises(BudgetDenied):
        CallBudget(missing)
    assert not missing.exists()


def test_overlapping_request_reservations_have_one_owner_and_no_oversubscription(tmp_path):
    budget = initialized(tmp_path)
    budget.begin_invocation("shared")
    ready = threading.Barrier(4)

    def reserve(number):
        ready.wait(timeout=5)
        try:
            return budget.reserve("shared", f'{{"number":{number}}}'.encode())
        except BudgetDenied as error:
            assert str(error) == "request_accounting_incomplete"
            return None

    with ThreadPoolExecutor(max_workers=4) as pool:
        claims = list(pool.map(reserve, range(4)))
    assert sum(claim is not None for claim in claims) == 1
    result = CallBudget(budget.path).report()
    assert len(result["requests"]) == 1
    assert 0 < result["reserved_estimate_nano_usd"] <= budget.limits.estimate_ceiling_nano_usd
    assert result["requests"][0]["id"] == next(claim for claim in claims if claim)


@pytest.mark.parametrize("after_commit", [False, True])
def test_reservation_persistence_failure_never_dispatches(
    runtime, tmp_path, monkeypatch, after_commit
):
    session, store, goal = bound(runtime, tmp_path)
    budget = initialized(tmp_path)
    original = budget.connect
    calls = []

    @contextmanager
    def connection(*, write=False):
        with original(write=write) as db:
            yield db
            pending = db.execute("SELECT 1 FROM requests WHERE status='pending'").fetchone()
            if write and pending and not after_commit:
                raise sqlite3.OperationalError("private-marker")
        if write and pending and after_commit:
            raise sqlite3.OperationalError("private-marker")

    monkeypatch.setattr(budget, "connect", connection)

    async def respond(request):
        calls.append(1)
        raise AssertionError("must not dispatch")

    with pytest.raises(BudgetDenied, match="cleanup_incomplete") if after_commit else nullcontext():
        asyncio.run(run_mock_buyer(session, store, goal, budget, "first", respond))
    assert not calls and authority(runtime) == (0, 0, 0)
    record = CallBudget(budget.path).report()
    assert len(record["requests"]) == int(after_commit)
    if after_commit:
        assert record["reserved_estimate_nano_usd"] > 0


@pytest.mark.parametrize("category", ["instructions", "tools", "output_schema"])
def test_full_serialized_limit_includes_non_input_contributions(
    runtime, tmp_path, monkeypatch, category
):
    from pydantic import Field, create_model

    from agentic_payments_lab.buyer_agent import runner

    session, store, goal = bound(runtime, tmp_path)
    budget = initialized(tmp_path, max_request_bytes=8000)
    if category == "instructions":
        monkeypatch.setattr(runner, "INSTRUCTIONS", "I" * 12_000)
    elif category == "tools":
        original = runner.BoundedModel.tools

        def tools(self):
            result = original(self)
            result[0].description = "T" * 12_000
            return result

        monkeypatch.setattr(runner.BoundedModel, "tools", tools)
    else:
        monkeypatch.setattr(
            runner,
            "Explanation",
            create_model("Explanation", explanation=(str, Field(description="S" * 12_000))),
        )
    calls = []

    async def respond(request):
        calls.append(request)
        raise AssertionError("oversized dispatch")

    report = asyncio.run(run_mock_buyer(session, store, goal, budget, "large", respond))
    assert report.termination == "model_error"  # The old input-only 32 KiB check would pass.
    assert not calls and budget.report()["requests"] == []
    assert authority(runtime) == (0, 0, 0)


def test_accumulated_history_counts_again_on_each_wire_request(runtime, tmp_path):
    session, store, goal = bound(runtime, tmp_path)
    first = initialized(tmp_path, requests_per_invocation=1)
    bodies = []

    async def respond(request):
        bodies.append(request.content)
        return httpx2.Response(
            200, json=payload(len(bodies), tool_response("get_purchase_status", {}, "call").output)
        )

    asyncio.run(run_mock_buyer(session, store, goal, first, "measure", respond))
    budget = CallBudget.initialize(
        tmp_path / "history.sqlite3", limits(max_request_bytes=len(bodies[0]) + 32)
    )
    bodies.clear()
    report = asyncio.run(run_mock_buyer(session, store, goal, budget, "history", respond))
    assert len(bodies) == 1 and report.model_usage.requests_started == 2
    assert len(budget.report()["requests"]) == 1
    assert report.termination == "model_error"


@pytest.mark.parametrize("after_payment,swallow", [(False, False), (False, True), (True, False)])
def test_cancellation_retains_partial_usage_and_settled_state(
    runtime, tmp_path, after_payment, swallow
):
    session, store, goal = bound(runtime, tmp_path, free=not after_payment)
    budget = initialized(tmp_path)
    calls = []

    async def exercise():
        entered = asyncio.Event()

        async def respond(request):
            body = json.loads(request.content)
            calls.append(body)
            number = len(calls)
            if not after_payment or number == 3:
                entered.set()
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    if not swallow:
                        raise
                    return httpx2.Response(
                        200,
                        json=payload(
                            number,
                            tool_response("purchase", {"quote_id": "fabricated"}, "late").output,
                        ),
                    )
            if number == 1:
                response = tool_response("get_quote", {"service_id": "report-standard"}, "quote")
            else:
                outputs = [i for i in body["input"] if i.get("type") == "function_call_output"]
                response = tool_response(
                    "purchase", {"quote_id": json.loads(outputs[-1]["output"])["quote_id"]}, "buy"
                )
            return httpx2.Response(200, json=payload(number, response.output))

        task = asyncio.create_task(run_mock_buyer(session, store, goal, budget, "cancel", respond))
        await asyncio.wait_for(entered.wait(), 10)
        task.cancel("PRIVATE_CANCELLATION_MARKER")
        with pytest.raises(asyncio.CancelledError) as error:
            await task
        assert "PRIVATE" not in str(error.value)

    asyncio.run(exercise())
    result = CallBudget(budget.path).report()
    assert len(calls) == len(result["requests"]) == (3 if after_payment else 1)
    assert result["invocations"][0]["outcome"] == "cancelled"
    assert result["blocked"] and not result["usage_complete"]
    assert result["requests"][-1]["usage"] is None
    assert result["known_estimate_nano_usd"] == (124 if after_payment else None)
    assert result["estimated_total_nano_usd"] is None
    assert result["reserved_estimate_nano_usd"] > 0
    with pytest.raises(BudgetDenied):
        CallBudget(budget.path).begin_invocation("restart")
    assert authority(runtime) == ((1, 1, 10_000) if after_payment else (0, 0, 0))
    if after_payment:
        status = runtime.buyer("task").status()
        assert status.payment_state == "settled" and status.delivery_state == "delivered"
    assert b"PRIVATE_CANCELLATION_MARKER" not in budget.path.read_bytes()


def test_cooperative_timeout_holds_uncertain_request(runtime, tmp_path):
    session, store, goal = bound(runtime, tmp_path)
    budget = initialized(tmp_path, timeout_seconds=1)

    async def respond(request):
        await asyncio.Event().wait()

    report = asyncio.run(run_mock_buyer(session, store, goal, budget, "timeout", respond))
    assert report.termination == "timeout"
    result = CallBudget(budget.path).report()
    assert result["invocations"][0]["outcome"] == "timeout"
    assert result["blocked"] and result["requests"][0]["usage"] is None
    assert result["reserved_estimate_nano_usd"] > 0 and authority(runtime) == (0, 0, 0)
