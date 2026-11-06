"""Crash gaps, cancellation, WAL retention and independent cross-process settlement counts."""

import asyncio
import os
import sqlite3
import subprocess
import sys
import threading
import time
import traceback
from contextlib import contextmanager
from pathlib import Path

import pytest
from merchant_support import scenario
from test_x402_gate import MARKER, NOW

from agentic_payments_lab.buyer_schema import SchemaError
from agentic_payments_lab.domain import digest
from agentic_payments_lab.x402_adapter.protocol import required_data
from agentic_payments_lab.x402_merchant_store import CACHE_SECONDS, MerchantStore


@pytest.fixture
def shop(tmp_path):
    return scenario(tmp_path)


@pytest.mark.parametrize("transaction", [1, 2, 3], ids=["claim", "observation", "cache"])
@pytest.mark.parametrize("ack", ["before_commit", "lost_ack"])
def test_persistence_failures_never_give_a_second_settlement_owner(
    shop, monkeypatch, transaction, ack
):
    purchase, header, _ = shop.payment()
    store = shop.merchant.store
    original = store.db.transaction
    count = 0

    @contextmanager
    def broken():
        nonlocal count
        count += 1
        number = count
        with original() as db:
            yield db
            if number == transaction and ack == "before_commit":
                raise sqlite3.OperationalError(MARKER + header)
        if number == transaction and ack == "lost_ack":
            raise sqlite3.OperationalError(MARKER + header)

    monkeypatch.setattr(store.db, "transaction", broken)
    reply = shop.send(purchase, header)
    assert reply.status_code == 503 and MARKER not in reply.text and header not in reply.text
    monkeypatch.setattr(store.db, "transaction", original)
    claimed = transaction != 1 or ack != "before_commit"
    authority_count = int(transaction > 1)
    assert shop.authority.counts() == (authority_count, authority_count, authority_count * 10_000)
    current = store.report(purchase, NOW)
    assert current["settlement_may_have_occurred"] is claimed
    if claimed:
        # Exercise an identical hostile/manual HTTP replay, never a client auto-retry.
        shop.send(purchase, header)
        assert shop.authority.counts() == (
            authority_count,
            authority_count,
            authority_count * 10_000,
        )
    if transaction > 2 or (transaction == 2 and ack == "lost_ack"):
        store.prepare_delivery(purchase, NOW)
        assert store.report(purchase, NOW)["delivery_state"] == "cached"
    else:
        assert current["delivery_state"] == "not_ready"
    # Buyer has never observed conclusive evidence, including when merchant caching succeeded.
    assert shop.buyer.report("task")["budget"] == {"budget": 30_000, "reserved": 10_000, "spent": 0}


@pytest.mark.parametrize("cancel_point", ["before_claim", "late_signer", "dispatch_wins"])
def test_existing_cancellation_fence_controls_actual_merchant_dispatch(shop, cancel_point):
    if cancel_point == "before_claim":
        shop.buyer.cancel("task", NOW)
    elif cancel_point == "late_signer":
        original = shop.signer.sign

        async def late(terms):
            signature = await original(terms)
            shop.buyer.cancel("task", NOW)
            return signature

        shop.signer.sign = late
    else:
        original = shop.buyer.authorize_dispatch

        def wins(*args):
            result = original(*args)
            shop.buyer.cancel("task", NOW)
            return result

        shop.buyer.authorize_dispatch = wins
    report = asyncio.run(shop.run())
    expected = int(cancel_point == "dispatch_wins")
    assert shop.authority.counts() == (expected, expected, expected * 10_000)
    assert report["budget"]["reserved"] == (0 if cancel_point == "before_claim" else 10_000)
    assert report["budget"]["spent"] == 0
    asyncio.run(shop.run())
    assert shop.authority.counts() == (expected, expected, expected * 10_000)


