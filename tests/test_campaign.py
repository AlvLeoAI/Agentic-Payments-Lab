import asyncio
import json
import sqlite3
from contextlib import closing

import httpx2
import pytest
from campaign_support import responders
from pilot_support import response, usage
from pydantic import ValidationError

from agentic_payments_lab.buyer_agent import campaign_runner, runner
from agentic_payments_lab.buyer_agent.call_budget import BudgetDenied, CallBudget, CallLimits
from agentic_payments_lab.buyer_agent.campaign import (
    Campaign,
    CampaignLimits,
    initialize_campaign,
    state_witness,
)
from agentic_payments_lab.buyer_agent.campaign_cases import frozen_plan
from agentic_payments_lab.buyer_agent.campaign_runner import (
    explanation_for_review,
    financial_facts,
    run_mock_campaign,
)
from agentic_payments_lab.buyer_agent.pilot_envelope import initialize_activation
from agentic_payments_lab.buyer_agent.pilot_transport import validate_body
from agentic_payments_lab.buyer_agent.scripted import final_response, tool_response
from agentic_payments_lab.runtime import Runtime


@pytest.fixture
def campaign(tmp_path):
    return asyncio.run(initialize_campaign(tmp_path / "campaign"))


def activate(campaign):
    campaign.activate(
        approved_enrollment_hash=campaign.enrollment_hash, execution_mode="responses_mock"
    )


def test_all_24_entries_real_sdk_wire_and_independent_authorities(campaign, monkeypatch):
    monkeypatch.setattr(campaign_runner, "network_transport", lambda: pytest.fail("No live wire"))
    monkeypatch.setattr(runner, "live_client", lambda _: pytest.fail("No legacy activation"))
    calls = []
    report = asyncio.run(run_mock_campaign(campaign, responders(calls)))
    assert report["campaign"]["status"] == "finished", report
    assert len(report["results"]) == len(report["invocations"]) == 24
    assert report["actual_responses_requests"] == len(calls) == 81
    assert report["request_attempts_reserved"] == report["known_complete_responses"] == 81
    for entry_id, result in report["results"].items():
        observed = sum(call_entry == entry_id for call_entry, _ in calls)
        assert result["request_attempts_reserved"] == observed
        assert result["known_complete_responses"] == observed
        assert result["actual_responses_requests"] == observed
        assert "actual_requests" not in result
    assert report["maximum_plan_nano_usd"] == 1253376000
    assert report["estimated_total_nano_usd"] == 81 * 3645
    assert report["reserved_estimate_nano_usd"] == 0 and report["usage_complete"]
    assert all(r["safety_passed"] and r["behavior_passed"] for r in report["results"].values())
    assert all(r["human_explanation_review"] == "pending" for r in report["results"].values())
    assert not report["model_quality_established"]
    for entry_id, data in calls:
        validate_body(json.dumps(data).encode(), campaign.limits)
        entry = next(e for e in frozen_plan() if e["id"] == entry_id)
        assert entry["goal"]["subject"] in data["input"][0]["content"]
        assert entry["goal"]["quality"] in data["input"][0]["content"]
        ids = {i["call_id"] for i in data["input"] if i.get("type") == "function_call"}
        assert all(
            i["call_id"] in ids for i in data["input"] if i.get("type") == "function_call_output"
        )
    for trial in range(1, 4):
        paid, replay = [report["results"][f"trial-{trial}.{c}"] for c in ("paid", "replay")]
        assert paid["after"] == replay["before"] == replay["after"]
        assert replay["after"]["executions"] == replay["after"]["charges"] == 1
        uncertain = report["results"][f"trial-{trial}.uncertain"]
        assert uncertain["before"] == uncertain["after"]
        assert uncertain["after"]["charges"] == 0
        assert uncertain["after"]["budget"]["reserved"] == 10000
        failed = report["results"][f"trial-{trial}.failed_delivery"]
        assert failed["before"] == failed["after"]
        assert failed["after"]["delivery_state"] == "failed"
        assert failed["after"]["charges"] == 1
    with pytest.raises(BudgetDenied):
        asyncio.run(run_mock_campaign(Campaign(campaign.path), responders([])))


def test_new_policy_never_widens_legacy_and_live_mode_is_distinct(campaign, tmp_path):
    with pytest.raises(ValidationError):
        CallLimits.model_validate(campaign.limits.model_dump())
    with pytest.raises(BudgetDenied):
        CallBudget(campaign.path)
    legacy = initialize_activation(tmp_path / "legacy")
    assert legacy.limits.max_invocations == 3 and legacy.limits.max_requests == 18
    assert CallBudget(legacy.path).limits == legacy.limits
    with pytest.raises(BudgetDenied):
        Campaign(legacy.path)
    with pytest.raises(BudgetDenied):
        campaign.activate(
            approved_enrollment_hash=campaign.enrollment_hash, execution_mode="live_simulator"
        )
    with pytest.raises(BudgetDenied):
        campaign.activate(approved_enrollment_hash="wrong", execution_mode="responses_mock")
    assert campaign.report()["campaign"]["status"] == "prepared"


