"""Actual offline ASGI exchange, binding, independent settlement counts and cache semantics."""

import asyncio
import base64
import json
import sqlite3

import httpx
import pytest
from merchant_support import scenario
from test_x402_gate import NOW, evidence_for
from x402.http.utils import decode_payment_required_header, decode_payment_response_header

from agentic_payments_lab.buyer_schema import SchemaError
from agentic_payments_lab.domain import TaskContext, digest
from agentic_payments_lab.x402_adapter.protocol import required_data, validate_required
from agentic_payments_lab.x402_contracts import GateDenied
from agentic_payments_lab.x402_merchant_contracts import (
    MerchantOrder,
)
from agentic_payments_lab.x402_merchant_store import CACHE_SECONDS, MerchantConflict, MerchantStore
from agentic_payments_lab.x402_provider import OfflineMerchant


@pytest.fixture
def shop(tmp_path):
    return scenario(tmp_path)


def wire(header):
    return json.loads(base64.b64decode(header))


def encode(data):
    return base64.b64encode(json.dumps(data).encode()).decode()


def test_asgi_exchange_is_identified_and_does_not_settle_buyer(shop):
    async def run():
        challenge = await shop.client.challenge(shop.context().purchase_id, shop.request)
        sdk = decode_payment_required_header(challenge)
        assert sdk.extensions["payment-identifier"]["info"] == {"required": True}
        # The original closed Phase 3B.1 profile still rejects extensions.
        with pytest.raises(GateDenied):
            validate_required(
                challenge,
                policy=shop.buyer.policy,
                resource=shop.buyer.policy.resource,
                method="POST",
            )
        buyer = await shop.run()
        result = await shop.client.retrieve(shop.context().purchase_id)
        return buyer, result

    buyer, result = asyncio.run(run())
    assert len(shop.signer.calls) == 1
    assert shop.authority.counts() == (1, 1, 10_000)
    assert buyer["phase"] == "reconciliation_required"
    assert buyer["budget"] == {"budget": 30_000, "reserved": 10_000, "spent": 0}
    assert buyer["delivery_state"] == "not_requested"
    assert result["blockchain_settlement_verified"] is False
    assert result["delivery_state"] == "cached"
    assert result["resource"]["purchase_id"] == buyer["purchase_id"]
    assert result["resource"]["resource_hash"] == digest(result["resource"]["resource"])
    assert result["resource"]["receipt_id"] is None
    # Only explicit operator fixture application can move the buyer's reservation.
    shop.buyer.apply_fixture("task", evidence_for(shop.buyer), NOW + 1)
    shop.buyer.apply_fixture("task", evidence_for(shop.buyer), NOW + 1)
    assert shop.buyer.report("task")["budget"] == {"budget": 30_000, "reserved": 0, "spent": 10_000}
    assert shop.authority.counts() == (1, 1, 10_000)


def test_identical_replay_restart_expiry_and_explicit_delivery_dedup(shop):
    purchase, header, _ = shop.payment()
    first = shop.send(purchase, header)
    assert first.status_code == 200
    response = decode_payment_response_header(first.headers["PAYMENT-RESPONSE"])
    assert response.success and response.network == "eip155:84532"
    for _ in range(3):
        shop.merchant.store.prepare_delivery(purchase, NOW)
        assert shop.send(purchase, header).json() == first.json()
    store = MerchantStore(
        shop.merchant.store.db.path,
        shop.buyer.policy,
        digest(required_data(shop.buyer.policy, identified=True)),
    )
    shop.merchant = OfflineMerchant(store, shop.authority, lambda: shop.now[0])
    shop.now[0] += 70  # Paid replay after authorization expiry returns existing content.
    assert shop.send(purchase, header).json() == first.json()
    shop.now[0] = NOW + CACHE_SECONDS
    assert asyncio.run(shop.client.retrieve(purchase))["delivery_state"] == "gone"
    store.expire_cache(shop.now[0])
    store.prepare_delivery(purchase, shop.now[0])  # Does not resurrect an expired cache.
    assert shop.send(purchase, header).status_code == 410
    with store.db.connect() as db:
        assert db.execute("SELECT content FROM deliveries").fetchone()[0] is None
        assert db.execute("SELECT count(*) FROM settlements").fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM orders").fetchone()[0] == 1
    assert shop.authority.counts() == (1, 1, 10_000)


