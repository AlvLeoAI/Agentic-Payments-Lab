"""Regression tests for durable agent evidence, using real SDK/Responses plumbing."""

import asyncio
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import httpx2
import pytest
from conftest import authorize
from openai import DefaultAsyncHttpxClient

from agentic_payments_lab.buyer_agent import runner
from agentic_payments_lab.buyer_agent.contracts import Goal, Settings
from agentic_payments_lab.buyer_agent.scripted import tool_response
from agentic_payments_lab.buyer_agent.store import AgentStore
from agentic_payments_lab.domain import PurchaseConflict, PurchaseRequest
from agentic_payments_lab.payments import PaymentFault
from agentic_payments_lab.runtime import Runtime

GOAL = Goal(subject="durability-test", purpose="research", quality="standard")
LIVE = Settings(
    mode="live", model="explicit-model", max_turns=6, max_output_tokens=512, timeout_seconds=30
)


def authority(path):
    with sqlite3.connect(f"file:{path / 'payments.sqlite3'}?mode=ro", uri=True) as db:
        outcomes = [json.loads(row[0]) for row in db.execute("SELECT result FROM authorizations")]
        attempts = db.execute("SELECT count(*) FROM attempts").fetchone()[0]
    settled = [r for r in outcomes if r["status"] == "settled"]
    return attempts, len(settled), sum(r["receipt"]["intent"]["amount"] for r in settled)


def reports(path):
    with sqlite3.connect(path / "agent.sqlite3") as db:
        return [json.loads(row[0]) for row in db.execute("SELECT data FROM runs ORDER BY rowid")]


def prepare(path):
    runtime = Runtime(path)
    authorize(runtime)
    store = AgentStore(path / "agent.sqlite3")
    store.initialize(runtime.ledger, "task", GOAL)
    return runtime, store


class ResponsesScenario:
    """Two genuine tool responses then an interruptible third HTTP request."""

    def __init__(self, path, stop_at=3, *, crash=False):
        self.path, self.stop_at, self.crash = path, stop_at, crash
        self.waiting = asyncio.Event()
        self.requests = []
        self.snapshots = []

    async def respond(self, request):
        body = json.loads(request.content)
        self.requests.append(body)
        self.snapshots.append(reports(self.path))
        assert str(request.url) == "https://api.openai.com/v1/responses"
        assert body["parallel_tool_calls"] is False
        assert body["max_output_tokens"] == 512
        if len(self.requests) == self.stop_at:
            self.waiting.set()
            if self.crash:
                os._exit(73)
            await asyncio.Future()
        if len(self.requests) == 1:
            response = tool_response("get_quote", {"service_id": "report-standard"}, "q")
        else:
            output = [v for v in body["input"] if v.get("type") == "function_call_output"][-1]
            assert output["call_id"] == "q"
            response = tool_response(
                "purchase", {"quote_id": json.loads(output["output"])["quote_id"]}, "p"
            )
        return httpx2.Response(
            200,
            json={
                "id": f"response-{len(self.requests)}",
                "object": "response",
                "created_at": 0,
                "status": "completed",
                "model": "explicit-model",
                "parallel_tool_calls": False,
                "output": [item.model_dump(mode="json") for item in response.output],
                "usage": {
                    "input_tokens": 11,
                    "output_tokens": 7,
                    "total_tokens": 18,
                    "input_tokens_details": {"cached_tokens": 0},
                    "output_tokens_details": {"reasoning_tokens": 0},
                },
            },
        )

    def client(self, **kwargs):
        # Preserve production client URL, timeouts, redirects, proxies and retry configuration.
        return DefaultAsyncHttpxClient(transport=httpx2.MockTransport(self.respond), **kwargs)


def resume(path):
    runtime = Runtime(path)
    return asyncio.run(
        runner.run_buyer(
            runtime.buyer("task"), AgentStore(path / "agent.sqlite3"), GOAL, Settings()
        )
    )


