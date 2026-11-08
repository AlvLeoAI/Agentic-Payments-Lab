import asyncio
import gc
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import httpx2
import pytest
from openai.types.responses import ResponseUsage
from pilot_support import initialized, response, usage
from pydantic import ValidationError
from test_buyer_agent import authority
from test_call_budget import bound

from agentic_payments_lab.buyer_agent.call_budget import BudgetDenied, CallBudget, usage_evidence
from agentic_payments_lab.buyer_agent.pilot_preparation import (
    initialize_pilot,
    pilot_limits,
    pilot_preflight,
    run_pilot_preparation,
)
from agentic_payments_lab.buyer_agent.pilot_pricing import LunaRates
from agentic_payments_lab.buyer_agent.scripted import ScriptedBuyer, tool_response


def test_actual_sdk_wire_path_pricing_and_payment_replay(runtime, tmp_path, monkeypatch):
    session, metadata, goal = bound(runtime, tmp_path, free=False)
    budget = initialized(tmp_path, session, goal)
    bodies = []

    async def no_network(*args, **kwargs):
        pytest.fail("Default network transport was invoked")

    monkeypatch.setattr(httpx2.AsyncHTTPTransport, "handle_async_request", no_network)
    for invocation in ("paid", "replay"):
        scripted = ScriptedBuyer()

        async def respond(request, budget=budget, scripted=scripted):
            body = json.loads(request.content)
            bodies.append(body)
            pending = budget.report()["requests"][-1]
            assert pending["status"] == "pending"
            assert pending["body_bytes"] == len(request.content)
            assert pending["reserved_nano"] == (len(request.content) + 1024) * 250 + 512 * 500
            output = await scripted.get_response(input=body["input"])
            return httpx2.Response(200, json=response(len(bodies), output.output))

        result = asyncio.run(
            run_pilot_preparation(session, metadata, goal, budget, invocation, respond)
        )
        assert result["mode"] == "offline_pilot_preparation" and not result["live_authorized"]
        assert result["live_calls"] == 0 and result["agent"]["termination"] == "completed"
        assert result["agent"]["purchase"]["payment_state"] == "settled"
        assert result["agent"]["purchase"]["delivery_state"] == "delivered"
        assert authority(runtime) == (1, 1, 10_000)
        budget = CallBudget(budget.path)
    assert len(bodies) == 7
    for body in bodies:
        assert body["model"] == "gpt-6-luna" and body["reasoning"] == {"effort": "none"}
        assert body["service_tier"] == "default" and body["max_output_tokens"] == 512
        assert body["parallel_tool_calls"] is False and body["store"] is False
        assert {t["name"] for t in body["tools"]} == {
            "list_services",
            "get_quote",
            "purchase",
            "get_purchase_status",
        }
        assert all(t["strict"] for t in body["tools"])
        assert body["text"]["format"]["strict"]
        items = body["input"]
        assert [i["call_id"] for i in items if i.get("type") == "function_call"] == [
            i["call_id"] for i in items if i.get("type") == "function_call_output"
        ]
    record = budget.report()
    # Ordinary 5*100 + reads 2*10 + 30m write 1*125 + 12h writes 2*250 + output 5*500.
    assert record["known_estimate_nano_usd"] == 7 * 3645
    assert record["usage_complete"] and record["reserved_estimate_nano_usd"] == 0
    assert record["cache_write_lifetime"] == "documented_30m_and_12h"
    assert record["requests"][0]["usage"]["ttl_12h_tokens"] == 2


def test_supported_initializer_checks_runtime_before_any_state(tmp_path, monkeypatch):
    from agentic_payments_lab.buyer_agent import pilot_preparation
    from agentic_payments_lab.runtime_guard import RuntimeUnverified

    def refused():
        raise RuntimeUnverified("isolated_pilot_runtime_unverified")

    def never(*args, **kwargs):
        pytest.fail("State was opened before runtime verification")

    monkeypatch.setattr(pilot_preparation, "require_pilot_runtime", refused)
    monkeypatch.setattr(pilot_preparation, "Runtime", never)
    monkeypatch.setattr(pilot_preparation, "AgentStore", never)
    monkeypatch.setattr(CallBudget, "initialize", never)
    path = tmp_path / "pilot"
    with pytest.raises(RuntimeUnverified):
        initialize_pilot(path, goal=None, grant=None, task_id="task", estimate_ceiling_nano_usd=0)
    assert not path.exists() and list(tmp_path.iterdir()) == []
    path.mkdir()
    marker = path / "existing"
    marker.write_bytes(b"untouched")
    with pytest.raises(RuntimeUnverified):
        initialize_pilot(path, goal=None, grant=None, task_id="task", estimate_ceiling_nano_usd=0)
    assert list(path.iterdir()) == [marker] and marker.read_bytes() == b"untouched"