@pytest.mark.parametrize("outcome", ["pending", "reported_failure", "unknown"])
def test_inconclusive_or_rejected_outcome_never_retries_or_delivers(shop, outcome):
    shop.authority.outcome = outcome
    purchase, header, _ = shop.payment()
    for _ in range(3):
        response = shop.send(purchase, header)
        assert response.status_code == 202
        assert response.json()["delivery_state"] == "not_ready"
        assert response.json()["settlement_observation"]["outcome"] == outcome
    with pytest.raises(GateDenied, match="not_ready"):
        shop.merchant.store.prepare_delivery(purchase, NOW)
    assert shop.authority.counts() == (1, 0, 0)
    assert shop.buyer.report("task")["budget"]["reserved"] == 10_000


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("x402Version",), True),
        (("x402Version",), "2"),
        (("resource", "url"), "https://evil.invalid/report"),
        (("accepted", "scheme"), "upto"),
        (("accepted", "network"), "eip155:8453"),
        (("accepted", "amount"), 10000),
        (("accepted", "amount"), "010000"),
        (("accepted", "asset"), "SIM-USDC"),
        (("accepted", "payTo"), "0x" + "33" * 20),
        (("accepted", "maxTimeoutSeconds"), 60.0),
        (("accepted", "extra", "version"), "3"),
        (("accepted", "extra", "assetTransferMethod"), "permit2"),
        (("payload", "authorization", "from"), "0x" + "33" * 20),
        (("payload", "authorization", "to"), "0x" + "33" * 20),
        (("payload", "authorization", "value"), True),
        (("payload", "authorization", "value"), "1e4"),
        (("payload", "authorization", "value"), "010000"),
        (("payload", "authorization", "validAfter"), "1"),
        (("payload", "authorization", "validBefore"), str(NOW)),
        (("payload", "authorization", "validBefore"), str(NOW + 61)),
        (("payload", "authorization", "validBefore"), str(NOW + 1000)),
        (("payload", "authorization", "nonce"), "0x" + "AA" * 32),
        (("payload", "signature"), "0x" + "ab" * 65),
        (("extensions", "payment-identifier", "info", "id"), "pay_fabricated_identity"),
        (("extensions", "payment-identifier", "info", "required"), 1),
        (("extensions",), {}),
    ],
)
def test_changed_or_coerced_wire_terms_reject_before_settlement(shop, path, value):
    purchase, header, _ = shop.payment()
    data = wire(header)
    target = data
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    assert shop.send(purchase, encode(data)).status_code == 400
    assert shop.authority.counts() == (0, 0, 0)
    with shop.merchant.store.db.connect() as db:
        assert db.execute("SELECT count(*) FROM settlements").fetchone()[0] == 0


@pytest.mark.parametrize("bad", ["not-base64", "x" * 8193, "W10=", "e30=", "bnVsbA=="])
def test_malformed_header_rejects_without_claim(shop, bad):
    purchase, _, _ = shop.payment()
    assert shop.send(purchase, bad).status_code == 400
    assert shop.authority.counts() == (0, 0, 0)


def test_duplicate_keys_unknown_fields_and_extensions_reject(shop):
    purchase, header, _ = shop.payment()
    raw = base64.b64decode(header).decode()
    duplicate = raw.replace('"x402Version":2', '"x402Version":2,"x402Version":2')
    assert duplicate != raw
    assert shop.send(purchase, base64.b64encode(duplicate.encode()).decode()).status_code == 400
    for parent, key, value in [
        ("payload", "secret", "marker"),
        ("extensions", "unsupported", {}),
        ("accepted", "unknown", True),
    ]:
        data = wire(header)
        data[parent][key] = value
        assert shop.send(purchase, encode(data)).status_code == 400
    assert shop.authority.counts() == (0, 0, 0)