@pytest.mark.parametrize("swallow", [False, True])
@pytest.mark.parametrize("after_transfer", [False, True])
def test_external_cancellation_preserves_exposure_and_sanitizes_payloads(
    shop, swallow, after_transfer, caplog
):
    original = shop.authority.settle
    captured_header = []

    async def run():
        started = asyncio.Event()

        async def block(**kwargs):
            captured_header.append(kwargs["payment_header"])
            observation = await original(**kwargs) if after_transfer else None
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                if swallow:
                    # This deliberately late double may finish; never another invocation.
                    return observation or await original(**kwargs)
                error = asyncio.CancelledError(MARKER + kwargs["payment_header"])
                error.add_note(MARKER)
                raise error from None

        shop.authority.settle = block
        task = asyncio.create_task(shop.run())
        await started.wait()
        task.cancel(MARKER)
        with pytest.raises(asyncio.CancelledError) as caught:
            await task
        formatted = "".join(traceback.format_exception(caught.value))
        assert MARKER not in formatted and captured_header[0] not in formatted

    asyncio.run(run())
    expected = int(after_transfer or swallow)
    assert shop.authority.counts() == (expected, expected, expected * 10_000)
    buyer = shop.buyer.report("task")
    assert buyer["phase"] == "reconciliation_required" and buyer["budget"]["reserved"] == 10_000
    result = asyncio.run(shop.client.retrieve(shop.context().purchase_id))
    assert result["delivery_state"] == ("pending" if swallow else "not_ready")
    assert result["resource"] is None
    asyncio.run(shop.run())
    assert len(shop.signer.calls) == 1
    assert shop.authority.counts() == (expected, expected, expected * 10_000)
    assert MARKER not in caplog.text and captured_header[0] not in caplog.text


def test_cancellation_after_cache_commit_does_not_undo_or_duplicate_delivery(shop):
    original = shop.merchant.store.prepare_delivery

    def interrupt(*args):
        original(*args)
        raise asyncio.CancelledError(MARKER)

    shop.merchant.store.prepare_delivery = interrupt
    with pytest.raises(asyncio.CancelledError, match="offline_gate_canceled"):
        asyncio.run(shop.run())
    first = asyncio.run(shop.client.retrieve(shop.context().purchase_id))
    asyncio.run(shop.run())
    assert asyncio.run(shop.client.retrieve(shop.context().purchase_id)) == first
    assert first["delivery_state"] == "cached"
    assert shop.authority.counts() == (1, 1, 10_000)
    assert shop.buyer.report("task")["budget"]["reserved"] == 10_000


def test_secret_markers_absent_from_db_wal_reports_and_exception_paths(shop, caplog):
    purchase, header, _ = shop.payment()
    original = shop.authority.settle

    async def fail(**kwargs):
        await original(**kwargs)
        error = RuntimeError(MARKER + kwargs["payment_header"])
        error.add_note(MARKER)
        raise error

    shop.authority.settle = fail
    with shop.merchant.store.db.connect() as anchor:
        anchor.execute("BEGIN")
        anchor.execute("SELECT count(*) FROM orders").fetchone()
        response = shop.send(purchase, header)
        assert response.status_code == 503
        report = shop.merchant.store.report(purchase, NOW)
        assert report["settlement_observation"] is None
        assert shop.send(purchase, header).status_code == 202
        artifacts = list(shop.merchant.store.db.path.parent.glob("*.sqlite3*"))
        assert any(str(path).endswith("-wal") for path in artifacts)
        for path in artifacts:
            data = path.read_bytes()
            assert MARKER.encode() not in data and header.encode() not in data, path.name
        assert MARKER not in str(report) + caplog.text + response.text
    assert shop.authority.counts() == (1, 1, 10_000)


def test_audit_uses_one_snapshot_during_concurrent_cache_expiry(shop, monkeypatch):
    purchase, header, _ = shop.payment()
    shop.send(purchase, header)
    store = shop.merchant.store
    original = store.db.connect
    triggered = False
    errors = []

    def expire():
        try:
            with original() as db:
                db.execute("BEGIN IMMEDIATE")
                db.execute("UPDATE deliveries SET content=NULL")
                db.commit()
        except BaseException as error:
            errors.append(error)

    def trace(sql):
        nonlocal triggered
        if "SELECT * FROM settlements" in sql and not triggered:
            triggered = True
            thread = threading.Thread(target=expire)
            thread.start()
            thread.join(timeout=5)
            assert not thread.is_alive()

    @contextmanager
    def interleaved(**kwargs):
        with original(**kwargs) as db:
            db.set_trace_callback(trace)
            yield db

    monkeypatch.setattr(store.db, "connect", interleaved)
    snapshot = store.report(purchase, NOW)
    assert triggered and not errors
    assert snapshot["delivery_state"] == "cached"
    assert store.report(purchase, NOW)["delivery_state"] == "gone"