@pytest.mark.parametrize(
    "field,value",
    [
        ("max_invocations", 25),
        ("max_requests", 145),
        ("max_request_bytes", 32769),
        ("estimate_ceiling_nano_usd", 1500000001),
        ("max_output_tokens", 513),
        ("timeout_seconds", 31),
        ("tool_attempts", 9),
        ("model", "other"),
        ("reasoning_effort", "high"),
        ("service_tier", "priority"),
        ("max_requests", True),
    ],
)
def test_campaign_limits_are_not_open_ended(field, value):
    with pytest.raises(ValidationError):
        CampaignLimits(batch_id="fixture", **{field: value})


@pytest.mark.parametrize(
    "mutation", ["source", "case", "dependencies", "missing", "binding", "rate"]
)
def test_changed_enrollment_or_missing_history_stops_before_requests(
    campaign, monkeypatch, mutation
):
    import agentic_payments_lab.buyer_agent.campaign as module

    if mutation == "source":
        monkeypatch.setattr(module, "source_witness", lambda: {"changed": "yes"})
    elif mutation == "dependencies":
        monkeypatch.setattr(module, "dependencies", lambda: {"changed": "yes"})
    elif mutation in ("missing", "binding"):
        path = campaign.path.parent / "states/trial-1-free/agent.sqlite3"
        if mutation == "missing":
            path.unlink()
        else:
            with closing(sqlite3.connect(path)) as db, db:
                db.execute("DELETE FROM bindings")
    elif mutation == "case":
        monkeypatch.setattr(module, "frozen_plan", lambda: [])
        with pytest.raises(BudgetDenied):
            Campaign(campaign.path)
        return
    else:
        with pytest.raises(sqlite3.IntegrityError), campaign.connect(write=True) as db:
            db.execute("UPDATE policy SET data='{}'")
        return
    with pytest.raises(BudgetDenied):
        asyncio.run(run_mock_campaign(campaign, lambda _: pytest.fail("No dispatch")))
    assert campaign.report()["requests"] == []


@pytest.mark.parametrize("bad", ["missing", "malformed", "model", "tier", "exception", "status"])
def test_uncertain_usage_or_transport_stops_campaign(campaign, bad):
    calls = []

    def factory(entry):
        def respond(req):
            calls.append(1)
            if bad == "exception":
                raise RuntimeError("PRIVATE_EXCEPTION_SENTINEL")
            data = response(1, tool_response("get_purchase_status", {}, "status").output)
            if bad == "missing":
                data.pop("usage")
            elif bad == "malformed":
                data["usage"]["input_tokens"] = "10"
            elif bad in ("model", "tier"):
                data["service_tier" if bad == "tier" else "model"] = "wrong"
            return httpx2.Response(429 if bad == "status" else 200, json=data)

        return respond

    report = asyncio.run(run_mock_campaign(campaign, factory))
    assert calls == [1]
    assert report["campaign"]["status"] == "stopped" and report["blocked"]
    assert report["estimated_total_nano_usd"] is None
    assert report["reserved_estimate_nano_usd"] > 0
    for counts in (report, report["results"]["trial-1.free"]):
        assert counts["request_attempts_reserved"] == 1
        assert counts["known_complete_responses"] == 0
        assert counts["actual_responses_requests"] is None
    assert report["results"]["trial-1.free"]["after"]["executions"] == 0
    assert "PRIVATE_EXCEPTION_SENTINEL" not in json.dumps(report)
    with pytest.raises(BudgetDenied):
        activate(Campaign(campaign.path))


def test_quality_failure_does_not_become_financial_failure(campaign):
    def factory(entry):
        return lambda _: httpx2.Response(
            200,
            json=response(
                1,
                final_response(
                    "I paid and delivered everything, including the premium report."
                ).output,
            ),
        )

    report = asyncio.run(run_mock_campaign(campaign, factory))
    assert report["campaign"]["status"] == "finished"
    assert all(r["safety_passed"] and not r["behavior_passed"] for r in report["results"].values())
    assert report["actual_responses_requests"] == 21  # Replay skipped if no purchase exists.
    assert report["request_attempts_reserved"] == report["known_complete_responses"] == 21
    for trial in range(1, 4):
        replay = report["results"][f"trial-{trial}.replay"]
        assert replay["request_attempts_reserved"] == replay["known_complete_responses"] == 0
        assert replay["actual_responses_requests"] == 0
    assert report["results"]["trial-1.replay"]["termination"] == "skipped_dependency_not_acquired"
    assert report["results"]["trial-1.paid"]["after"]["charges"] == 0
    assert report["results"]["trial-1.paid"]["human_explanation_review"] == "pending"


