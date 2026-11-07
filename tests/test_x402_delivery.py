import asyncio
import copy
import json
import traceback
import warnings

import pytest
from delivery_support import (
    accept,
    acceptance_rows,
    acceptor,
    assert_no_new_execution,
    cached_report,
    financial_rows,
    merchant_rows,
    settled_shop,
    write_evidence,
)
from evidence_support import paid, reconcile, track_dispatches
from fastapi.responses import JSONResponse, Response
from merchant_support import scenario
from test_x402_gate import MARKER, NOW

from agentic_payments_lab.domain import digest
from agentic_payments_lab.x402_contracts import GateDenied
from agentic_payments_lab.x402_delivery import DeliveryUnavailable, OfflineDeliveryAcceptor
from agentic_payments_lab.x402_delivery_contracts import MerchantDeliveryReport
from agentic_payments_lab.x402_merchant_store import CACHE_SECONDS
from agentic_payments_lab.x402_provider import OfflineMerchantClient
from agentic_payments_lab.x402_store import OfflineGateStore


@pytest.fixture
def shop(tmp_path, monkeypatch):
    return settled_shop(tmp_path, monkeypatch)


def test_acceptance_and_audit_are_one_record_and_replay_needs_no_retrieval(shop, monkeypatch):
    finance, merchant = financial_rows(shop.buyer), merchant_rows(shop)
    original = cached_report(shop)["resource"]
    retrievals = []
    method = OfflineMerchantClient.retrieve

    async def retrieve(self, purchase_id):
        retrievals.append(purchase_id)
        return await method(self, purchase_id)

    monkeypatch.setattr(OfflineMerchantClient, "retrieve", retrieve)
    first = accept(shop)
    assert first["current_invocation"] == {
        "outcome": "accepted",
        "reason": None,
        "retrieval_attempts": 1,
    }
    assert first["delivery_state"] == "delivered"
    assert first["phase"] == "fixture_settled"
    assert not first["blockchain_settlement_verified"]
    assert first["delivery"]["source"] == "offline_fixture"
    assert first["delivery"]["acceptance"]["resource_hash"] == original["resource_hash"]
    assert first["delivery"]["acceptance"]["envelope_hash"] == digest(original)
    assert original["resource"]["summary"] not in json.dumps(first)
    reopened = OfflineGateStore(shop.buyer.db.path, shop.buyer.policy)
    again = accept(shop, store=reopened)
    assert again["current_invocation"] == {
        "outcome": "already_accepted",
        "reason": None,
        "retrieval_attempts": 0,
    }
    assert again["delivery"] == first["delivery"]
    assert len(retrievals) == len(acceptance_rows(reopened)) == 1
    assert "current_invocation" not in reopened.report("task")
    assert financial_rows(reopened) == finance
    assert merchant_rows(shop) == merchant
    assert_no_new_execution(shop)


def test_100_overlapping_reads_commit_one_acceptance_without_execution(shop, monkeypatch, tmp_path):
    method = OfflineMerchantClient.retrieve
    finance, merchant = financial_rows(shop.buyer), merchant_rows(shop)
    arrivals = []

    async def run():
        all_arrived, release = asyncio.Event(), asyncio.Event()

        async def retrieve(self, purchase_id):
            result = await method(self, purchase_id)
            arrivals.append(purchase_id)
            if len(arrivals) == 100:
                all_arrived.set()
            await release.wait()
            return result

        monkeypatch.setattr(OfflineMerchantClient, "retrieve", retrieve)
        tasks = [asyncio.create_task(acceptor(shop).accept("task")) for _ in range(100)]
        try:
            await asyncio.wait_for(all_arrived.wait(), 4)
            assert len(arrivals) == 100 and not any(t.done() for t in tasks)
            assert acceptance_rows(shop.buyer) == []
            # Retrieval never holds a buyer write lock across its await.
            with shop.buyer.db.transaction() as db:
                assert db.execute("SELECT spent FROM grants").fetchone()[0] == 10_000
        finally:
            release.set()
        return await asyncio.gather(*tasks)

    reports = asyncio.run(run())
    assert sum(r["current_invocation"]["outcome"] == "accepted" for r in reports) == 1
    assert all(r["delivery_state"] == "delivered" for r in reports)
    assert all(r["current_invocation"]["retrieval_attempts"] == 1 for r in reports)
    assert len(acceptance_rows(shop.buyer)) == 1
    assert financial_rows(shop.buyer) == finance
    assert merchant_rows(shop) == merchant
    assert_no_new_execution(shop)
    write_evidence(tmp_path / "100-overlap-evidence.json", shop, reports[0])