def test_supported_initializer_uses_fresh_explicit_bound_state_only(tmp_path):
    from agentic_payments_lab.buyer_agent.contracts import Goal
    from agentic_payments_lab.clock import FixedClock
    from agentic_payments_lab.domain import Grant

    now = FixedClock().now()
    inputs = {
        "goal": Goal(subject="synthetic-pilot", purpose="research", quality="basic"),
        "grant": Grant(
            grant_id="pilot-grant",
            operator_id="operator",
            service_ids=("report-free",),
            purpose="research",
            budget=0,
            per_purchase_limit=0,
            issued_at=now,
            expires_at=now + 3600,
        ),
        "task_id": "pilot-task",
        "estimate_ceiling_nano_usd": 250_000_000,
    }
    path = tmp_path / "pilot"
    prepared = initialize_pilot(path, **inputs)
    assert prepared.session.context.task_id == "pilot-task"
    assert prepared.budget.report()["requests"] == []
    assert (
        prepared.metadata.bind(
            prepared.session.context, prepared.goal, prepared.session.agent_binding()
        )
        is False
    )
    assert authority(prepared.runtime) == (0, 0, 0)
    before = {f.name: f.read_bytes() for f in path.iterdir()}
    with pytest.raises(FileExistsError):
        initialize_pilot(path, **inputs)
    gc.collect()  # Deferred reader cleanup must not change the preserved file set.
    assert {f.name: f.read_bytes() for f in path.iterdir()} == before


def test_pinned_sdk_preserves_documented_lifetime_extras_and_no_double_counting():
    raw = usage()
    decoded = ResponseUsage.model_validate(raw).model_dump()
    assert decoded == raw  # OpenAI 3.42.1 retains documented fields via model extras.
    known, complete = usage_evidence(decoded, cache_lifetimes=True)
    assert complete and LunaRates().estimate(known) == 3645
    raw.update(input_tokens=12000, output_tokens=0, total_tokens=12000)
    raw["input_tokens_details"] = {
        "cached_tokens": 4000,
        "cache_write_tokens": 7000,
        "cache_write_tokens_details": {"ttl_30m_tokens": 3000, "ttl_12h_tokens": 4000},
    }
    raw["output_tokens_details"]["reasoning_tokens"] = 0
    known, complete = usage_evidence(raw, cache_lifetimes=True)
    assert complete and LunaRates().estimate(known) == 1_515_000


@pytest.mark.parametrize(
    "field,value",
    [
        ("input_nano_usd", 99),
        ("input_nano_usd", 100.0),
        ("write_12h_nano_usd", 125),
        ("output_nano_usd", "500"),
        ("source", "caller_rates"),
        ("pricing_url", "https://untrusted.invalid"),
    ],
)
def test_rate_card_cannot_be_silently_repriced(field, value):
    with pytest.raises(ValidationError):
        LunaRates(**{field: value})


@pytest.mark.parametrize(
    "field,value",
    [
        ("reasoning_effort", "low"),
        ("service_tier", "flex"),
        ("max_request_bytes", 32769),
        ("input_tokens_per_byte", 2),
        ("framing_tokens", 0),
        ("max_requests", 19),
        ("requests_per_invocation", 7),
    ],
)
def test_pilot_configuration_cannot_exceed_approved_preparation(runtime, tmp_path, field, value):
    session, _, goal = bound(runtime, tmp_path)
    with pytest.raises(ValidationError):
        pilot_limits(session, goal, estimate_ceiling_nano_usd=250_000_000, **{field: value})