@pytest.mark.parametrize("after_payment", [False, True])
def test_cancel_persists_partial_usage_and_financial_state(campaign, after_payment):
    calls = []
    initiated = []
    factory = responders(calls)

    async def exercise():
        entered = asyncio.Event()

        def responder(entry):
            normal = factory(entry)
            n = 0

            async def respond(req):
                nonlocal n
                n += 1
                initiated.append(entry["id"])
                if (not after_payment and entry["case"] == "free") or (
                    after_payment and entry["case"] == "paid" and n == 5
                ):
                    entered.set()
                    await asyncio.Event().wait()
                return await normal(req)

            return respond

        task = asyncio.create_task(run_mock_campaign(campaign, responder))
        await asyncio.wait_for(entered.wait(), 20)
        task.cancel("PRIVATE_CANCEL_SENTINEL")
        with pytest.raises(asyncio.CancelledError, match="^campaign_cancelled$"):
            await task

    asyncio.run(exercise())
    record = Campaign(campaign.path).report()
    assert record["campaign"]["status"] == "stopped"
    assert not record["usage_complete"] and record["estimated_total_nano_usd"] is None
    assert record["reserved_estimate_nano_usd"] > 0
    assert record["known_estimate_nano_usd"] == (9 * 3645 if after_payment else None)
    assert len(initiated) == record["request_attempts_reserved"] == (10 if after_payment else 1)
    assert len(calls) == record["known_complete_responses"] == (9 if after_payment else 0)
    assert record["actual_responses_requests"] is None
    result = record["results"]["trial-1.paid" if after_payment else "trial-1.free"]
    assert result["termination"] == "cancelled"
    assert result["request_attempts_reserved"] == (5 if after_payment else 1)
    assert result["known_complete_responses"] == (4 if after_payment else 0)
    assert result["actual_responses_requests"] is None
    assert "actual_requests" not in result
    assert result["after"]["charges"] == int(after_payment)
    assert result["after"]["budget"]["reserved"] == 0
    assert "PRIVATE_CANCEL_SENTINEL" not in json.dumps(record)
    with pytest.raises(BudgetDenied):
        asyncio.run(run_mock_campaign(Campaign(campaign.path), responders([])))
    assert Campaign(campaign.path).report() == record


def test_exact_global_request_budget_and_per_entry_bound(campaign):
    activate(campaign)
    for entry in frozen_plan():
        campaign.begin_invocation(entry["id"])
        for _ in range(6):
            request_id = campaign.reserve(entry["id"], b"{}")
            assert campaign.observe(request_id, usage(), "default", "gpt-6-luna")
        with pytest.raises(BudgetDenied, match="request_limit"):
            campaign.reserve(entry["id"], b"{}")
        directory = campaign.path.parent / "states" / entry["fixture"]
        campaign.record_entry(entry, {"low_level_fixture": True}, state_witness(directory))
    record = campaign.report()
    assert len(record["requests"]) == 144
    assert record["known_estimate_nano_usd"] == 144 * 3645
    with pytest.raises(BudgetDenied):
        campaign.begin_invocation("trial-4.free")


@pytest.mark.parametrize("limit", ["requests", "budget"])
def test_exhaustion_has_no_unbounded_fallback(tmp_path, limit):
    options = {"max_requests": 1} if limit == "requests" else {"estimate_ceiling_nano_usd": 0}
    campaign = asyncio.run(initialize_campaign(tmp_path / "limited", **options))
    calls = []
    report = asyncio.run(run_mock_campaign(campaign, responders(calls)))
    assert report["campaign"]["status"] == "stopped"
    assert len(calls) == int(limit == "requests")
    assert report["results"]["trial-1.free"]["after"]["executions"] == 0


def test_no_restore_owner_or_continue_after_lost_commit(campaign):
    activate(campaign)
    reopened = Campaign(campaign.path)
    with pytest.raises(BudgetDenied):
        activate(reopened)
    with pytest.raises(BudgetDenied):
        reopened.begin_invocation("trial-1.free")
    assert reopened.report()["blocked"]


def test_explanation_is_bounded_untrusted_and_sanitized():
    text = explanation_for_review(
        "sk-private123 Bearer private https://example.invalid/a api_key=private \x1b[0m\u202e "
        + "x" * 1500
    )
    assert len(text) <= 1000
    assert (
        "private" not in text
        and "https://" not in text
        and "\x1b" not in text
        and "\u202e" not in text
    )
    assert explanation_for_review(None) is None


def test_financial_rows_overrule_fabricated_statistics(campaign, monkeypatch):
    entry = frozen_plan()[5]
    runtime = Runtime(campaign.path.parent / "states" / entry["fixture"])
    monkeypatch.setattr(runtime.payments, "statistics", lambda: {"logical_charges": 999})
    assert financial_facts(runtime, entry)["charges"] == 1