@pytest.mark.parametrize(
    "field,value",
    [
        ("purchase_id", "other-purchase"),
        ("payment_id", "other-payment"),
        ("mode", "real_x402"),
        ("blockchain_settlement_verified", True),
        ("blockchain_settlement_verified", 0),
        ("settlement_may_have_occurred", "true"),
        ("resource.purchase_id", "other-purchase"),
        ("resource.receipt_id", "fake-receipt"),
        ("resource.resource.service_id", "other-service"),
        ("resource.resource.subject", "other-subject"),
        ("resource.resource.request_hash", "a" * 64),
        ("resource.resource.quality", "premium"),
        ("resource.resource.summary", MARKER),
        ("resource.resource.summary", "x" * 257),
        ("resource.resource_hash", "a" * 64),
        ("resource.resource_hash", "A" * 64),
        ("resource.resource.subject", 42),
        ("delivery_state", "pending"),
        ("settlement_observation", None),
        ("resource.resource.extra", MARKER),
        ("signature", MARKER),
    ],
)
def test_changed_coerced_or_unknown_response_fields_cannot_be_accepted(
    shop, monkeypatch, field, value, caplog
):
    response = copy.deepcopy(cached_report(shop))
    parent = response
    *path, key = field.split(".")
    for part in path:
        parent = parent[part]
    parent[key] = value
    monkeypatch.setattr(shop.merchant, "_result", lambda _: JSONResponse(response))
    finance = financial_rows(shop.buyer)
    result = accept(shop)
    assert result["delivery_state"] == "failed"
    assert result["current_invocation"]["reason"] == "unavailable_or_invalid"
    assert acceptance_rows(shop.buyer) == []
    assert financial_rows(shop.buyer) == finance
    assert MARKER not in json.dumps(result) + caplog.text
    for path in shop.buyer.db.path.parent.iterdir():
        if path.is_file():
            assert MARKER.encode() not in path.read_bytes()
    assert_no_new_execution(shop)


def test_recomputed_hash_does_not_authorize_changed_deterministic_content(shop, monkeypatch):
    response = cached_report(shop)
    response["resource"]["resource"]["summary"] = "substituted content"
    response["resource"]["resource_hash"] = digest(response["resource"]["resource"])
    monkeypatch.setattr(shop.merchant, "_result", lambda _: JSONResponse(response))
    assert accept(shop)["delivery_state"] == "failed"
    assert acceptance_rows(shop.buyer) == []
    assert_no_new_execution(shop)


@pytest.mark.parametrize(
    "kind",
    [
        "oversized",
        "invalid_json",
        "duplicate",
        "nested_duplicate",
        "list",
        "null",
        "wrong_status",
        "wrong_type",
        "redirect",
    ],
)
def test_wire_boundary_rejects_bad_bodies_and_http_envelopes(shop, monkeypatch, kind):
    raw = json.dumps(cached_report(shop)).encode()
    status, media = 200, "application/json"
    if kind == "oversized":
        raw += b" " * 16_385
    elif kind == "invalid_json":
        raw = b"{"
    elif kind == "duplicate":
        raw = raw[:-1] + b',"mode":"offline_x402_merchant"}'
    elif kind == "nested_duplicate":
        raw = raw.replace(b'"quality": "basic"', b'"quality":"basic","quality":"basic"')
    elif kind == "list":
        raw = b"[]"
    elif kind == "null":
        raw = b"null"
    elif kind == "wrong_status":
        status = 202
    elif kind == "redirect":
        status = 302
    elif kind == "wrong_type":
        media = "text/plain"
    monkeypatch.setattr(
        shop.merchant, "_result", lambda _: Response(raw, status_code=status, media_type=media)
    )
    result = accept(shop)
    assert result["delivery_state"] == "failed"
    assert result["current_invocation"]["retrieval_attempts"] == 1
    assert acceptance_rows(shop.buyer) == []
    assert_no_new_execution(shop)