def test_enrollment_is_explicit_immutable_and_rejects_simulator_identity(shop):
    context = shop.context()
    shop.enroll()  # Idempotent explicit enrollment of the same original binding.
    for changed in [
        MerchantOrder(
            context=context, request=shop.request.model_copy(update={"subject": "changed"})
        ),
        MerchantOrder(
            context=context.model_copy(update={"task_id": "changed"}), request=shop.request
        ),
        MerchantOrder(
            context=context.model_copy(update={"purchase_id": "offline-purchase-" + "a" * 64}),
            request=shop.request,
        ),
    ]:
        with pytest.raises(MerchantConflict):
            shop.merchant.store.enroll(changed)
    for context in [
        TaskContext(task_id="new", grant_id="simulator", purchase_id="simulated-purchase"),
        TaskContext(
            task_id="new",
            grant_id=shop.buyer.policy.grant.grant_id,
            purchase_id="simulated-purchase",
        ),
    ]:
        with pytest.raises(GateDenied):
            shop.merchant.store.enroll(MerchantOrder(context=context, request=shop.request))
    missing = shop.context("unenrolled")
    with pytest.raises(GateDenied):
        asyncio.run(shop.client.challenge(missing.purchase_id, shop.request))
    assert shop.authority.counts() == (0, 0, 0)


def test_changed_body_identifier_nonce_and_signature_retries_conflict(shop):
    purchase, header, _ = shop.payment()
    assert shop.send(purchase, header).status_code == 200
    changed = shop.request.model_copy(update={"subject": "different"})
    assert shop.send(purchase, header, changed).status_code == 409
    for field, value in [("nonce", "0x" + "99" * 32), ("validBefore", str(NOW + 59))]:
        data = wire(header)
        data["payload"]["authorization"][field] = value
        assert shop.send(purchase, encode(data)).status_code == 409
    data = wire(header)
    data["payload"]["signature"] = "offline-test-signature:another_marker"
    assert shop.send(purchase, encode(data)).status_code == 409
    assert shop.authority.counts() == (1, 1, 10_000)


def test_nonce_reuse_across_purchases_and_conflicting_concurrent_retries(shop):
    purchase, header, terms = shop.payment()
    shop.enroll("other")
    other_purchase, other_header, _ = shop.payment("other")
    other = wire(other_header)
    other["payload"]["authorization"]["nonce"] = terms.nonce
    assert shop.send(purchase, header).status_code == 200
    assert shop.send(other_purchase, encode(other)).status_code == 409
    assert shop.authority.counts() == (1, 1, 10_000)


def test_100_concurrent_paid_replays_one_settlement_and_one_cache_row(shop):
    purchase, header, _ = shop.payment()
    original = shop.authority.settle
    entries = []

    async def run():
        entered, finish = asyncio.Event(), asyncio.Event()

        async def held(**kwargs):
            # Measure entry before suspension, independently of the inner
            # authority's execution/transfer records after the barrier.
            entries.append(kwargs["authorization"].nonce)
            entered.set()
            await finish.wait()
            return await original(**kwargs)

        shop.authority.settle = held

        async def send():
            return await shop.client._request(
                purchase_id=purchase, request=shop.request, payment_header=header
            )

        first = asyncio.create_task(send())
        try:
            await asyncio.wait_for(entered.wait(), timeout=5)
            replies = await asyncio.wait_for(
                asyncio.gather(*[send() for _ in range(99)]), timeout=5
            )
            assert not first.done()
            assert len(entries) == 1
            assert all(reply.status_code == 202 for reply in replies)
            assert len({reply.text for reply in replies}) == 1
            assert replies[0].json()["settlement_observation"] is None
            assert replies[0].json()["resource"] is None
            assert shop.authority.counts() == (0, 0, 0)
            with shop.merchant.store.db.connect() as db:
                assert db.execute("SELECT count(*) FROM settlements").fetchone()[0] == 1
                assert db.execute("SELECT count(*) FROM deliveries").fetchone()[0] == 0
        finally:
            finish.set()
            completed = await first
        assert completed.status_code == 200
        assert completed.json()["delivery_state"] == "cached"
        assert await shop.client.retrieve(purchase) == completed.json()

    asyncio.run(run())
    assert len(entries) == 1
    assert shop.authority.counts() == (1, 1, 10_000)
    with shop.merchant.store.db.connect() as db:
        assert db.execute("SELECT count(*) FROM settlements").fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM deliveries").fetchone()[0] == 1