@pytest.mark.parametrize(
    "mutation",
    [
        "lifetime_missing",
        "one_lifetime_missing",
        "bad_sum",
        "overlap",
        "boolean",
        "negative",
        "float",
        "unknown_category",
        "unknown_lifetime",
        "usage_missing",
        "model",
        "tier",
        "long_context",
    ],
)
def test_uncertain_usage_and_identity_stop_before_tools(runtime, tmp_path, mutation):
    session, metadata, goal = bound(runtime, tmp_path)
    budget = initialized(tmp_path, session, goal)
    raw = response(1, tool_response("get_purchase_status", {}, "status").output)
    details = raw["usage"]["input_tokens_details"]
    lifetimes = details["cache_write_tokens_details"]
    if mutation == "lifetime_missing":
        del details["cache_write_tokens_details"]
    elif mutation == "one_lifetime_missing":
        del lifetimes["ttl_12h_tokens"]
    elif mutation == "bad_sum":
        lifetimes["ttl_12h_tokens"] = 3
    elif mutation == "overlap":
        details["cached_tokens"] = 8
    elif mutation in ("boolean", "negative", "float"):
        lifetimes["ttl_12h_tokens"] = {"boolean": True, "negative": -1, "float": 2.0}[mutation]
    elif mutation == "unknown_category":
        raw["usage"]["audio_tokens"] = 1
    elif mutation == "unknown_lifetime":
        lifetimes["ttl_24h_tokens"] = 2
    elif mutation == "usage_missing":
        raw["usage"] = {"input_tokens": 10}
    elif mutation == "model":
        raw["model"] = "other-model"
    elif mutation == "tier":
        raw["service_tier"] = "priority"
    else:
        raw["usage"].update(input_tokens=272001, total_tokens=272006)
    calls = []

    async def respond(request):
        calls.append(1)
        return httpx2.Response(200, json=raw)

    result = asyncio.run(run_pilot_preparation(session, metadata, goal, budget, "one", respond))
    assert result["agent"]["termination"] == "model_error"
    assert result["agent"]["tool_attempts"] == 0 and calls == [1]
    record = CallBudget(budget.path).report()
    assert record["blocked"] and record["reserved_estimate_nano_usd"] > 0
    assert record["known_estimate_nano_usd"] is record["estimated_total_nano_usd"] is None
    assert record["requests"][0]["usage"]["input_tokens"] in (10, 272001)
    assert authority(runtime) == (0, 0, 0)
    with pytest.raises(BudgetDenied):
        asyncio.run(
            run_pilot_preparation(
                session, metadata, goal, CallBudget(budget.path), "again", respond
            )
        )
    assert calls == [1]


def test_preflight_is_not_spending_authority_and_live_activation_refuses(runtime, tmp_path):
    session, metadata, goal = bound(runtime, tmp_path)
    budget = initialized(tmp_path, session, goal)
    before = budget.path.read_bytes()
    result = pilot_preflight(session, goal, budget)
    assert result["ordinary_input_plan_nano_usd"] == 65_433_600
    assert result["worst_write_plan_nano_usd"] == 156_672_000
    assert result["estimated_input_tokens_per_request"] == 33792
    assert result["maximum_planned_requests"] == 18
    assert not any(
        result[k]
        for k in (
            "live_authorized",
            "credentials_loaded",
            "model_access_verified",
            "credit_balance_verified",
            "billing_cap_guaranteed",
        )
    )
    with pytest.raises(BudgetDenied, match="activation_not_authorized"):
        asyncio.run(
            run_pilot_preparation(session, metadata, goal, budget, "live", None, mode="live")
        )
    with pytest.raises(BudgetDenied, match="task_binding"):
        pilot_preflight(session, goal.model_copy(update={"subject": "changed"}), budget)
    assert budget.path.read_bytes() == before and authority(runtime) == (0, 0, 0)


def test_pilot_preparation_keeps_x402_scripted_only(tmp_path):
    from offline_buyer_support import counters, setup

    shop, buyer, metadata, goal = setup(tmp_path)
    with pytest.raises(BudgetDenied, match="simulator_required"):
        pilot_limits(buyer, goal, estimate_ceiling_nano_usd=250_000_000)
    assert counters(shop) == (0, 0, 0, 0, 0)


@pytest.mark.parametrize("status", [200, 429, 500])
def test_request_bound_and_zero_retries_with_sourced_rates(runtime, tmp_path, status):
    session, metadata, goal = bound(runtime, tmp_path)
    budget = initialized(tmp_path, session, goal)
    calls, active = [], 0

    async def respond(request):
        nonlocal active
        active += 1
        assert active == 1
        try:
            await asyncio.sleep(0)
            calls.append(1)
            return httpx2.Response(
                status,
                json=response(
                    len(calls), tool_response("get_purchase_status", {}, f"s-{len(calls)}").output
                ),
            )
        finally:
            active -= 1

    result = asyncio.run(run_pilot_preparation(session, metadata, goal, budget, "bounded", respond))
    assert len(calls) == (6 if status == 200 else 1)
    assert result["agent"]["termination"] == ("turn_limit" if status == 200 else "model_error")
    assert authority(runtime) == (0, 0, 0)


def test_zero_planning_budget_refuses_before_mock_dispatch(runtime, tmp_path):
    session, metadata, goal = bound(runtime, tmp_path)
    budget = initialized(tmp_path, session, goal, ceiling=0)

    async def respond(request):
        pytest.fail("No request is affordable")

    result = asyncio.run(run_pilot_preparation(session, metadata, goal, budget, "zero", respond))
    assert result["agent"]["termination"] == "model_error"
    assert budget.report()["requests"] == [] and authority(runtime) == (0, 0, 0)