def test_other_tasks_cached_result_is_not_rebound(shop, monkeypatch):
    other = shop.enroll("other")
    response = cached_report(shop)
    response["purchase_id"] = response["resource"]["purchase_id"] = other.purchase_id
    monkeypatch.setattr(shop.merchant, "_result", lambda _: JSONResponse(response))
    finance = financial_rows(shop.buyer)
    assert accept(shop)["delivery_state"] == "failed"
    assert financial_rows(shop.buyer) == finance
    assert shop.buyer.report("other")["phase"] == "not_started"
    assert acceptance_rows(shop.buyer) == []
    assert_no_new_execution(shop)


def test_unknown_payment_is_reserved_and_cached_http_success_cannot_settle(tmp_path, monkeypatch):
    shop = scenario(tmp_path)
    shop.dispatch_entries = track_dispatches(monkeypatch)
    paid(shop)
    assert cached_report(shop)["delivery_state"] == "cached"
    retrieved = []

    async def retrieve(*args):
        retrieved.append(True)
        return cached_report(shop)

    monkeypatch.setattr(OfflineMerchantClient, "retrieve", retrieve)
    finance = financial_rows(shop.buyer)
    with pytest.raises(GateDenied):
        accept(shop)
    assert not retrieved and not acceptance_rows(shop.buyer)
    assert financial_rows(shop.buyer) == finance
    assert shop.buyer.report("task")["budget"] == {"budget": 30_000, "reserved": 10_000, "spent": 0}
    assert_no_new_execution(shop)


@pytest.mark.parametrize("task", ["missing", "not-started", 42, True, "x" * 101, "../task"])
def test_invalid_missing_or_unsettled_task_never_retrieves(shop, monkeypatch, task):
    shop.enroll("not-started")
    calls = []

    async def forbidden(*args):
        calls.append(True)
        raise AssertionError("Must reject before retrieval")

    monkeypatch.setattr(OfflineMerchantClient, "retrieve", forbidden)
    finance = financial_rows(shop.buyer)
    with pytest.raises((GateDenied, DeliveryUnavailable)):
        asyncio.run(acceptor(shop).accept(task))
    assert not calls and not acceptance_rows(shop.buyer)
    assert financial_rows(shop.buyer) == finance
    assert_no_new_execution(shop)


def test_expired_cache_fails_without_repayment_or_acceptance(shop):
    shop.now[0] = NOW + CACHE_SECONDS
    finance = financial_rows(shop.buyer)
    result = accept(shop)
    assert result["delivery_state"] == "failed"
    assert result["current_invocation"]["reason"] == "gone"
    assert not acceptance_rows(shop.buyer)
    assert financial_rows(shop.buyer) == finance
    assert_no_new_execution(shop)


@pytest.mark.parametrize("state", ["not_ready", "pending"])
def test_valid_unavailable_delivery_never_changes_settled_finance(shop, monkeypatch, state):
    response = cached_report(shop)
    response.update(delivery_state=state, resource=None)
    monkeypatch.setattr(shop.merchant, "_result", lambda _: JSONResponse(response, status_code=202))
    finance, merchant = financial_rows(shop.buyer), merchant_rows(shop)
    report = accept(shop)
    assert report["current_invocation"] == {
        "outcome": "failed",
        "reason": state,
        "retrieval_attempts": 1,
    }
    assert report["phase"] == "fixture_settled" and report["delivery_state"] == "failed"
    assert not acceptance_rows(shop.buyer)
    assert financial_rows(shop.buyer) == finance and merchant_rows(shop) == merchant
    assert_no_new_execution(shop)


