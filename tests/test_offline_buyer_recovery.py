import asyncio
import json
import sqlite3
import subprocess
import sys
import traceback
from pathlib import Path

import pytest
from agents.usage import Usage
from evidence_support import reconciler
from offline_buyer_processes import competing_buyers
from offline_buyer_support import counters, drive, finance, quote, setup
from test_buyer_agent import SequenceModel

from agentic_payments_lab.buyer_agent import runner as buyer_runner
from agentic_payments_lab.buyer_agent.contracts import Settings
from agentic_payments_lab.buyer_agent.offline_x402 import OfflineBoundary
from agentic_payments_lab.buyer_agent.runner import run_buyer
from agentic_payments_lab.buyer_agent.scripted import ScriptedBuyer, final_response, tool_response
from agentic_payments_lab.buyer_agent.store import AgentStore
from agentic_payments_lab.x402_delivery import OfflineDeliveryAcceptor

MARKER = "offline-test-signature:BRIDGE_PRIVATE_PAYLOAD"


def last_run(metadata):
    with sqlite3.connect(metadata.db.path) as db:
        return json.loads(
            db.execute("SELECT data FROM runs ORDER BY rowid DESC LIMIT 1").fetchone()[0]
        )


@pytest.mark.parametrize("at", ["first_response", "after_payment", "after_acceptance"])
def test_external_cancellation_preserves_partial_usage_and_original_finance(tmp_path, at):
    shop, buyer, metadata, goal = setup(tmp_path)
    handle = quote(buyer, metadata, goal)
    reached = asyncio.Event()

    async def pending(_):
        reached.set()
        await asyncio.Event().wait()

    first = tool_response("get_purchase_status", {}, "status")
    second = tool_response("purchase", {"quote_id": handle}, "purchase")
    first.usage, second.usage = (
        Usage(input_tokens=11, output_tokens=7),
        Usage(input_tokens=13, output_tokens=9),
    )
    model = SequenceModel([pending] if at == "first_response" else [first, second, pending])

    async def execute():
        task = asyncio.create_task(run_buyer(buyer, metadata, goal, Settings(), model=model))
        await asyncio.wait_for(reached.wait(), 5)
        if at == "after_acceptance":
            # Explicit operator work, outside all model tools and continuation hooks.
            await reconciler(shop.buyer).reconcile("task")
            await OfflineDeliveryAcceptor(
                shop.buyer, buyer.client, lambda: shop.now[0] + 100
            ).accept("task")
        before = shop.buyer.report("task")
        task.cancel(MARKER)
        with pytest.raises(asyncio.CancelledError) as error:
            await task
        assert MARKER not in "".join(traceback.format_exception(error.value))
        assert shop.buyer.report("task") == before

    asyncio.run(execute())
    run = last_run(metadata)
    assert run["termination"] == "cancelled" and run["run_complete"]
    usage = run["model_usage"]
    assert usage["incomplete"] and usage["unresolved_requests"] == 1
    assert (usage["requests_started"], usage["responses_received"]) == (
        (1, 0) if at == "first_response" else (3, 2)
    )
    assert (usage["input_tokens"], usage["output_tokens"]) == (
        (0, 0) if at == "first_response" else (24, 16)
    )
    assert run["purchase"] == OfflineBoundary(buyer, metadata, goal).status().model_dump(
        mode="json"
    )
    assert counters(shop) == ((0, 0, 0, 0, 0) if at == "first_response" else (1, 1, 1, 1, 10_000))
    # A separate explicit invocation can use the same identity, but cannot resume
    # any existing execution or retrieve delivery. The interrupted run stays unchanged.
    original_id = run["run_id"]
    report = drive(buyer, AgentStore(metadata.db.path), goal)
    assert report.purchase.purchase_id == buyer.context.purchase_id
    assert counters(shop) == (1, 1, 1, 1, 10_000)
    with metadata.db.connect() as db:
        assert (
            json.loads(
                db.execute("SELECT data FROM runs WHERE run_id=?", (original_id,)).fetchone()[0]
            )
            == run
        )