@pytest.mark.parametrize("after_payment,swallow", [(False, False), (False, True), (True, False)])
def test_cancellation_retains_priced_partial_usage_and_payment(
    runtime, tmp_path, after_payment, swallow
):
    session, metadata, goal = bound(runtime, tmp_path, free=not after_payment)
    budget = initialized(tmp_path, session, goal)
    calls = []

    async def exercise():
        entered = asyncio.Event()

        async def respond(request):
            body = json.loads(request.content)
            calls.append(1)
            if not after_payment or len(calls) == 3:
                entered.set()
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    if not swallow:
                        raise
                    return httpx2.Response(
                        200,
                        json=response(
                            1, tool_response("purchase", {"quote_id": "late"}, "late").output
                        ),
                    )
            if len(calls) == 1:
                output = tool_response("get_quote", {"service_id": "report-standard"}, "q").output
            else:
                last = [i for i in body["input"] if i.get("type") == "function_call_output"][-1]
                output = tool_response(
                    "purchase", {"quote_id": json.loads(last["output"])["quote_id"]}, "p"
                ).output
            return httpx2.Response(200, json=response(len(calls), output))

        task = asyncio.create_task(
            run_pilot_preparation(session, metadata, goal, budget, "cancel", respond)
        )
        await asyncio.wait_for(entered.wait(), 10)
        task.cancel("PRIVATE_CANCEL")
        with pytest.raises(asyncio.CancelledError, match="offline_mock"):
            await task

    asyncio.run(exercise())
    record = CallBudget(budget.path).report()
    assert len(record["requests"]) == (3 if after_payment else 1)
    assert record["known_estimate_nano_usd"] == (2 * 3645 if after_payment else None)
    assert not record["usage_complete"] and record["estimated_total_nano_usd"] is None
    assert record["reserved_estimate_nano_usd"] > 0 and record["blocked"]
    assert record["invocations"][0]["outcome"] == "cancelled"
    with pytest.raises(BudgetDenied):
        CallBudget(budget.path).begin_invocation("restart")
    assert authority(runtime) == ((1, 1, 10000) if after_payment else (0, 0, 0))
    if after_payment:
        assert session.status().payment_state == "settled"
        assert session.status().delivery_state == "delivered"
    assert b"PRIVATE_CANCEL" not in budget.path.read_bytes()


def test_timeout_retains_reservation(runtime, tmp_path):
    session, metadata, goal = bound(runtime, tmp_path)
    budget = initialized(tmp_path, session, goal, timeout_seconds=1)

    async def respond(request):
        await asyncio.Event().wait()

    result = asyncio.run(run_pilot_preparation(session, metadata, goal, budget, "timeout", respond))
    assert result["agent"]["termination"] == "timeout"
    assert (
        result["call_budget"]["blocked"] and result["call_budget"]["reserved_estimate_nano_usd"] > 0
    )
    assert authority(runtime) == (0, 0, 0)


@pytest.mark.parametrize("after_commit", [False, True])
def test_priced_reservation_commit_failure_never_dispatches(
    runtime, tmp_path, monkeypatch, after_commit
):
    session, metadata, goal = bound(runtime, tmp_path)
    budget = initialized(tmp_path, session, goal)
    original = budget.reserve

    def failed(*args):
        if after_commit:
            original(*args)
        raise sqlite3.OperationalError("PRIVATE_FAILURE")

    monkeypatch.setattr(budget, "reserve", failed)

    async def respond(request):
        pytest.fail("Reservation was not acknowledged")

    result = asyncio.run(run_pilot_preparation(session, metadata, goal, budget, "failure", respond))
    assert result["agent"]["termination"] == "model_error"
    assert len(budget.report()["requests"]) == int(after_commit)
    assert authority(runtime) == (0, 0, 0)


@pytest.mark.parametrize("category", ["instructions", "tools", "output_schema"])
def test_pilot_full_body_bound_covers_schemas_and_instructions(
    runtime, tmp_path, monkeypatch, category
):
    from pydantic import Field, create_model

    from agentic_payments_lab.buyer_agent import runner

    session, metadata, goal = bound(runtime, tmp_path)
    budget = initialized(tmp_path, session, goal, max_request_bytes=8000)
    if category == "instructions":
        monkeypatch.setattr(runner, "INSTRUCTIONS", "I" * 12000)
    elif category == "tools":
        original = runner.BoundedModel.tools

        def tools(self):
            result = original(self)
            result[0].description = "T" * 12000
            return result

        monkeypatch.setattr(runner.BoundedModel, "tools", tools)
    else:
        monkeypatch.setattr(
            runner,
            "Explanation",
            create_model("Explanation", explanation=(str, Field(description="S" * 12000))),
        )

    async def respond(request):
        pytest.fail("The complete body exceeds the limit")

    result = asyncio.run(run_pilot_preparation(session, metadata, goal, budget, "size", respond))
    assert result["agent"]["termination"] == "model_error"
    assert budget.report()["requests"] == [] and authority(runtime) == (0, 0, 0)