def test_conclusive_nonuse_does_not_authorize_delivery(tmp_path, monkeypatch):
    shop = scenario(tmp_path)
    shop.buyer.claim("task", digest({"unused": True}), NOW)
    reconcile(shop.buyer, unused=True)
    finance = financial_rows(shop.buyer)
    calls = []

    async def retrieve(*args):
        calls.append(True)
        raise AssertionError("Unused authorization must not retrieve delivery")

    monkeypatch.setattr(OfflineMerchantClient, "retrieve", retrieve)
    with pytest.raises(GateDenied):
        accept(shop)
    assert not calls and not acceptance_rows(shop.buyer)
    assert financial_rows(shop.buyer) == finance
    assert shop.buyer.report("task")["phase"] == "fixture_unused"
    assert shop.authority.counts() == (0, 0, 0)
    assert not shop.signer.calls


def test_failed_retrieval_then_explicit_acceptance_and_expiry_does_not_downgrade(shop, monkeypatch):
    original = shop.merchant._result
    monkeypatch.setattr(shop.merchant, "_result", lambda _: Response(status_code=503))
    assert accept(shop)["delivery_state"] == "failed"
    assert accept(shop)["delivery_state"] == "failed"
    with shop.buyer.db.connect() as db:
        assert (
            db.execute(
                "SELECT count(*) FROM events WHERE event_type='offline_delivery_failed'"
            ).fetchone()[0]
            == 1
        )
    monkeypatch.setattr(shop.merchant, "_result", original)
    result = accept(shop)
    shop.now[0] = NOW + CACHE_SECONDS
    again = accept(shop)
    assert again["delivery_state"] == "delivered"
    assert again["delivery"] == result["delivery"]
    assert again["current_invocation"]["retrieval_attempts"] == 0
    assert len(acceptance_rows(shop.buyer)) == 1
    assert_no_new_execution(shop)


def test_expired_revoked_grant_allows_noncharging_acceptance(shop):
    with shop.buyer.db.transaction() as db:
        db.execute("UPDATE grants SET revoked=1")
    runner = acceptor(shop)
    runner.clock = lambda: NOW + 1000
    assert asyncio.run(runner.accept("task"))["delivery_state"] == "delivered"
    assert_no_new_execution(shop)


def test_only_matching_closed_merchant_client_is_accepted(shop):
    class Fake:
        mode = "offline_double"

    with pytest.raises(GateDenied):
        OfflineDeliveryAcceptor(shop.buyer, Fake(), lambda: NOW)
    other_policy = shop.merchant.policy.model_copy(
        update={"resource": "https://other.invalid/report"}
    )
    original = shop.merchant.policy
    shop.merchant.policy = other_policy
    try:
        with pytest.raises(GateDenied):
            acceptor(shop)
    finally:
        shop.merchant.policy = original
    assert_no_new_execution(shop)


def test_forged_models_and_errors_cannot_retain_payload_markers(shop, monkeypatch, caplog):
    binding = shop.buyer.delivery_request("task")
    data = cached_report(shop)
    data["blockchain_settlement_verified"] = MARKER
    forged = MerchantDeliveryReport.model_construct(**data)
    with warnings.catch_warnings(record=True) as caught:
        shop.buyer.record_delivery("task", binding, forged, "unavailable_or_invalid", NOW)
    assert not caught and not acceptance_rows(shop.buyer)

    async def fails(*args):
        raise RuntimeError(MARKER)

    monkeypatch.setattr(OfflineMerchantClient, "retrieve", fails)
    result = accept(shop)
    assert MARKER not in json.dumps(result) + caplog.text
    for path in shop.buyer.db.path.parent.iterdir():
        if path.is_file():
            assert MARKER.encode() not in path.read_bytes()
    runner = acceptor(shop)
    runner.clock = lambda: (_ for _ in ()).throw(RuntimeError(MARKER))
    with pytest.raises(DeliveryUnavailable) as error:
        asyncio.run(runner.accept("task"))
    assert MARKER not in "".join(traceback.format_exception(error.value))
    assert_no_new_execution(shop)