@pytest.mark.parametrize("where", ["challenge", "model"])
@pytest.mark.parametrize("kind", ["cancel", "timeout"])
def test_late_response_cannot_claim_or_sign(tmp_path, monkeypatch, where, kind):
    shop, buyer, metadata, goal = setup(tmp_path)
    ready = asyncio.Event()
    late_returned = asyncio.Event()

    class CheckpointTimeout:
        CancelledError = asyncio.CancelledError

        def timeout(self, seconds):
            assert seconds == 0.1
            # Target cancellation at the selected await, not during the earlier
            # SDK/SQLite work. Keep the real asyncio timeout and its conversion
            # to TimeoutError; other modules' timers remain untouched.
            self.scope = asyncio.timeout(None)
            return self.scope

    controlled = CheckpointTimeout()
    if kind == "timeout":
        monkeypatch.setattr(buyer_runner, "asyncio", controlled)
    if where == "challenge":
        original = buyer.client.challenge

        async def late(*args):
            result = await original(*args)
            ready.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                late_returned.set()
                return result

        buyer.client.challenge = late
        model = ScriptedBuyer()
    else:
        handle = quote(buyer, metadata, goal)

        async def late(_):
            ready.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                late_returned.set()
                return tool_response("purchase", {"quote_id": handle}, "late")

        model = SequenceModel([late])

    async def execute():
        task = asyncio.create_task(
            run_buyer(
                buyer,
                metadata,
                goal,
                Settings(timeout_seconds=0.1 if kind == "timeout" else 10),
                model=model,
            )
        )
        await asyncio.wait_for(ready.wait(), 5)
        if kind == "cancel":
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 5)
        else:
            assert controlled.scope.when() is None
            controlled.scope.reschedule(asyncio.get_running_loop().time())
            assert (await asyncio.wait_for(task, 5)).termination == "timeout"
            assert controlled.scope.expired()
        assert late_returned.is_set()  # The cancellation fence sees a late result.

    asyncio.run(execute())
    assert counters(shop) == (0, 0, 0, 0, 0)
    assert finance(shop) == (0, 0)
    assert shop.buyer.report("task")["authorization_id"] is None
    assert last_run(metadata)["termination"] == ("cancelled" if kind == "cancel" else "timeout")


def test_timeout_before_challenge_does_not_require_later_checkpoint(tmp_path):
    shop, buyer, metadata, goal = setup(tmp_path)
    challenges = []

    async def unexpected_challenge(*args):
        challenges.append("challenge")
        raise AssertionError("A pending first model response cannot request a quote")

    async def pending(_):
        await asyncio.Event().wait()

    buyer.client.challenge = unexpected_challenge
    # Use the unmodified runner's actual timer. A timeout may occur before any
    # response/checkpoint; waiting for a later challenge would conceal its result.
    report = asyncio.run(
        run_buyer(
            buyer,
            metadata,
            goal,
            Settings(timeout_seconds=0.1),
            model=SequenceModel([pending]),
        )
    )
    assert report.termination == "timeout" and report.run_complete
    assert report.metadata_persisted and challenges == []
    usage = report.model_usage
    assert usage.responses_received == 0
    assert usage.unresolved_requests == usage.requests_started
    assert usage.incomplete == (usage.requests_started > 0)
    assert counters(shop) == (0, 0, 0, 0, 0)
    assert finance(shop) == (0, 0)
    assert shop.buyer.report("task")["authorization_id"] is None
    # Model explanations are deliberately excluded from durable agent metadata.
    assert last_run(metadata) == report.model_dump(mode="json", exclude={"model_explanation"})