@pytest.mark.parametrize("stop_at", [1, 2, 3])
def test_responses_cancellation_is_durable(tmp_path, monkeypatch, stop_at):
    runtime, store = prepare(tmp_path)
    scenario = ResponsesScenario(tmp_path, stop_at)
    monkeypatch.setenv("OPENAI_API_KEY", "test-only-not-a-real-key")
    monkeypatch.setattr(runner, "DefaultAsyncHttpxClient", scenario.client)

    async def exercise():
        task = asyncio.create_task(runner.run_buyer(runtime.buyer("task"), store, GOAL, LIVE))
        await asyncio.wait_for(scenario.waiting.wait(), 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 2)

    asyncio.run(exercise())
    rows = reports(tmp_path)
    assert len(rows) == 1, "Cancellation must leave a durable run, not orphaned tool events"
    saved = rows[0]
    assert saved["termination"] == "cancelled" and saved["run_complete"]
    usage = saved["model_usage"]
    assert usage["requests_started"] == stop_at == len(scenario.requests)
    assert usage["responses_received"] == stop_at - 1
    assert (usage["input_tokens"], usage["output_tokens"]) == (
        11 * (stop_at - 1),
        7 * (stop_at - 1),
    )
    assert usage["incomplete"] and usage["unresolved_requests"] == 1
    assert usage["api_cost"] is None
    for number, snapshot in enumerate(scenario.snapshots, start=1):
        assert len(snapshot) == 1
        pending = snapshot[0]
        assert pending["termination"] == "running" and not pending["run_complete"]
        assert pending["model_usage"]["requests_started"] == number
        assert pending["model_usage"]["responses_received"] == number - 1
        assert pending["model_usage"]["incomplete"]
    if stop_at == 3:
        assert saved["purchase"]["payment_state"] == "settled"
        assert saved["purchase"]["delivery_state"] == "delivered"
        assert authority(tmp_path) == (1, 1, 10_000)
    else:
        assert saved["purchase"]["payment_state"] == "not_started"
        assert authority(tmp_path) == (0, 0, 0)
    resumed = resume(tmp_path)
    assert resumed.purchase.purchase_id == saved["purchase"]["purchase_id"]
    assert resumed.purchase.payment_state == "settled"
    assert authority(tmp_path) == (1, 1, 10_000)
    assert reports(tmp_path)[0] == saved  # New run never overwrites interrupted-run usage.


