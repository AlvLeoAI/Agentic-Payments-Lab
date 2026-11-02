import asyncio
import json
import subprocess
import sys
from pathlib import Path

import pytest
from test_authorization import buy

from agentic_payments_lab.coordinator import Checkpoint, SimulatedCrash
from agentic_payments_lab.domain import PaymentIntent, Resolution
from agentic_payments_lab.payments import PaymentFault
from agentic_payments_lab.provider import DeliveryFault


@pytest.mark.parametrize("fault", [DeliveryFault.UNAVAILABLE, DeliveryFault.CORRUPT_RESPONSE])
def test_paid_but_delivery_failed_recovers_without_payment(prepared, fault):
    runtime, _, _ = prepared
    runtime.provider.fault = fault
    outcome = buy(*prepared)
    assert outcome.payment_state == "settled"
    assert outcome.delivery_state == "failed"
    assert runtime.ledger.budget("grant").spent == 10_000
    runtime.provider.fault = DeliveryFault.NONE
    recovered = asyncio.run(runtime.coordinator.reconcile("task"))
    assert recovered.delivery_state == "delivered"
    assert runtime.payments.execute.call_count == 1
    assert runtime.payments.statistics()["logical_charges"] == 1
    events = runtime.ledger.audit(outcome.purchase_id)
    assert any(event["event_type"] == "reconciliation_requested" for event in events)


def test_timeout_after_charge_is_reserved_until_reconciled(prepared):
    runtime, _, _ = prepared
    runtime.payments.fault = PaymentFault.TIMEOUT_AFTER_COMMIT
    outcome = buy(*prepared)
    assert outcome.payment_state == "unknown"
    assert runtime.payments.statistics()["logical_charges"] == 1
    assert runtime.ledger.budget("grant").reserved == 10_000
    runtime.clock.advance(4000)  # Expiry cannot erase existing exposure.
    assert buy(*prepared).payment_state == "unknown"
    assert runtime.payments.execute.call_count == 1
    recovered = asyncio.run(runtime.coordinator.reconcile("task"))
    assert recovered.payment_state == "settled"
    assert recovered.delivery_state == "delivered"
    assert runtime.ledger.budget("grant").reserved == 0
    assert runtime.ledger.budget("grant").spent == 10_000
    assert runtime.payments.execute.call_count == 1


def test_unknown_requires_authoritative_resolution_and_fences_late_payment(prepared):
    runtime, _, _ = prepared
    runtime.payments.fault = PaymentFault.TIMEOUT_BEFORE_COMMIT
    outcome = buy(*prepared)
    assert outcome.payment_state == "unknown"
    assert runtime.payments.statistics()["logical_charges"] == 0
    runtime.clock.advance(4000)
    assert asyncio.run(runtime.coordinator.reconcile("task")).payment_state == "unknown"
    assert runtime.ledger.budget("grant").reserved == 10_000
    assert buy(*prepared).payment_state == "unknown"
    intent = PaymentIntent.model_validate_json(runtime.ledger.record(outcome.purchase_id)["intent"])
    # Cancel at authority before recording cancellation locally: a delayed executor
    # still sees approval, so the durable tombstone must fence it independently.
    canceled = runtime.payments.cancel(intent)
    assert canceled.status == "failed"
    runtime.payments.fault = PaymentFault.NONE
    assert runtime.payments.execute(intent).status == "failed"
    assert runtime.payments.statistics()["logical_charges"] == 0
    recovered = asyncio.run(runtime.coordinator.reconcile("task", cancel=True))
    assert recovered.payment_state == "failed"
    assert runtime.ledger.budget("grant").reserved == 0
    assert runtime.ledger.budget("grant").available == 40_000
    with pytest.raises(PermissionError):
        runtime.payments.execute(intent)


def test_authoritative_decline_releases_budget(prepared):
    runtime, _, _ = prepared
    runtime.payments.fault = PaymentFault.DECLINE
    outcome = buy(*prepared)
    assert outcome.payment_state == "failed"
    assert outcome.payment_error == "simulated_decline"
    assert runtime.ledger.budget("grant").available == 40_000
    assert runtime.payments.statistics()["logical_charges"] == 0
    assert buy(*prepared) == outcome
    assert runtime.payments.execute.call_count == 1


