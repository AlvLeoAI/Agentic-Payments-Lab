"""Scores must follow independent records, including intentionally false narratives."""

import asyncio
import hashlib
import json
import sqlite3

import httpx2
import pytest
from call_budget_support import initialized, payload
from test_call_budget import bound

from agentic_payments_lab.buyer_agent.contracts import AgentReport
from agentic_payments_lab.buyer_agent.evaluation import evaluate, score_case
from agentic_payments_lab.buyer_agent.mock_responses import run_mock_buyer
from agentic_payments_lab.buyer_agent.scripted import ScriptedBuyer
from agentic_payments_lab.buyer_agent.wire_evaluation import score_wire_case


def test_evaluation_covers_unknown_replay_operator_delivery_and_incident(tmp_path):
    root = tmp_path / "evaluation"
    result = asyncio.run(evaluate(root))
    assert result["passed"], json.dumps(result, indent=2)
    assert result["model_behavior"] == "scripted_not_quality_evidence"
    cases = result["cases"]
    assert [c["case"] for c in cases] == [
        "new_unknown",
        "replay_unknown",
        "operator_delivered",
        "incident_visible",
    ]
    assert len({c["observed"]["purchase_id"] for c in cases}) == 1
    assert [(c["observed"]["reserved"], c["observed"]["spent"]) for c in cases] == [
        (10_000, 0),
        (10_000, 0),
        (0, 10_000),
        (0, 10_000),
    ]
    assert all(c["observed"]["double_calls"] == ["signer", "dispatcher", "settler"] for c in cases)
    assert result["wire"]["passed"] and result["wire"]["restart_fenced"]
    assert result["wire"]["mode"] == "responses_mock_over_simulator"
    assert [c["observed"]["mock_requests"] for c in result["wire"]["cases"]] == [5, 2, 1]
    assert result["wire"]["call_budget"]["blocked"]
    assert result["wire"]["call_budget"]["estimated_total_nano_usd"] is None
    assert all(c["observed"]["authority_charges"] == 1 for c in result["wire"]["cases"])
    with sqlite3.connect(root / "authority.sqlite3") as db:
        assert db.execute("SELECT count(*),sum(amount) FROM transfers").fetchone() == (1, 10_000)
    with sqlite3.connect(root / "agent.sqlite3") as db:
        data = json.loads(
            db.execute("SELECT data FROM runs ORDER BY rowid DESC LIMIT 1").fetchone()[0]
        )
    report = AgentReport.model_validate(
        {**data, "model_explanation": "Real payment verified; all incidents resolved!"}
    )
    kwargs = dict(name="incident_visible", phase="fixture_settled", delivered=True, incident=True)
    # Prose cannot earn or erase any score.
    assert score_case(root, report, **kwargs)["passed"]
    lie = report.model_copy(
        update={"purchase": report.purchase.model_copy(update={"operator_incident": False})}
    )
    assert not score_case(root, lie, **kwargs)["checks"]["incident_visible"]
    with sqlite3.connect(root / "authority.sqlite3") as db:
        db.execute("INSERT INTO calls VALUES('dispatcher')")
    assert not score_case(root, report, **kwargs)["checks"]["one_double_execution"]
    with sqlite3.connect(root / "agent.sqlite3") as db:
        db.execute("DELETE FROM tool_events WHERE run_id=?", (report.run_id,))
    assert not score_case(root, report, **kwargs)["checks"]["actual_tool_trace"]


def test_evaluation_refuses_retained_state(tmp_path):
    marker = tmp_path / "retained.txt"
    marker.write_text("preserve")
    with pytest.raises(FileExistsError):
        asyncio.run(evaluate(tmp_path))
    assert list(tmp_path.iterdir()) == [marker]
    assert marker.read_text() == "preserve"


def test_wire_scorer_rejects_false_authoritative_fields(runtime, tmp_path):
    session, metadata, goal = bound(runtime, tmp_path, free=False)
    budget = initialized(tmp_path)
    with sqlite3.connect(tmp_path / "model-mock.sqlite3") as db:
        db.execute("CREATE TABLE calls(body_hash TEXT,body_bytes INTEGER)")
    scripted = ScriptedBuyer()

    async def respond(request):
        with sqlite3.connect(tmp_path / "model-mock.sqlite3") as db:
            db.execute(
                "INSERT INTO calls VALUES(?,?)",
                (hashlib.sha256(request.content).hexdigest(), len(request.content)),
            )
        result = await scripted.get_response(input=json.loads(request.content)["input"])
        return httpx2.Response(200, json=payload(scripted.step, result.output))

    report = asyncio.run(run_mock_buyer(session, metadata, goal, budget, "wire_paid", respond))
    assert score_wire_case(tmp_path, report, "wire_paid")["passed"]
    mutations = [
        {"purchase": report.purchase.model_copy(update={field: value})}
        for field, value in (
            ("amount_atomic", 0),
            ("purchase_id", "replacement"),
            ("simulated_receipt_id", "fabricated"),
            ("operator_reconciliation_required", True),
        )
    ] + [
        {"decision": "not_purchased"},
        {"task_id": "other"},
        {"metadata_persisted": False},
        {"run_complete": False},
        {"termination": "model_error"},
        {"model_usage": report.model_usage.model_copy(update={"incomplete": True})},
    ]
    for mutation in mutations:
        assert not score_wire_case(tmp_path, report.model_copy(update=mutation), "wire_paid")[
            "passed"
        ], mutation
    # Even when a forged run copy agrees, ledger-owned amount/receipt/decision must not pass.
    forged = report.model_copy(
        update={
            "purchase": report.purchase.model_copy(update={"amount_atomic": 0}),
            "decision": "not_purchased",
        }
    )
    with sqlite3.connect(tmp_path / "agent.sqlite3") as db:
        db.execute(
            "UPDATE runs SET data=? WHERE run_id=?",
            (forged.model_dump_json(exclude={"model_explanation"}), report.run_id),
        )
    score = score_wire_case(tmp_path, forged, "wire_paid")
    assert score["checks"]["persisted_run_correspondence"]
    assert not score["checks"]["authoritative_report_fields"] and not score["passed"]