def test_cancelled_unknown_payment_keeps_reservation(tmp_path, monkeypatch):
    runtime, store = prepare(tmp_path)
    runtime.payments.fault = PaymentFault.TIMEOUT_AFTER_COMMIT
    scenario = ResponsesScenario(tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "test-only-not-a-real-key")
    monkeypatch.setattr(runner, "DefaultAsyncHttpxClient", scenario.client)

    async def exercise():
        task = asyncio.create_task(runner.run_buyer(runtime.buyer("task"), store, GOAL, LIVE))
        await asyncio.wait_for(scenario.waiting.wait(), 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(exercise())
    saved = reports(tmp_path)[0]
    assert saved["purchase"]["payment_state"] == "unknown"
    assert saved["purchase"]["operator_reconciliation_required"]
    assert runtime.ledger.budget("grant").reserved == 10_000
    assert resume(tmp_path).purchase.payment_state == "unknown"
    assert authority(tmp_path) == (1, 1, 10_000)
    assert runtime.ledger.budget("grant").reserved == 10_000


def test_cancellation_between_responses_keeps_complete_observed_usage(tmp_path, monkeypatch):
    runtime, store = prepare(tmp_path)
    scenario = ResponsesScenario(tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "test-only-not-a-real-key")
    monkeypatch.setattr(runner, "DefaultAsyncHttpxClient", scenario.client)

    async def exercise():
        delivering = asyncio.Event()

        async def deliver(request):
            delivering.set()
            await asyncio.Future()

        monkeypatch.setattr(runtime.merchant, "deliver", deliver)
        task = asyncio.create_task(runner.run_buyer(runtime.buyer("task"), store, GOAL, LIVE))
        await asyncio.wait_for(delivering.wait(), 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(exercise())
    saved = reports(tmp_path)[0]
    assert saved["termination"] == "cancelled"
    assert saved["model_usage"]["requests_started"] == len(scenario.requests) == 2
    assert saved["model_usage"]["responses_received"] == 2
    assert saved["model_usage"]["input_tokens"] == 22
    assert saved["model_usage"]["output_tokens"] == 14
    assert not saved["model_usage"]["incomplete"]
    assert saved["model_usage"]["unresolved_requests"] == 0
    assert saved["purchase"]["payment_state"] == "settled"
    assert saved["purchase"]["delivery_state"] == "not_requested"
    assert saved["purchase"]["operator_reconciliation_required"]
    assert resume(tmp_path).purchase.delivery_state == "not_requested"
    assert authority(tmp_path) == (1, 1, 10_000)


def test_response_persistence_failure_keeps_pending_evidence(tmp_path, monkeypatch):
    runtime, store = prepare(tmp_path)
    scenario = ResponsesScenario(tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "test-only-not-a-real-key")
    monkeypatch.setattr(runner, "DefaultAsyncHttpxClient", scenario.client)
    progress = store.progress

    def fail_after_response(report):
        if report.model_usage.responses_received:
            raise sqlite3.OperationalError("injected response write failure")
        progress(report)

    monkeypatch.setattr(store, "progress", fail_after_response)
    report = asyncio.run(runner.run_buyer(runtime.buyer("task"), store, GOAL, LIVE))
    assert report.termination == "metadata_error" and not report.metadata_persisted
    assert len(scenario.requests) == 1
    assert report.model_usage.input_tokens == 11
    saved = reports(tmp_path)[0]
    assert not saved["run_complete"] and saved["model_usage"]["incomplete"]
    assert saved["model_usage"]["unresolved_requests"] == 1
    assert saved["model_usage"]["responses_received"] == 0
    assert authority(tmp_path) == (0, 0, 0)


@pytest.mark.parametrize(
    "cleanup", ["repeated_cancel", "close_timeout", "write_failure", "locked_store"]
)
def test_cancellation_cleanup_is_best_effort_and_bounded(tmp_path, monkeypatch, cleanup):
    runtime, store = prepare(tmp_path)
    scenario = ResponsesScenario(tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "test-only-not-a-real-key")
    monkeypatch.setattr(runner, "DefaultAsyncHttpxClient", scenario.client)
    real_client = runner.live_client
    clients = []

    async def exercise():
        closing = asyncio.Event()

        def client(settings):
            value = real_client(settings)
            clients.append(value)
            if cleanup in ("repeated_cancel", "close_timeout"):

                async def close():
                    closing.set()
                    await asyncio.Future()

                monkeypatch.setattr(value, "close", close)
            return value

        monkeypatch.setattr(runner, "live_client", client)
        task = asyncio.create_task(runner.run_buyer(runtime.buyer("task"), store, GOAL, LIVE))
        await asyncio.wait_for(scenario.waiting.wait(), 5)
        lock = None
        if cleanup == "write_failure":

            def fail(report):
                raise sqlite3.OperationalError("injected persistence failure")

            monkeypatch.setattr(store, "finish", fail)
        elif cleanup == "locked_store":
            lock = sqlite3.connect(store.db.path)
            lock.execute("BEGIN IMMEDIATE")
        try:
            task.cancel()
            if cleanup == "repeated_cancel":
                await asyncio.wait_for(closing.wait(), 1)
                # Cancellation evidence is already durable before awaiting close.
                assert reports(tmp_path)[0]["termination"] == "cancelled"
                task.cancel()
            done, _ = await asyncio.wait([task], timeout=2)
            assert task in done, "cleanup must not wait the financial SQLite timeout"
            with pytest.raises(asyncio.CancelledError):
                await task
        finally:
            if lock:
                lock.rollback()
                lock.close()
            for value in clients:
                await value._client.aclose()

    asyncio.run(exercise())
    saved = reports(tmp_path)[0]
    assert saved["model_usage"]["incomplete"]
    assert saved["model_usage"]["input_tokens"] == 22
    assert saved["termination"] == (
        "running" if cleanup in ("write_failure", "locked_store") else "cancelled"
    )
    assert saved["run_complete"] == (cleanup not in ("write_failure", "locked_store"))
    assert authority(tmp_path) == (1, 1, 10_000)


@pytest.mark.parametrize("sink", ["start", "progress"])
def test_persistence_failure_prevents_provider_dispatch(tmp_path, monkeypatch, sink):
    runtime, store = prepare(tmp_path)
    scenario = ResponsesScenario(tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "test-only-not-a-real-key")
    monkeypatch.setattr(runner, "DefaultAsyncHttpxClient", scenario.client)

    def fail(*args):
        raise sqlite3.OperationalError("injected persistence failure")

    monkeypatch.setattr(store, sink, fail)
    report = asyncio.run(runner.run_buyer(runtime.buyer("task"), store, GOAL, LIVE))
    assert report.termination == "metadata_error" and not report.metadata_persisted
    assert scenario.requests == []
    assert authority(tmp_path) == (0, 0, 0)


@pytest.mark.parametrize("stop_at", [1, 3])
def test_abrupt_exit_retains_pending_usage(tmp_path, stop_at):
    completed = subprocess.run(
        [sys.executable, __file__, str(tmp_path), str(stop_at)],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert completed.returncode == 73, completed.stderr
    saved = reports(tmp_path)[0]
    assert saved["termination"] == "running" and not saved["run_complete"]
    assert saved["model_usage"]["requests_started"] == stop_at
    assert saved["model_usage"]["responses_received"] == stop_at - 1
    assert saved["model_usage"]["input_tokens"] == (stop_at - 1) * 11
    assert saved["model_usage"]["incomplete"]
    assert saved["model_usage"]["unresolved_requests"] == 1
    assert authority(tmp_path) == ((1, 1, 10_000) if stop_at == 3 else (0, 0, 0))
    assert resume(tmp_path).purchase.payment_state == "settled"
    assert authority(tmp_path) == (1, 1, 10_000)
    assert reports(tmp_path)[0] == saved


@pytest.mark.parametrize("loss", ["database", "binding"])
@pytest.mark.parametrize(
    "change", [{}, {"subject": "changed"}, {"purpose": "other"}, {"quality": "premium"}]
)
def test_missing_metadata_requires_explicit_recovery(tmp_path, loss, change):
    runtime, store = prepare(tmp_path)
    first = resume(tmp_path)
    if loss == "database":
        store.db.path.rename(tmp_path / "saved-agent.sqlite3")
        store = AgentStore(tmp_path / "agent.sqlite3")
    else:
        with store.db.transaction() as db:
            db.execute("DELETE FROM bindings WHERE task_id='task'")
    goal = GOAL.model_copy(update=change)
    with pytest.raises(ValueError):
        asyncio.run(runner.run_buyer(runtime.buyer("task"), store, goal, Settings()))
    with pytest.raises(ValueError):
        store.initialize(runtime.ledger, "task", goal)  # Initialization is not recovery.
    assert authority(tmp_path) == (1, 1, 10_000)
    store.recover(runtime.ledger, "task")  # No caller-supplied replacement goal.
    if change:
        with pytest.raises(PurchaseConflict):
            asyncio.run(runner.run_buyer(runtime.buyer("task"), store, goal, Settings()))
    restored = resume(tmp_path)
    assert restored.purchase == first.purchase
    assert restored.history_incomplete
    assert authority(tmp_path) == (1, 1, 10_000)
    with runtime.ledger.db.connect() as db:
        assert db.execute("SELECT count(*) FROM tasks").fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM purchases").fetchone()[0] == 1


def test_legitimate_phase1_task_requires_explicit_initialization(tmp_path):
    runtime = Runtime(tmp_path)
    authorize(runtime)
    store = AgentStore(tmp_path / "agent.sqlite3")
    with pytest.raises(ValueError, match="agent_initialization_required"):
        asyncio.run(runner.run_buyer(runtime.buyer("task"), store, GOAL, Settings()))
    assert reports(tmp_path) == [] and authority(tmp_path) == (0, 0, 0)
    store.initialize(runtime.ledger, "task", GOAL)
    assert not resume(tmp_path).history_incomplete
    assert authority(tmp_path) == (1, 1, 10_000)
    for goal in (
        GOAL.model_copy(update={"subject": "changed"}),
        GOAL.model_copy(update={"quality": "premium"}),
    ):
        with pytest.raises(PurchaseConflict):
            store.initialize(runtime.ledger, "task", goal)


def test_interrupted_initialization_requires_recovery_from_witness(tmp_path, monkeypatch):
    runtime = Runtime(tmp_path)
    authorize(runtime)
    store = AgentStore(tmp_path / "agent.sqlite3")
    original = store.db.transaction

    def fail():
        raise sqlite3.OperationalError("metadata write failed after witness")

    monkeypatch.setattr(store.db, "transaction", fail)
    with pytest.raises(sqlite3.OperationalError):
        store.initialize(runtime.ledger, "task", GOAL)
    assert runtime.ledger.agent_binding("task") is not None
    monkeypatch.setattr(store.db, "transaction", original)
    with pytest.raises(ValueError, match="agent_recovery_required"):
        store.initialize(runtime.ledger, "task", GOAL)
    assert authority(tmp_path) == (0, 0, 0)
    store.recover(runtime.ledger, "task")
    assert resume(tmp_path).history_incomplete
    assert authority(tmp_path) == (1, 1, 10_000)


def test_missing_metadata_before_purchase_cannot_change_intent(tmp_path):
    runtime, store = prepare(tmp_path)
    store.db.path.rename(tmp_path / "saved-agent.sqlite3")
    store = AgentStore(tmp_path / "agent.sqlite3")
    with pytest.raises(PurchaseConflict):
        store.initialize(runtime.ledger, "task", GOAL.model_copy(update={"subject": "replacement"}))
    with pytest.raises(ValueError):
        resume(tmp_path)
    assert authority(tmp_path) == (0, 0, 0)
    store.recover(runtime.ledger, "task")
    resume(tmp_path)
    assert runtime.buyer("task").status().resource.resource.subject == GOAL.subject


def test_no_new_intent_for_legacy_or_already_purchased_task(tmp_path):
    runtime = Runtime(tmp_path)
    authorize(runtime)
    store = AgentStore(tmp_path / "agent.sqlite3")
    with store.db.transaction() as db:
        db.execute("INSERT INTO bindings VALUES('task','legacy')")
    with pytest.raises(ValueError, match="agent_recovery_required"):
        store.initialize(runtime.ledger, "task", GOAL)
    with pytest.raises(ValueError, match="agent_recovery_required"):
        store.recover(runtime.ledger, "task")
    assert runtime.ledger.agent_binding("task") is None
    runtime.ledger.create_task("phase1-paid", "grant", runtime.clock.now())
    request = PurchaseRequest(service_id="report-standard", subject="phase1", purpose="research")
    quote = asyncio.run(runtime.buyer("phase1-paid").quote(request))
    asyncio.run(runtime.buyer("phase1-paid").purchase(request, quote))
    with pytest.raises(PurchaseConflict):
        store.initialize(runtime.ledger, "phase1-paid", GOAL)
    assert authority(tmp_path) == (1, 1, 10_000)


def test_cli_initialization_and_metadata_recovery(tmp_path):
    def cli(*args, expected=0):
        result = subprocess.run(
            [sys.executable, "-m", "agentic_payments_lab", "--state-dir", str(tmp_path), *args],
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert result.returncode == expected, result.stderr
        return json.loads(result.stdout)

    goal_args = ("--subject", GOAL.subject, "--purpose", GOAL.purpose, "--quality", GOAL.quality)
    cli("init", "--budget", "10000", "--cap", "10000")
    cli("task", "task")
    assert cli("agent", "task", *goal_args, expected=2)["error"] == "agent_initialization_required"
    cli("agent-init", "task", *goal_args)
    first = cli("agent", "task", *goal_args)
    (tmp_path / "agent.sqlite3").rename(tmp_path / "old-agent.sqlite3")
    assert cli("agent", "task", *goal_args, expected=2)["error"] == "agent_recovery_required"
    assert cli("agent-init", "task", *goal_args, expected=2)["error"] == "agent_recovery_required"
    cli("agent-recover", "task")
    last = cli("agent", "task", *goal_args)
    assert last["history_incomplete"] and last["purchase"] == first["purchase"]
    assert authority(tmp_path) == (1, 1, 10_000)


if __name__ == "__main__":
    directory = Path(sys.argv[1])
    instance, metadata = prepare(directory)
    scenario = ResponsesScenario(directory, int(sys.argv[2]), crash=True)
    os.environ["OPENAI_API_KEY"] = "test-only-not-a-real-key"
    with patch.object(runner, "DefaultAsyncHttpxClient", scenario.client):
        asyncio.run(runner.run_buyer(instance.buyer("task"), metadata, GOAL, LIVE))
