import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from threading import Barrier, Event, current_thread

from conftest import authorize

from agentic_payments_lab.payments import PaymentFault


def test_100_simultaneous_retries_one_charge(prepared):
    runtime, request, quote = prepared
    gate = Barrier(100)

    def purchase(_):
        gate.wait(timeout=30)
        return asyncio.run(runtime.buyer("task").purchase(request, quote))

    with ThreadPoolExecutor(max_workers=100) as pool:
        results = list(pool.map(purchase, range(100)))
    assert len({outcome.purchase_id for outcome in results}) == 1
    assert runtime.buyer("task").status().delivery_state == "delivered"
    assert runtime.payments.statistics() == {
        "execution_attempts": 1,
        "logical_charges": 1,
        "charged_atomic": 10_000,
        "signing_attempts": 0,
    }
    assert runtime.ledger.budget("grant").spent == 10_000
    assert runtime.ledger.budget("grant").reserved == 0
    events = runtime.ledger.audit(results[0].purchase_id)
    assert sum(event["event_type"] == "dispatch_recorded" for event in events) == 1


def test_concurrent_distinct_tasks_cannot_overspend(runtime, request_data):
    authorize(runtime, budget=30_000, cap=10_000)
    quote = asyncio.run(runtime.coordinator.quote(request_data))
    for i in range(20):
        runtime.ledger.create_task(f"task-{i}", "grant", runtime.clock.now())
    gate = Barrier(20)

    def purchase(i):
        gate.wait(timeout=30)
        return asyncio.run(runtime.coordinator.purchase(f"task-{i}", request_data, quote))

    with ThreadPoolExecutor(max_workers=20) as pool:
        results = list(pool.map(purchase, range(20)))
    assert sum(result.payment_state == "settled" for result in results) == 3
    assert sum(result.payment_error == "insufficient_budget" for result in results) == 17
    assert runtime.payments.statistics()["logical_charges"] == 3
    assert runtime.payments.execute.call_count == 3
    assert runtime.ledger.budget("grant").model_dump() == {
        "limit": 30_000,
        "reserved": 0,
        "spent": 30_000,
        "available": 0,
    }


def test_concurrent_reconciliation_accounts_once(prepared):
    runtime, request, quote = prepared
    runtime.payments.fault = PaymentFault.TIMEOUT_AFTER_COMMIT
    assert (
        asyncio.run(runtime.coordinator.purchase("task", request, quote)).payment_state == "unknown"
    )
    gate = Barrier(10)

    def reconcile(_):
        gate.wait(timeout=30)
        return asyncio.run(runtime.coordinator.reconcile("task"))

    with ThreadPoolExecutor(max_workers=10) as pool:
        outcomes = list(pool.map(reconcile, range(10)))
    assert all(result.payment_state == "settled" for result in outcomes)
    assert runtime.buyer("task").status().delivery_state == "delivered"
    assert runtime.ledger.budget("grant").spent == 10_000
    assert runtime.ledger.budget("grant").reserved == 0
    assert runtime.payments.statistics()["execution_attempts"] == 1


def test_cancellation_and_execution_share_authoritative_fence(prepared):
    runtime, request, quote = prepared
    purchase_id = runtime.ledger.task("task").purchase_id
    runtime.ledger.reserve(purchase_id, request, quote, "grant", runtime.clock.now())
    intent = runtime.ledger.claim(purchase_id, runtime.clock.now())
    gate = Barrier(2)

    def operation(action):
        gate.wait(timeout=30)
        return action(intent)

    with ThreadPoolExecutor(max_workers=2) as pool:
        execution, cancellation = list(
            pool.map(
                operation,
                [runtime.payments.execute, runtime.payments.cancel],
            )
        )
    assert execution == cancellation
    assert execution.status in ("settled", "failed")
    runtime.ledger.resolve(purchase_id, execution, runtime.clock.now())
    runtime.ledger.resolve(purchase_id, cancellation, runtime.clock.now())
    assert runtime.ledger.budget("grant").reserved == 0
    assert runtime.ledger.budget("grant").spent == (10_000 if execution.status == "settled" else 0)
    assert runtime.payments.statistics()["logical_charges"] <= 1


def test_audit_export_keeps_one_snapshot_during_reconciliation(prepared, monkeypatch):
    runtime, request, quote = prepared
    runtime.payments.fault = PaymentFault.TIMEOUT_AFTER_COMMIT
    outcome = asyncio.run(runtime.coordinator.purchase("task", request, quote))
    assert outcome.payment_state == "unknown"
    before = runtime.ledger.export_audit(outcome.purchase_id)
    events_query_started, reconciliation_finished = Event(), Event()
    resumed_after_reconciliation = Event()
    original_connect = runtime.ledger.db.connect

    @contextmanager
    def observed_connection():
        with original_connect() as db:
            if current_thread().name.startswith("audit-export"):

                def pause_before_events(sql):
                    if sql.lstrip().upper().startswith("SELECT") and "FROM events" in sql:
                        # The purchase has already been read. Let the writer commit
                        # before SQLite executes the export's events query.
                        events_query_started.set()
                        if reconciliation_finished.wait(timeout=30):
                            resumed_after_reconciliation.set()

                db.set_trace_callback(pause_before_events)
            yield db

    monkeypatch.setattr(runtime.ledger.db, "connect", observed_connection)
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="audit-export") as pool:
        pending = pool.submit(runtime.ledger.export_audit, outcome.purchase_id)
        try:
            assert events_query_started.wait(timeout=30)
            recovered = asyncio.run(runtime.coordinator.reconcile("task"))
            assert recovered.payment_state == "settled"
            assert recovered.delivery_state == "delivered"
        finally:
            reconciliation_finished.set()
        exported = pending.result(timeout=30)

    # Reconciliation finished while the export was open, but both exported reads
    # must still describe the snapshot established by its first SELECT.
    assert resumed_after_reconciliation.is_set(), "Export resumed before reconciliation committed"
    assert exported == before
    after = runtime.ledger.export_audit(outcome.purchase_id)
    assert after["payment_state"] == "settled"
    assert after["delivery_state"] == "delivered"
    assert {event["event_type"] for event in after["events"]} >= {"settled", "delivered"}
    assert runtime.payments.statistics()["execution_attempts"] == 1