def test_100_gate_calls_and_20_budget_competitors_measure_authority(shop):
    async def same():
        return await asyncio.gather(*[shop.run() for _ in range(100)])

    asyncio.run(same())
    assert len(shop.signer.calls) == 1
    assert shop.authority.counts() == (1, 1, 10_000)
    for number in range(20):
        shop.enroll(f"task-{number}")

    async def compete():
        return await asyncio.gather(
            *[shop.run(f"task-{number}") for number in range(20)], return_exceptions=True
        )

    results = asyncio.run(compete())
    assert sum(isinstance(result, dict) for result in results) == 2
    assert all(isinstance(result, (dict, GateDenied)) for result in results)
    assert len(shop.signer.calls) == 3
    assert shop.authority.counts() == (3, 3, 30_000)
    assert shop.buyer.report("task")["budget"] == {"budget": 30_000, "reserved": 30_000, "spent": 0}


def test_untrusted_asgi_has_no_paid_or_cache_access(shop):
    async def run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=shop.merchant.app)
        ) as client:
            paid = await client.post(shop.buyer.policy.resource, json={})
            cached = await client.get(
                "https://merchant.invalid/__offline/status/" + shop.context().purchase_id
            )
            return paid, cached

    paid, cached = asyncio.run(run())
    assert (paid.status_code, cached.status_code) == (400, 404)
    assert shop.authority.counts() == (0, 0, 0)


def test_merchant_identity_and_nonce_tombstones_cannot_be_reset(shop):
    purchase, header, _ = shop.payment()
    shop.send(purchase, header)
    for sql in [
        "DELETE FROM orders",
        "DELETE FROM settlements",
        "DELETE FROM deliveries",
        "UPDATE orders SET task_id='new'",
        "UPDATE settlements SET nonce='other'",
        "UPDATE settlements SET outcome='unknown'",
        "UPDATE deliveries SET content='new'",
    ]:
        with shop.merchant.store.db.connect() as db, pytest.raises(sqlite3.IntegrityError):
            db.execute(sql)


def test_unknown_schema_rejection_is_read_only_and_missing_state_is_not_initialized(shop, tmp_path):
    path = tmp_path / "unsupported.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA user_version=999")
    before = path.read_bytes()
    with pytest.raises(SchemaError):
        MerchantStore(
            path, shop.buyer.policy, digest(required_data(shop.buyer.policy, identified=True))
        )
    assert path.read_bytes() == before
    assert not path.with_name(path.name + "-wal").exists()
    missing = tmp_path / "missing.sqlite3"
    with pytest.raises(SchemaError):
        MerchantStore(
            missing, shop.buyer.policy, digest(required_data(shop.buyer.policy, identified=True))
        )
    assert not missing.exists()
    with pytest.raises(SchemaError):
        MerchantStore.initialize(shop.merchant.store.db.path, shop.buyer.policy, "a" * 64)