def test_initialization_failure_is_atomic_and_never_implicitly_repaired(
    shop, monkeypatch, tmp_path
):
    from agentic_payments_lab import x402_merchant_store

    original = x402_merchant_store._ddl

    def fail(db, schema):
        original(db, schema)
        raise sqlite3.OperationalError("interrupted initialization")

    monkeypatch.setattr(x402_merchant_store, "_ddl", fail)
    target = tmp_path / "incomplete.sqlite3"
    with pytest.raises(sqlite3.OperationalError):
        MerchantStore.initialize(target, shop.buyer.policy, "a" * 64)
    with sqlite3.connect(target) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM sqlite_master").fetchone()[0] == 0
    before = target.read_bytes()
    with pytest.raises(SchemaError):
        MerchantStore(target, shop.buyer.policy, "a" * 64)
    assert target.read_bytes() == before


def worker(shop, operation, *args):
    return subprocess.Popen(
        [
            sys.executable,
            str(Path(__file__).with_name("x402_merchant_worker.py")),
            str(shop.merchant.store.db.path.parent),
            operation,
            *args,
        ],
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


@pytest.mark.parametrize(
    ("boundary", "calls", "transfers", "delivery"),
    [
        ("before_claim", 0, 0, "not_ready"),
        ("after_claim", 0, 0, "not_ready"),
        ("settle_entry", 1, 0, "not_ready"),
        ("after_transfer", 1, 1, "not_ready"),
        ("before_observe", 1, 1, "not_ready"),
        ("after_observe", 1, 1, "pending"),
        ("before_cache", 1, 1, "pending"),
        ("after_cache", 1, 1, "cached"),
    ],
)
def test_abrupt_exit_at_merchant_boundaries_never_resends_on_restart(
    shop, boundary, calls, transfers, delivery
):
    process = worker(shop, boundary)
    stdout, stderr = process.communicate(timeout=20)
    assert process.returncode == 73, (stdout, stderr)
    assert shop.authority.counts() == (calls, transfers, transfers * 10_000)
    store = MerchantStore(
        shop.merchant.store.db.path,
        shop.buyer.policy,
        digest(required_data(shop.buyer.policy, identified=True)),
    )
    status = store.report(shop.context().purchase_id, NOW)
    assert status["delivery_state"] == delivery
    assert status["settlement_may_have_occurred"] is (boundary != "before_claim")
    # New gate instance, no surviving signature, existing buyer identity and claim.
    asyncio.run(shop.run())
    assert shop.signer.calls == []
    assert shop.authority.counts() == (calls, transfers, transfers * 10_000)
    assert shop.buyer.report("task")["budget"] == {"budget": 30_000, "reserved": 10_000, "spent": 0}
    if delivery in ("pending", "cached"):
        store.prepare_delivery(shop.context().purchase_id, NOW)
        assert store.report(shop.context().purchase_id, NOW)["delivery_state"] == "cached"
        assert shop.authority.counts() == (calls, transfers, transfers * 10_000)


def test_separate_process_http_replays_have_one_settle_owner(shop):
    shop.payment()  # Prepare a public authorization fixture, not a retained signature.
    root = shop.merchant.store.db.path.parent
    processes = [worker(shop, "race", str(number)) for number in range(4)]
    try:
        deadline = time.monotonic() + 15
        while len(list(root.glob("ready-*"))) != len(processes) and time.monotonic() < deadline:
            time.sleep(0.02)
        assert len(list(root.glob("ready-*"))) == len(processes)
        (root / "go").touch()
        for process in processes:
            stdout, stderr = process.communicate(timeout=15)
            assert process.returncode == 0, (stdout, stderr)
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.communicate()
    assert shop.authority.counts() == (1, 1, 10_000)
    assert shop.merchant.store.report(shop.context().purchase_id, NOW)["delivery_state"] == "cached"


def test_cache_expiry_cannot_clear_nonce_or_bind_a_replacement_purchase(shop):
    from test_x402_merchant import encode, wire

    purchase, header, terms = shop.payment()
    shop.send(purchase, header)
    shop.merchant.store.expire_cache(NOW + CACHE_SECONDS)
    shop.enroll("other")
    other_purchase, other_header, _ = shop.payment("other")
    data = wire(other_header)
    data["payload"]["authorization"]["nonce"] = terms.nonce
    assert shop.send(other_purchase, encode(data)).status_code == 409
    assert shop.authority.counts() == (1, 1, 10_000)