@pytest.mark.parametrize(
    "option",
    [{"background": True}, {"prompt_cache_options": {"ttl": "24h"}}, {"service_tier": "flex"}],
)
def test_unapproved_wire_options_stop_before_reservation(runtime, tmp_path, monkeypatch, option):
    from dataclasses import replace

    from agents import OpenAIResponsesModel

    session, metadata, goal = bound(runtime, tmp_path)
    budget = initialized(tmp_path, session, goal)
    original = OpenAIResponsesModel.get_response

    async def inject(self, **kwargs):
        kwargs["model_settings"] = replace(kwargs["model_settings"], extra_body=option)
        return await original(self, **kwargs)

    monkeypatch.setattr(OpenAIResponsesModel, "get_response", inject)

    async def respond(request):
        pytest.fail("Unsupported priced operation")

    result = asyncio.run(run_pilot_preparation(session, metadata, goal, budget, "option", respond))
    assert result["agent"]["termination"] == "model_error"
    assert budget.report()["requests"] == [] and authority(runtime) == (0, 0, 0)


def test_eighteen_requests_and_three_invocations_survive_reopen(runtime, tmp_path):
    session, metadata, goal = bound(runtime, tmp_path)
    budget = initialized(tmp_path, session, goal)
    calls = []

    async def respond(request):
        calls.append(1)
        return httpx2.Response(
            200,
            json=response(
                len(calls), tool_response("get_purchase_status", {}, str(len(calls))).output
            ),
        )

    for number in range(3):
        result = asyncio.run(
            run_pilot_preparation(session, metadata, goal, budget, str(number), respond)
        )
        assert result["agent"]["termination"] == "turn_limit"
        budget = CallBudget(budget.path)
    assert len(calls) == len(budget.report()["requests"]) == 18
    with pytest.raises(BudgetDenied, match="invocation_limit"):
        asyncio.run(run_pilot_preparation(session, metadata, goal, budget, "fourth", respond))
    assert len(calls) == 18 and authority(runtime) == (0, 0, 0)


@pytest.mark.parametrize("boundary", ["invocation", "reservation", "dispatch", "usage"])
def test_process_exit_retains_priced_journal_and_fences_restart(runtime, tmp_path, boundary):
    session, _, goal = bound(runtime, tmp_path)
    budget = initialized(tmp_path, session, goal)
    command = [
        sys.executable,
        str(Path(__file__).with_name("pilot_worker.py")),
        str(tmp_path),
        boundary,
    ]
    stdout, stderr = tmp_path / "worker.stdout", tmp_path / "worker.stderr"
    try:
        with stdout.open("w") as out, stderr.open("w") as err:
            completed = subprocess.run(command, stdout=out, stderr=err, timeout=30)
        failure = None if completed.returncode == 83 else f"exit={completed.returncode}"
    except subprocess.TimeoutExpired:
        failure = "worker timeout"
    if failure:
        # Full diagnostics are in the assertion as well as disposable artifact files.
        pytest.fail(
            f"{failure}\nSTDOUT:\n{stdout.read_text()}\nSTDERR:\n{stderr.read_text()}",
            pytrace=False,
        )
    reopened = CallBudget(budget.path)
    record = reopened.report()
    assert record["blocked"] and len(record["invocations"]) == 1
    assert len(record["requests"]) == int(boundary != "invocation")
    transport = tmp_path / "transport.sqlite3"
    with sqlite3.connect(transport) as db:
        dispatches = db.execute("SELECT count(*) FROM calls").fetchone()[0]
    assert dispatches == int(boundary in ("dispatch", "usage"))
    if boundary in ("reservation", "dispatch"):
        assert record["reserved_estimate_nano_usd"] > 0
        assert record["estimated_total_nano_usd"] is None
    if boundary == "usage":
        assert record["known_estimate_nano_usd"] == 3645 and record["usage_complete"]
    with pytest.raises(BudgetDenied):
        reopened.begin_invocation("replacement")
    with pytest.raises(BudgetDenied):
        reopened.reserve("worker", b"{}")
    assert authority(runtime) == (0, 0, 0)