def test_fabricated_settlement_dict_and_lost_response_cannot_account_buyer(shop):
    async def fabricate(**kwargs):
        return {"success": True, "transaction": "0x" + "ab" * 32}

    shop.authority.settle = fabricate
    report = asyncio.run(shop.run())
    assert report["phase"] == "reconciliation_required"
    assert report["budget"]["reserved"] == 10_000 and report["budget"]["spent"] == 0
    merchant = asyncio.run(shop.client.retrieve(shop.context().purchase_id))
    assert merchant["settlement_observation"] is None
    assert merchant["delivery_state"] == "not_ready"


def test_pending_claim_has_no_takeover_when_first_call_is_still_running(shop):
    purchase, header, _ = shop.payment()
    original = shop.authority.settle

    async def run():
        started, finish = asyncio.Event(), asyncio.Event()

        async def block(**kwargs):
            started.set()
            await finish.wait()
            return await original(**kwargs)

        shop.authority.settle = block
        first = asyncio.create_task(
            shop.client._request(purchase_id=purchase, request=shop.request, payment_header=header)
        )
        await started.wait()
        for _ in range(5):
            reply = await shop.client._request(
                purchase_id=purchase, request=shop.request, payment_header=header
            )
            assert reply.status_code == 202 and reply.json()["settlement_observation"] is None
        assert shop.authority.counts() == (0, 0, 0)
        finish.set()
        assert (await first).status_code == 200

    asyncio.run(run())
    assert shop.authority.counts() == (1, 1, 10_000)


@pytest.mark.parametrize(
    "case", ["extra_context", "duplicate_key", "oversize", "coercion", "duplicate_header"]
)
def test_raw_http_body_and_header_rejections_precede_settlement(shop, case):
    purchase, header, _ = shop.payment()
    body = {"purchase_id": purchase, "request": shop.request.model_dump()}
    if case == "extra_context":
        body["grant_id"] = "replace-grant"
    if case == "coercion":
        body["request"]["subject"] = 123
    encoded = json.dumps(body)
    if case == "duplicate_key":
        encoded = encoded.replace('"purchase_id":', '"purchase_id":"ignored","purchase_id":')
    if case == "oversize":
        encoded += " " * 4097
    headers = [("content-type", "application/json"), ("PAYMENT-SIGNATURE", header)]
    if case == "duplicate_header":
        headers.append(("PAYMENT-SIGNATURE", header))

    async def run():
        async def asgi(scope, receive, send):
            await shop.merchant.app(
                {**scope, "offline_merchant_access": shop.merchant._access}, receive, send
            )

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=asgi)) as client:
            return await client.post(shop.buyer.policy.resource, headers=headers, content=encoded)

    response = asyncio.run(run())
    assert response.status_code == 400
    assert header not in response.text
    assert shop.authority.counts() == (0, 0, 0)
    with shop.merchant.store.db.connect() as db:
        assert db.execute("SELECT count(*) FROM settlements").fetchone()[0] == 0


def test_body_key_order_does_not_change_canonical_binding(shop):
    purchase, header, _ = shop.payment()
    first = shop.send(purchase, header)
    request = type(shop.request).model_validate(dict(reversed(shop.request.model_dump().items())))
    assert shop.send(purchase, header, request).json() == first.json()
    assert shop.authority.counts() == (1, 1, 10_000)


def test_changed_merchant_profile_or_buyer_file_cannot_be_adopted(shop):
    changed = shop.buyer.policy.model_copy(update={"recipient": "0x" + "33" * 20})
    for path, policy, requirement_hash in [
        (shop.merchant.store.db.path, changed, digest(required_data(changed, identified=True))),
        (shop.merchant.store.db.path, shop.buyer.policy, "a" * 64),
        (
            shop.buyer.db.path,
            shop.buyer.policy,
            digest(required_data(shop.buyer.policy, identified=True)),
        ),
    ]:
        before = path.read_bytes()
        with pytest.raises(SchemaError):
            MerchantStore(path, policy, requirement_hash)
        assert path.read_bytes() == before
