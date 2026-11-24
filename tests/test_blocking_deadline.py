"""NFR-L1: lock waits and purchase steps against a run deadline.

The deadline clock is scripted, so these tests assert which steps start and how
long a lock wait may be, not how long anything takes. Two tests stall a real
simulator tool call and one holds a real SQLite lock.
"""

import asyncio
import sqlite3
import time
from contextlib import closing
from unittest.mock import Mock

import pytest
from conftest import authorize
from test_buyer_agent import SequenceModel, authority, buy_quote, drive

from agentic_payments_lab import storage
from agentic_payments_lab.buyer_agent.contracts import Settings
from agentic_payments_lab.buyer_agent.scripted import final_response, tool_response
from agentic_payments_lab.coordinator import Checkpoint
from agentic_payments_lab.payments import PaymentFault
from agentic_payments_lab.storage import (
    PAYMENT_STEP_LOCK_WAIT,
    Database,
    DeadlineExpired,
    blocking_deadline,
    payment_step,
)

QUOTE = tool_response("get_quote", {"service_id": "report-standard"}, "quote")


class Clock:
    def __init__(self):
        self.now = 1000.0

    def monotonic(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


@pytest.fixture
def clock(monkeypatch):
    value = Clock()
    monkeypatch.setattr(storage, "time", value)
    return value


@pytest.fixture
def waits(monkeypatch):
    """The lock wait of every connection opened after this point."""
    seen, real = [], sqlite3.connect

    def connect(*args, **kwargs):
        if "timeout" in kwargs:
            seen.append(kwargs["timeout"])
        return real(*args, **kwargs)

    monkeypatch.setattr(storage.sqlite3, "connect", connect)
    return seen


def purchase(prepared, *, late=False):
    runtime, request, quote = prepared

    async def scenario():
        with blocking_deadline(5):
            if late:
                storage.time.advance(10)
            return await runtime.buyer("task").purchase(request, quote)

    return asyncio.run(scenario())


def expire_at(runtime, clock, point):
    runtime.coordinator.checkpoint = lambda reached: clock.advance(99) if reached == point else None


def test_lock_wait_is_the_time_left_before_the_deadline(tmp_path, clock, waits):
    database = Database(tmp_path / "d.sqlite3", "")

    def wait(**kwargs):
        with database.connect(**kwargs):
            pass
        return waits[-1]

    assert wait() == 30
    with blocking_deadline(5):
        assert wait() == 5
        clock.advance(3)
        assert wait() == 2 and wait(timeout=0.1) == 0.1
        with blocking_deadline(60):
            assert wait() == 2  # A nested deadline cannot extend the outer one.
        clock.advance(2.5)
        assert wait() == 0 and wait(timeout=0.1) == 0
        with payment_step():
            assert wait() == PAYMENT_STEP_LOCK_WAIT and wait(timeout=0.1) == 0.1
        assert wait() == 0
    assert wait() == 30


def test_expired_deadline_does_not_wait_for_a_held_lock(prepared, clock):
    runtime, request, quote = prepared
    purchase_id = runtime.ledger.task("task").purchase_id
    with closing(sqlite3.connect(runtime.ledger.db.path, isolation_level=None)) as holder:
        holder.execute("BEGIN IMMEDIATE")
        started = time.monotonic()
        with blocking_deadline(5):
            clock.advance(10)
            with pytest.raises(sqlite3.OperationalError):
                runtime.ledger.reserve(purchase_id, request, quote, "grant", runtime.clock.now())
        assert time.monotonic() - started < 5  # The configured wait is 30 seconds.
        holder.execute("ROLLBACK")
    assert runtime.ledger.budget("grant").reserved == 0
    assert runtime.buyer("task").status_or_none() is None


def test_purchase_does_not_start_after_the_deadline(prepared, clock):
    runtime, _, _ = prepared
    with pytest.raises(DeadlineExpired):
        purchase(prepared, late=True)
    assert runtime.buyer("task").status_or_none() is None
    assert runtime.ledger.budget("grant").reserved == 0
    assert runtime.payments.execute.call_count == 0


def test_deadline_after_reservation_stops_before_dispatch(prepared, clock):
    runtime, _, _ = prepared
    expire_at(runtime, clock, Checkpoint.AFTER_RESERVATION)
    with pytest.raises(DeadlineExpired):
        purchase(prepared)
    assert runtime.buyer("task").status().payment_state == "reserved"
    assert runtime.ledger.budget("grant").reserved == 10_000
    assert runtime.payments.execute.call_count == 0
    assert authority(runtime) == (0, 0, 0)
    # The operator releases the unsent reservation; nothing was ever dispatched.
    recovered = asyncio.run(runtime.coordinator.reconcile("task"))
    assert recovered.payment_state == "failed"
    assert runtime.ledger.budget("grant").reserved == 0
    assert runtime.payments.execute.call_count == 0


@pytest.mark.parametrize(
    "point",
    [Checkpoint.AFTER_DISPATCH, "during_payment_call", Checkpoint.AFTER_PAYMENT],
)
def test_dispatched_payment_is_finished_and_recorded_after_the_deadline(
    prepared, clock, waits, point
):
    runtime, _, _ = prepared
    mark = []
    if point == "during_payment_call":
        inner = runtime.payments.execute

        def slow(intent):
            mark.append(len(waits))
            clock.advance(10)
            return inner(intent)

        runtime.payments.execute = Mock(side_effect=slow)
    else:

        def checkpoint(reached):
            if reached == point:
                mark.append(len(waits))
                clock.advance(10)

        runtime.coordinator.checkpoint = checkpoint
    with pytest.raises(DeadlineExpired):
        purchase(prepared)  # Refused at the next new step: delivery.
    late = waits[mark[0] :]
    assert late and set(late) == {PAYMENT_STEP_LOCK_WAIT}
    del waits[:]
    status = runtime.buyer("task").status()
    assert status.payment_state == "settled" and status.delivery_state == "not_requested"
    budget = runtime.ledger.budget("grant")
    assert (budget.reserved, budget.spent) == (0, 10_000)
    assert runtime.payments.execute.call_count == 1
    assert authority(runtime) == (1, 1, 10_000)
    recovered = asyncio.run(runtime.coordinator.reconcile("task"))
    assert recovered.delivery_state == "delivered"
    assert runtime.payments.execute.call_count == 1
    assert authority(runtime) == (1, 1, 10_000)


def test_deadline_after_settlement_does_not_start_delivery(prepared, clock, monkeypatch):
    runtime, _, _ = prepared
    deliveries = Mock(wraps=runtime.merchant.deliver)
    monkeypatch.setattr(runtime.merchant, "deliver", deliveries)
    expire_at(runtime, clock, Checkpoint.AFTER_SETTLEMENT)
    with pytest.raises(DeadlineExpired):
        purchase(prepared)
    status = runtime.buyer("task").status()
    assert status.payment_state == "settled" and status.delivery_state == "not_requested"
    assert deliveries.call_count == 0
    assert authority(runtime) == (1, 1, 10_000)


def test_lost_payment_response_after_the_deadline_is_recorded_unknown(prepared, clock):
    runtime, _, _ = prepared
    runtime.payments.fault = PaymentFault.TIMEOUT_AFTER_COMMIT
    expire_at(runtime, clock, Checkpoint.AFTER_DISPATCH)
    with pytest.raises(DeadlineExpired):
        purchase(prepared)
    assert runtime.buyer("task").status().payment_state == "unknown"
    assert runtime.ledger.budget("grant").reserved == 10_000
    assert runtime.payments.execute.call_count == 1
    assert authority(runtime) == (1, 1, 10_000)


def test_run_ends_as_timeout_when_the_deadline_passes_inside_a_purchase(runtime, tmp_path, clock):
    authorize(runtime)
    expire_at(runtime, clock, Checkpoint.AFTER_SETTLEMENT)
    result = drive(
        runtime, tmp_path, model=SequenceModel([QUOTE, buy_quote, final_response("late")])
    )
    assert result.termination == "timeout"
    assert (result.events[-1].tool, result.events[-1].outcome) == ("purchase", "timeout")
    assert result.purchase.payment_state == "settled"
    assert result.purchase.delivery_state == "not_requested"
    assert result.purchase.operator_reconciliation_required
    assert authority(runtime) == (1, 1, 10_000)


@pytest.mark.parametrize("stalled", ["quote", "deliver"])
def test_stalled_simulator_tool_call_ends_the_run_as_timeout(
    runtime, tmp_path, monkeypatch, stalled
):
    authorize(runtime)

    async def never(*args, **kwargs):
        await asyncio.Event().wait()

    monkeypatch.setattr(runtime.merchant, stalled, never)
    result = drive(
        runtime,
        tmp_path,
        settings=Settings(timeout_seconds=3),
        model=SequenceModel([QUOTE, buy_quote, final_response("late")]),
    )
    assert result.termination == "timeout"
    if stalled == "quote":
        assert result.purchase.payment_state == "not_started"
        assert authority(runtime) == (0, 0, 0)
    else:
        assert result.purchase.payment_state == "settled"
        assert result.purchase.delivery_state == "not_requested"
        assert result.purchase.operator_reconciliation_required
        assert authority(runtime) == (1, 1, 10_000)
