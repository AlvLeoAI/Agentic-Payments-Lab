import asyncio
import json
import subprocess
import sys

import pytest
from test_authorization import buy

from agentic_payments_lab.domain import DeliveryRequest


def test_discovery_and_payment_required(prepared):
    runtime, request, quote = prepared
    catalog = asyncio.run(runtime.merchant.request("GET", "/services"))
    assert catalog.status_code == 200
    assert len(catalog.json()) == 3
    assert catalog.json()[0]["amount"] == 0
    assert "request_schema" in catalog.json()[0] and "response_schema" in catalog.json()[0]
    delivery = DeliveryRequest(
        purchase_id=runtime.ledger.task("task").purchase_id, request=request, quote=quote
    )
    result = asyncio.run(
        runtime.merchant.request("POST", "/resources", delivery.model_dump(mode="json"))
    )
    assert result.status_code == 402
    assert runtime.payments.statistics()["logical_charges"] == 0
    with pytest.raises(ValueError):
        asyncio.run(runtime.merchant.request("GET", "https://untrusted.invalid"))


@pytest.mark.parametrize("change", ["receipt_id", "purchase_id", "quote", "amount"])
def test_receipt_forgery_and_replay_are_rejected(prepared, change):
    runtime, request, quote = prepared
    outcome = buy(*prepared)
    receipt = outcome.receipt
    purchase_id = outcome.purchase_id
    if change == "receipt_id":
        receipt = receipt.model_copy(update={"receipt_id": "forged"})
    if change == "purchase_id":
        purchase_id = "another-purchase"
    if change == "quote":
        quote = quote.model_copy(update={"recipient": "attacker"})
    if change == "amount":
        receipt = receipt.model_copy(
            update={"intent": receipt.intent.model_copy(update={"amount": 1})}
        )
    delivery = DeliveryRequest(
        purchase_id=purchase_id, request=request, quote=quote, receipt=receipt
    )
    response = asyncio.run(
        runtime.merchant.request("POST", "/resources", delivery.model_dump(mode="json"))
    )
    assert response.status_code in (400, 402)
    assert runtime.payments.statistics()["logical_charges"] == 1


def test_provider_returns_same_resource_after_expiry(prepared):
    runtime, request, quote = prepared
    outcome = buy(*prepared)
    runtime.clock.advance(4000)
    delivery = DeliveryRequest(
        purchase_id=outcome.purchase_id, request=request, quote=quote, receipt=outcome.receipt
    )
    assert asyncio.run(runtime.merchant.deliver(delivery)) == outcome.resource
    assert asyncio.run(runtime.merchant.deliver(delivery)) == outcome.resource
    assert runtime.payments.statistics()["logical_charges"] == 1


def test_cli_workflow_and_repeatable_demo(tmp_path):
    def cli(*args):
        result = subprocess.run(
            [sys.executable, "-m", "agentic_payments_lab", "--state-dir", str(tmp_path), *args],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)

    assert len(cli("list")) == 3
    cli("init")
    task = cli("task", "cli-task")
    quote = cli("quote")
    paid = cli("purchase", "cli-task", "--quote-id", quote["quote_id"])
    assert paid["purchase_id"] == task["purchase_id"]
    assert paid["payment_state"] == "settled" and paid["delivery_state"] == "delivered"
    assert cli("status", "cli-task") == paid
    assert cli("reconcile", "cli-task") == paid
    assert "receipt" not in cli("audit", "cli-task")
    demo = cli("demo")
    assert demo["paid"]["payment_state"] == "settled"
    assert demo["denied"]["payment_state"] == "denied"
    assert demo["adapter"]["logical_charges"] == 2  # CLI purchase plus demo purchase.
    assert cli("demo") == demo