def test_cancellation_during_signing_keeps_claim_and_fences_late_signer(tmp_path):
    shop, buyer, metadata, goal = setup(tmp_path)
    ready, release = asyncio.Event(), asyncio.Event()
    original = buyer.gate.signer.sign

    async def late(terms):
        signature = await original(terms)
        ready.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            return signature
        return signature

    buyer.gate.signer.sign = late

    async def execute():
        task = asyncio.create_task(run_buyer(buyer, metadata, goal, Settings()))
        await asyncio.wait_for(ready.wait(), 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(execute())
    assert counters(shop) == (1, 0, 0, 0, 0)
    assert finance(shop) == (10_000, 0)
    result = drive(buyer, metadata, goal)
    assert result.purchase.payment_state == "canceled_before_dispatch"
    assert result.purchase.operator_reconciliation_required
    assert counters(shop) == (1, 0, 0, 0, 0)


@pytest.mark.parametrize("boundary", ["quote", "purchase_event", "finish"])
def test_metadata_failure_does_not_release_replace_or_replay(tmp_path, monkeypatch, boundary):
    shop, buyer, metadata, goal = setup(tmp_path)
    original = metadata.db.transaction
    if boundary == "quote":
        from contextlib import contextmanager

        @contextmanager
        def fail_quote():
            with original() as db:
                yield db
                if db.execute("SELECT 1 FROM quote_handles").fetchone():
                    raise sqlite3.OperationalError(MARKER)

        monkeypatch.setattr(metadata.db, "transaction", fail_quote)
    elif boundary == "purchase_event":
        event = metadata.event

        def fail_event(run_id, value):
            if value.tool == "purchase":
                raise sqlite3.OperationalError(MARKER)
            event(run_id, value)

        monkeypatch.setattr(metadata, "event", fail_event)
    else:

        def fail_finish(_):
            raise sqlite3.OperationalError(MARKER)

        monkeypatch.setattr(metadata, "finish", fail_finish)
    result = drive(buyer, metadata, goal)
    assert result.termination in ("metadata_error", "tool_error")
    assert MARKER not in result.model_dump_json()
    if boundary == "quote":
        assert counters(shop) == (0, 0, 0, 0, 0)
        assert finance(shop) == (0, 0)
    else:
        assert counters(shop) == (1, 1, 1, 1, 10_000)
        assert finance(shop) == (10_000, 0)
        monkeypatch.undo()
        assert drive(buyer, metadata, goal).purchase == result.purchase
        assert counters(shop) == (1, 1, 1, 1, 10_000)


@pytest.mark.parametrize("kind", ["lost_response", "fabricated_success"])
def test_merchant_response_cannot_settle_accounting_or_leak_payload(tmp_path, kind, caplog):
    shop, buyer, metadata, goal = setup(tmp_path)
    dispatch = buyer.client.dispatch

    async def result(**kwargs):
        if kind == "lost_response":
            await dispatch(**kwargs)
            raise RuntimeError(MARKER)
        return {"settled": True, "transaction": "0x" + "ab" * 32, "instructions": MARKER}

    buyer.client.dispatch = result
    report = drive(buyer, metadata, goal)
    assert report.purchase.payment_state == "reconciliation_required"
    assert finance(shop) == (10_000, 0)
    assert report.purchase.delivery_state == "not_requested"
    assert not report.purchase.blockchain_settlement_verified
    counts = counters(shop)
    assert drive(buyer, metadata, goal).purchase == report.purchase
    assert counters(shop) == counts
    assert MARKER not in caplog.text + report.model_dump_json()
    for path in tmp_path.iterdir():
        if path.is_file():
            assert MARKER.encode() not in path.read_bytes()


@pytest.mark.parametrize("limit", ["tools", "turns", "output", "parallel"])
def test_same_runner_limits_before_execution(tmp_path, limit):
    shop, buyer, metadata, goal = setup(tmp_path)
    settings = Settings(tool_limit=2) if limit == "tools" else Settings(max_turns=2)
    if limit == "output":
        steps = [final_response("x" * 9000)]
    elif limit == "parallel":
        response = tool_response("list_services", {}, "a")
        response.output += tool_response("get_purchase_status", {}, "b").output
        steps = [response]
    else:
        steps = [tool_response("list_services", {}, str(i)) for i in range(6)]
    model = SequenceModel(steps)
    report = drive(buyer, metadata, goal, model=model, settings=settings)
    assert (
        report.termination
        == {
            "tools": "tool_limit",
            "turns": "turn_limit",
            "output": "output_limit",
            "parallel": "invalid_model_output",
        }[limit]
    )
    assert len(model.inputs) <= (3 if limit == "tools" else 2)
    assert counters(shop) == (0, 0, 0, 0, 0)


@pytest.mark.parametrize(
    "boundary", ["after_quote", "after_claim", "after_dispatch", "after_finish"]
)
def test_process_death_keeps_identity_and_never_reexecutes_claim(tmp_path, boundary):
    shop, buyer, metadata, goal = setup(tmp_path)
    original = buyer.context
    worker = Path(__file__).with_name("offline_buyer_worker.py")
    child = subprocess.run(
        [sys.executable, str(worker), str(tmp_path), boundary],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert child.returncode == 95, child.stderr
    before = counters(shop)
    assert finance(shop) == ((0, 0) if boundary == "after_quote" else (10_000, 0))
    run = last_run(metadata)
    assert run["run_complete"] == (boundary == "after_finish")
    report = drive(buyer, AgentStore(metadata.db.path), goal)
    assert report.purchase.purchase_id == original.purchase_id
    assert counters(shop) == ((1, 1, 1, 1, 10_000) if boundary == "after_quote" else before)
    assert finance(shop) == (10_000, 0)


def test_four_processes_enter_gate_together_with_one_execution(tmp_path):
    shop, buyer, metadata, goal = setup(tmp_path)

    def before_release():
        assert counters(shop) == (0, 0, 0, 0, 0)
        assert finance(shop) == (0, 0)

    with competing_buyers(tmp_path, before_release) as (children, outputs):
        assert len(list(tmp_path.glob("ready-*"))) == 4
        assert [p.returncode for p in children] == [0] * 4
        assert {json.loads(stdout)["purchase_id"] for stdout, _ in outputs} == {
            buyer.context.purchase_id
        }
        assert counters(shop) == (1, 1, 1, 1, 10_000)
        assert finance(shop) == (10_000, 0)
        assert drive(buyer, metadata, goal).purchase.purchase_id == buyer.context.purchase_id
        assert counters(shop) == (1, 1, 1, 1, 10_000)
