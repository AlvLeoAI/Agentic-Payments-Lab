"""Separately labelled simulator evaluation of the guarded Responses mock path."""

import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path

import httpx2

from ..domain import Grant
from ..runtime import Runtime
from .call_budget import BudgetDenied, CallBudget, CallLimits, PlanningRates
from .contracts import Goal
from .mock_responses import run_mock_buyer
from .scripted import ScriptedBuyer
from .store import AgentStore


def rows(path, sql, parameters=()):
    with closing(sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        db.execute("BEGIN")
        return [dict(r) for r in db.execute(sql, parameters)]


def score_wire_case(root, report, name):
    """Read-only independent checks after the invocation; no live quality score."""
    purchase = rows(root / "ledger.sqlite3", "SELECT * FROM purchases")[0]
    grant = rows(root / "ledger.sqlite3", "SELECT reserved,spent FROM grants")[0]
    authority = rows(root / "payments.sqlite3", "SELECT purchase_id,result FROM authorizations")
    attempts = rows(root / "payments.sqlite3", "SELECT * FROM attempts")
    traces = rows(
        root / "agent.sqlite3",
        "SELECT data FROM tool_events WHERE run_id=? ORDER BY sequence",
        (report.run_id,),
    )
    trace = [json.loads(r["data"]) for r in traces]
    persisted = rows(
        root / "agent.sqlite3", "SELECT data FROM runs WHERE run_id=?", (report.run_id,)
    )
    task = rows(
        root / "ledger.sqlite3",
        "SELECT task_id FROM tasks WHERE purchase_id=?",
        (purchase["purchase_id"],),
    )[0]
    receipt = json.loads(purchase["receipt"])
    requests = rows(root / "call-budget.sqlite3", "SELECT * FROM requests ORDER BY rowid")
    wire = rows(
        root / "model-mock.sqlite3", "SELECT body_hash,body_bytes FROM calls ORDER BY rowid"
    )
    per_call = [r for r in requests if r["invocation"] == name]
    expected = {"wire_paid": 5, "wire_replay": 2, "wire_missing_usage": 1}[name]
    expected_tools = {
        "wire_paid": ["get_purchase_status", "list_services", "get_quote", "purchase"],
        "wire_replay": ["get_purchase_status"],
        "wire_missing_usage": [],
    }[name]
    unknown = name == "wire_missing_usage"
    checks = {
        "host_termination": report.termination == ("model_error" if unknown else "completed"),
        "host_completion": report.run_complete
        and report.metadata_persisted
        and report.model_usage.incomplete == unknown,
        "authoritative_report_fields": report.task_id == task["task_id"]
        and report.purchase.amount_atomic == purchase["amount"] == 10_000
        and report.purchase.simulated_receipt_id == receipt["receipt_id"]
        and report.purchase.operator_reconciliation_required is False
        and report.decision == "purchase_recorded",
        "persisted_run_correspondence": len(persisted) == 1
        and json.loads(persisted[0]["data"])
        == report.model_dump(mode="json", exclude={"model_explanation"}),
        "authoritative_simulator_state": purchase["purchase_id"] == report.purchase.purchase_id
        and purchase["payment_state"] == report.purchase.payment_state == "settled"
        and purchase["delivery_state"] == report.purchase.delivery_state == "delivered"
        and grant == {"reserved": 0, "spent": 10_000},
        "one_authority_charge": len(attempts) == len(authority) == 1
        and authority[0]["purchase_id"] == purchase["purchase_id"]
        and json.loads(authority[0]["result"])["status"] == "settled"
        and json.loads(authority[0]["result"])["receipt"]["intent"]["amount"] == 10_000,
        "receipt_correspondence": len(authority) == 1
        and json.loads(authority[0]["result"])["receipt"] == receipt,
        "actual_tool_trace": [e["tool"] for e in trace] == expected_tools
        and trace == [e.model_dump() for e in report.events]
        and all(e["outcome"] == "ok" for e in trace)
        and report.tool_attempts == len(trace),
        "actual_dispatch_accounting": len(per_call) == expected
        and [{k: r[k] for k in ("body_hash", "body_bytes")} for r in requests] == wire,
        "complete_wire_bound": all(r["body_bytes"] <= 32_768 for r in requests),
        "usage_and_reservation": all(r["status"] == "complete" for r in requests[:-1])
        and requests[-1]["status"] == ("incomplete" if unknown else "complete")
        and (
            not unknown
            or (
                requests[-1]["reserved_nano"] > 0 and requests[-1]["observed_estimate_nano"] is None
            )
        ),
    }
    return {
        "case": name,
        "passed": all(checks.values()),
        "checks": checks,
        "observed": {
            "purchase_id": purchase["purchase_id"],
            "authority_executions": len(attempts),
            "authority_charges": len(authority),
            "spent": grant["spent"],
            "reserved": grant["reserved"],
            "mock_requests": len(per_call),
            "tool_trace": trace,
        },
    }


async def evaluate_wire(root):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=False)
    runtime = Runtime(root)
    now = runtime.clock.now()
    runtime.ledger.add_grant(
        Grant(
            grant_id="wire-grant",
            operator_id="offline-evaluator",
            service_ids=("report-standard",),
            purpose="research",
            budget=10_000,
            per_purchase_limit=10_000,
            issued_at=now,
            expires_at=now + 600,
        ),
        now,
    )
    runtime.ledger.create_task("wire-task", "wire-grant", now)
    goal = Goal(subject="synthetic-wire-evaluation", purpose="research", quality="standard")
    metadata = AgentStore(root / "agent.sqlite3")
    metadata.initialize(runtime.ledger, "wire-task", goal)
    budget = CallBudget.initialize(
        root / "call-budget.sqlite3",
        CallLimits(
            batch_id="offline-wire-evaluation",
            model="gpt-6-luna",
            estimate_ceiling_nano_usd=10_000_000,
            rates=PlanningRates(
                input_nano_usd=3,
                cached_input_nano_usd=1,
                cache_write_surcharge_nano_usd=2,
                output_nano_usd=6,
            ),
        ),
    )
    with sqlite3.connect(root / "model-mock.sqlite3") as db:
        db.execute("CREATE TABLE calls(body_hash TEXT NOT NULL,body_bytes INTEGER NOT NULL)")
    results = []
    for name in ("wire_paid", "wire_replay", "wire_missing_usage"):
        scripted = ScriptedBuyer()

        async def respond(request, _name=name, _scripted=scripted):
            with sqlite3.connect(root / "model-mock.sqlite3") as db:
                db.execute(
                    "INSERT INTO calls VALUES(?,?)",
                    (hashlib.sha256(request.content).hexdigest(), len(request.content)),
                )
            body = json.loads(request.content)
            response = await _scripted.get_response(input=body["input"])
            return httpx2.Response(
                200,
                json={
                    "id": f"{_name}-{_scripted.step}",
                    "object": "response",
                    "created_at": 0,
                    "status": "completed",
                    "model": "gpt-6-luna",
                    "service_tier": "default",
                    "parallel_tool_calls": False,
                    "output": [item.model_dump(mode="json") for item in response.output],
                    "usage": None
                    if _name == "wire_missing_usage"
                    else {
                        "input_tokens": 10,
                        "output_tokens": 5,
                        "total_tokens": 15,
                        "input_tokens_details": {"cached_tokens": 2, "cache_write_tokens": 3},
                        "output_tokens_details": {"reasoning_tokens": 1},
                    },
                },
            )

        report = await run_mock_buyer(
            runtime.buyer("wire-task"), metadata, goal, budget, name, respond
        )
        results.append(score_wire_case(root, report, name))
    before = rows(root / "model-mock.sqlite3", "SELECT * FROM calls")
    blocked = False
    try:
        await run_mock_buyer(
            runtime.buyer("wire-task"),
            metadata,
            goal,
            CallBudget(budget.path),
            "forbidden-resume",
            respond,
        )
    except BudgetDenied as error:
        blocked = str(error) == "request_accounting_incomplete"
    unchanged = rows(root / "model-mock.sqlite3", "SELECT * FROM calls") == before
    result = {
        "mode": "responses_mock_over_simulator",
        "live_calls": 0,
        "model": "gpt-6-luna",
        "billing_verified": False,
        "passed": blocked and unchanged and all(r["passed"] for r in results),
        "restart_fenced": blocked and unchanged,
        "cases": results,
        "call_budget": budget.report(),
    }
    return result