@pytest.mark.parametrize("change", ["expire", "revoke"])
def test_recheck_before_dispatch(prepared, change):
    runtime, _, _ = prepared

    def checkpoint(point):
        if point == Checkpoint.AFTER_RESERVATION:
            if change == "expire":
                runtime.clock.advance(60)
            else:
                runtime.ledger.revoke_grant("grant", runtime.clock.now())

    runtime.coordinator.checkpoint = checkpoint
    outcome = buy(*prepared)
    assert outcome.payment_state == "denied"
    assert runtime.payments.execute.call_count == 0
    assert runtime.ledger.budget("grant").reserved == 0


def test_crash_before_adapter_is_unknown_not_retryable(prepared):
    runtime, _, _ = prepared

    def crash(point):
        if point == Checkpoint.AFTER_DISPATCH:
            raise SimulatedCrash()

    runtime.coordinator.checkpoint = crash
    with pytest.raises(SimulatedCrash):
        buy(*prepared)
    assert buy(*prepared).payment_state == "executing"
    assert asyncio.run(runtime.coordinator.reconcile("task")).payment_state == "unknown"
    assert runtime.ledger.budget("grant").reserved == 10_000
    assert runtime.payments.execute.call_count == 0
    assert asyncio.run(runtime.coordinator.reconcile("task", cancel=True)).payment_state == "failed"
    assert runtime.ledger.budget("grant").reserved == 0


def test_mismatched_receipt_retains_exposure(prepared):
    runtime, request, quote = prepared
    purchase_id = runtime.ledger.task("task").purchase_id
    runtime.ledger.reserve(purchase_id, request, quote, "grant", runtime.clock.now())
    intent = runtime.ledger.claim(purchase_id, runtime.clock.now())
    result = runtime.payments.execute(intent)
    forged = result.receipt.model_copy(update={"intent": intent.model_copy(update={"amount": 1})})
    runtime.ledger.resolve(
        purchase_id, Resolution(status="settled", receipt=forged), runtime.clock.now()
    )
    assert runtime.ledger.outcome(purchase_id).payment_error == "receipt_intent_mismatch"
    assert runtime.ledger.budget("grant").reserved == 10_000
    assert asyncio.run(runtime.coordinator.reconcile("task")).payment_state == "settled"


@pytest.mark.parametrize(
    ("checkpoint", "expected", "attempts", "charges"),
    [
        (Checkpoint.AFTER_RESERVATION, "failed", 0, 0),
        (Checkpoint.AFTER_DISPATCH, "unknown", 0, 0),
        (Checkpoint.AFTER_PAYMENT, "settled", 1, 1),
        (Checkpoint.AFTER_SETTLEMENT, "settled", 1, 1),
        (Checkpoint.AFTER_DELIVERY, "settled", 1, 1),
    ],
)
def test_real_process_exit_and_fresh_process_recovery(
    prepared, tmp_path, checkpoint, expected, attempts, charges
):
    runtime, _, quote = prepared
    worker = Path(__file__).with_name("recovery_worker.py")
    base = [sys.executable, str(worker), str(tmp_path), quote.quote_id]
    crashed = subprocess.run([*base, str(checkpoint)], capture_output=True, text=True, timeout=30)
    assert crashed.returncode == 73, crashed.stderr
    recovered = subprocess.run([*base, "reconcile"], capture_output=True, text=True, timeout=30)
    assert recovered.returncode == 0, recovered.stderr
    result = json.loads(recovered.stdout)
    assert result["payment_state"] == expected
    assert result["stats"]["execution_attempts"] == attempts
    assert result["stats"]["logical_charges"] == charges
    assert runtime.ledger.budget("grant").reserved == (10_000 if expected == "unknown" else 0)
    if expected == "settled":
        assert result["delivery_state"] == "delivered"
        assert runtime.ledger.budget("grant").spent == 10_000
    again = asyncio.run(runtime.coordinator.reconcile("task"))
    assert again.payment_state == expected
    assert runtime.payments.statistics()["execution_attempts"] == attempts
