import asyncio
import json
import sqlite3
import subprocess
import sys
import threading
import time
import traceback
import warnings
from contextlib import contextmanager
from pathlib import Path

import pytest
from evidence_support import (
    evidence_data,
    paid,
    reconcile,
    reconciler,
    source_for,
    terminal_events,
    track_dispatches,
)
from merchant_support import scenario
from test_x402_gate import MARKER, NOW, evidence_for, record

from agentic_payments_lab.buyer_schema import SchemaError
from agentic_payments_lab.domain import digest
from agentic_payments_lab.ledger import Ledger
from agentic_payments_lab.storage import Database
from agentic_payments_lab.x402_adapter.protocol import OfflineSignature
from agentic_payments_lab.x402_contracts import GateDenied
from agentic_payments_lab.x402_evidence import Observation
from agentic_payments_lab.x402_reconciliation import ReconciliationUnavailable
from agentic_payments_lab.x402_store import OfflineGateStore

WORKER = Path(__file__).with_name("x402_evidence_worker.py")


@pytest.fixture
def shop(tmp_path, monkeypatch):
    result = scenario(tmp_path)
    result.dispatch_entries = track_dispatches(monkeypatch)
    paid(result)
    return result


@pytest.mark.parametrize(
    "boundary",
    [
        "before_record",
        "after_observation",
        "after_accounting",
        "before_commit",
        "after_commit",
        "report_lost",
    ],
)
def test_process_death_reopens_original_identity_with_atomic_accounting(shop, boundary):
    before = record(shop.buyer)
    process = subprocess.run(
        [sys.executable, str(WORKER), str(shop.buyer.db.path), boundary],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert process.returncode == 96, process.stderr
    assert not shop.buyer.db.path.with_name("forbidden-execution.txt").exists()
    committed = boundary in ("after_commit", "report_lost")
    reopened = OfflineGateStore(shop.buyer.db.path, shop.buyer.policy)
    report = reopened.report("task")
    assert report["budget"]["spent"] == (10_000 if committed else 0)
    assert report["budget"]["reserved"] == (0 if committed else 10_000)
    assert report["reconciliation"]["observation_count"] == int(committed)
    assert terminal_events(reopened) == int(committed)
    assert record(reopened)["nonce"] == before["nonce"]
    assert record(reopened)["authorization_id"] == before["authorization_id"]
    assert shop.authority.counts() == (1, 1, 10_000)
    asyncio.run(shop.run())
    assert len(shop.signer.calls) == 1  # Reopen/replay never reacquires the signer.
    assert shop.dispatch_entries == ["dispatch"]
    reconcile(reopened)
    reconcile(reopened)
    assert terminal_events(reopened) == 1
    assert reopened.report("task")["reconciliation"]["observation_count"] == 1
    assert shop.authority.counts() == (1, 1, 10_000)


@pytest.mark.parametrize(
    "failure", ["observation", "budget", "terminal", "event", "commit", "lost_ack"]
)
def test_persistence_failure_or_lost_ack_is_atomic_and_explicitly_recoverable(
    shop, monkeypatch, failure
):
    original = shop.buyer.db.connect

    @contextmanager
    def connection(**kwargs):
        with original(**kwargs) as db:

            def authorize(action, a, b, *_):
                if action == sqlite3.SQLITE_INSERT and a == "events":
                    if failure == "observation":
                        return sqlite3.SQLITE_DENY
                if (
                    (failure == "budget" and action == sqlite3.SQLITE_UPDATE and a == "grants")
                    or (
                        failure == "terminal"
                        and action == sqlite3.SQLITE_UPDATE
                        and a == "x402_attempts"
                    )
                    or (
                        failure == "commit"
                        and action == sqlite3.SQLITE_TRANSACTION
                        and a == "COMMIT"
                    )
                ):
                    return sqlite3.SQLITE_DENY
                return sqlite3.SQLITE_OK

            db.set_authorizer(authorize)
            yield db

    if failure == "event":
        original_event = Ledger._event

        def event(db, purchase_id, event_type, data, now):
            # SQLite's authorizer runs at statement preparation; its cache can
            # hide the second identical INSERT. Fault at the actual event call.
            if event_type == "offline_fixture_applied":
                raise sqlite3.OperationalError("synthetic terminal audit failure")
            return original_event(db, purchase_id, event_type, data, now)

        monkeypatch.setattr(Ledger, "_event", event)
    elif failure == "lost_ack":
        original_tx = shop.buyer.db.transaction

        @contextmanager
        def lost_ack():
            with original_tx() as db:
                yield db
            raise sqlite3.OperationalError(MARKER)

        monkeypatch.setattr(shop.buyer.db, "transaction", lost_ack)
    else:
        monkeypatch.setattr(shop.buyer.db, "connect", connection)
    with pytest.raises(ReconciliationUnavailable):
        reconcile(shop.buyer)
    monkeypatch.undo()
    report = shop.buyer.report("task")
    committed = failure == "lost_ack"
    assert report["budget"]["spent"] == 10_000 * committed
    assert report["budget"]["reserved"] == 10_000 * (not committed)
    assert terminal_events(shop.buyer) == int(committed)
    assert report["reconciliation"]["observation_count"] == int(committed)
    assert shop.authority.counts() == (1, 1, 10_000)
    reconcile(shop.buyer)
    assert terminal_events(shop.buyer) == 1


def test_separate_process_reconciliation_owns_one_accounting_transition(shop):
    children = [
        subprocess.Popen(
            [sys.executable, str(WORKER), str(shop.buyer.db.path), "concurrent"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for _ in range(4)
    ]
    try:
        deadline = time.monotonic() + 10
        while len(list(shop.buyer.db.path.parent.glob("ready-*"))) < 4:
            assert time.monotonic() < deadline, "Observers did not reach the barrier"
            time.sleep(0.01)
        # All four have read evidence and are about to apply it. No observer
        # holds a write lock across that await/observation boundary.
        with shop.buyer.db.transaction() as db:
            assert db.execute("SELECT reserved FROM grants").fetchone()[0] == 10_000
    finally:
        shop.buyer.db.path.with_name("release-observers").touch()
    results = [(child, child.communicate(timeout=20)) for child in children]
    assert all(child.returncode == 0 for child, _ in results), results
    assert not shop.buyer.db.path.with_name("forbidden-execution.txt").exists()
    assert terminal_events(shop.buyer) == 1
    assert shop.buyer.report("task")["reconciliation"]["observation_count"] == 1
    assert shop.buyer.report("task")["budget"]["spent"] == 10_000
    assert shop.authority.counts() == (1, 1, 10_000)
    assert shop.dispatch_entries == ["dispatch"]


def test_report_uses_same_snapshot_for_accounting_observations_and_incident(shop, monkeypatch):
    before = shop.buyer.report("task")
    observation = asyncio.run(
        reconciler(shop.buyer).observer.observe(shop.buyer.reconciliation_terms("task"))
    )
    at_events, written = threading.Event(), threading.Event()
    original = shop.buyer.db.connect
    reader = threading.get_ident()
    errors = []

    @contextmanager
    def connection(**kwargs):
        with original(**kwargs) as db:

            def trace(sql):
                if threading.get_ident() == reader and sql.startswith("SELECT * FROM events"):
                    at_events.set()
                    assert written.wait(5)

            db.set_trace_callback(trace)
            yield db

    def writer():
        try:
            assert at_events.wait(5)
            shop.buyer.record_observation("task", observation, NOW + 100)
        except Exception as error:
            errors.append(error)
        finally:
            written.set()

    monkeypatch.setattr(shop.buyer.db, "connect", connection)
    thread = threading.Thread(target=writer)
    thread.start()
    report = shop.buyer.report("task")
    thread.join(5)
    assert not thread.is_alive() and not errors
    assert report == before
    monkeypatch.undo()
    assert shop.buyer.report("task")["phase"] == "fixture_settled"


@pytest.mark.parametrize("fail_cleanup", [False, True])
def test_cancellation_before_first_response_and_cleanup_failure(shop, monkeypatch, fail_cleanup):
    source = source_for(evidence_data(shop.buyer.reconciliation_terms("task")))
    original = source.read
    runner = reconciler(shop.buyer, source=source)

    async def run():
        entered = asyncio.Event()

        async def blocked(method, **selectors):
            await original(method, **selectors)
            entered.set()
            await asyncio.Event().wait()

        source.read = blocked
        task = asyncio.create_task(runner.reconcile("task"))
        await asyncio.wait_for(entered.wait(), 5)
        if fail_cleanup:

            def fail(*args):
                raise OSError(MARKER)

            monkeypatch.setattr(shop.buyer, "record_observation", fail)
        task.cancel(MARKER)
        with pytest.raises(asyncio.CancelledError) as error:
            await task
        assert MARKER not in "".join(traceback.format_exception(error.value))

    asyncio.run(run())
    report = shop.buyer.report("task")
    assert report["budget"]["reserved"] == 10_000 and report["budget"]["spent"] == 0
    assert report["reconciliation"]["observation_count"] == (0 if fail_cleanup else 1)
    assert len(source.calls) == 1
    if not fail_cleanup:
        assert report["reconciliation"]["last_observation"]["reason"] == "canceled"
        records = next(
            e["data"]["records"]
            for e in report["events"]
            if e["event_type"] == "offline_reconciliation_observed"
        )
        assert records["profile"] is None and records["reads_attempted"] == 1
    assert terminal_events(shop.buyer) == 0


def test_expired_nonuse_reconciliation_fences_a_late_signer(tmp_path):
    shop = scenario(tmp_path)

    async def run():
        entered, release = asyncio.Event(), asyncio.Event()

        async def sign(terms):
            shop.signer.calls.append(terms)
            entered.set()
            await release.wait()
            return OfflineSignature(MARKER)

        shop.signer.sign = sign
        executing = asyncio.create_task(shop.run())
        await asyncio.wait_for(entered.wait(), 5)
        await reconciler(shop.buyer, unused=True).reconcile("task")
        release.set()
        await executing

    asyncio.run(run())
    assert shop.authority.counts() == (0, 0, 0)
    assert len(shop.signer.calls) == 1
    assert shop.buyer.report("task")["phase"] == "fixture_unused"
    assert record(shop.buyer)["dispatch_authorized"] == 0
    assert terminal_events(shop.buyer) == 1


def test_incident_fences_an_already_claimed_other_purchase_and_legacy_fixture(shop):
    shop.enroll("other")
    owner, terms = shop.buyer.claim("other", digest({}), NOW)
    reconcile(shop.buyer)
    reconcile(shop.buyer, unused=True)
    with pytest.raises(GateDenied, match="operator_review_required"):
        shop.buyer.authorize_dispatch(owner, terms, NOW)
    with pytest.raises(GateDenied, match="operator_review_required"):
        shop.buyer.apply_fixture("task", evidence_for(shop.buyer), NOW + 100)
    assert shop.buyer.report("other")["budget"]["reserved"] == 10_000
    assert shop.authority.counts() == (1, 1, 10_000)


def test_reordered_success_after_nonuse_is_incident_not_double_accounting(tmp_path):
    shop = scenario(tmp_path)
    shop.payment()
    reconcile(shop.buyer, unused=True)
    report, _ = reconcile(shop.buyer)
    assert report["phase"] == "fixture_unused"
    assert report["reconciliation"]["operator_incident"]
    assert report["budget"]["reserved"] == report["budget"]["spent"] == 0
    assert terminal_events(shop.buyer) == 1


def test_version3_reopen_preserves_all_existing_tables_and_history(shop):
    path = shop.buyer.db.path
    with sqlite3.connect(path) as db:
        before = list(db.iterdump())
        assert db.execute("PRAGMA user_version").fetchone()[0] == 4
    reopened = OfflineGateStore(path, shop.buyer.policy)
    with sqlite3.connect(path) as db:
        assert list(db.iterdump()) == before
    reconcile(reopened)
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 4
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
    with shop.merchant.store.db.connect() as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 1


def test_missing_or_future_state_is_not_initialized_for_reconciliation(shop):
    path = shop.buyer.db.path.parent / "missing.sqlite3"
    with pytest.raises(SchemaError):
        OfflineGateStore(path, shop.buyer.policy)
    assert not path.exists()
    # New disposable copy; never mutate retained/operator state for this probe.
    with sqlite3.connect(shop.buyer.db.path) as source, sqlite3.connect(path) as target:
        source.backup(target)
        target.execute("PRAGMA journal_mode=DELETE")
        target.execute("PRAGMA user_version=99")
    before = path.read_bytes()
    with pytest.raises(SchemaError):
        OfflineGateStore(path, shop.buyer.policy)
    assert path.read_bytes() == before
    assert not path.with_name(path.name + "-wal").exists()


def test_forged_model_serialization_does_not_log_signature_markers(shop, caplog):
    runner = reconciler(shop.buyer)
    observation = asyncio.run(runner.observer.observe(shop.buyer.reconciliation_terms("task")))
    data = observation.model_dump(mode="json")
    data["receipt"]["success"] = MARKER
    forged = Observation.model_construct(**data)
    with warnings.catch_warnings(record=True) as caught, pytest.raises(GateDenied):
        shop.buyer.record_observation("task", forged, NOW + 100)
    assert not caught
    assert MARKER not in caplog.text + json.dumps(shop.buyer.report("task"))


def test_expired_or_revoked_grant_does_not_prevent_read_only_reconciliation(shop):
    with shop.buyer.db.transaction() as db:
        db.execute("UPDATE grants SET revoked=1")
    runner = reconciler(shop.buyer)
    runner.clock = lambda: NOW + 1000
    report = asyncio.run(runner.reconcile("task"))
    assert report["phase"] == "fixture_settled"
    assert shop.authority.counts() == (1, 1, 10_000)


def test_cancellation_after_accounting_commit_preserves_outcome(shop, monkeypatch):
    original = shop.buyer.record_observation

    def cancel_after_commit(*args):
        original(*args)
        asyncio.current_task().cancel(MARKER)

    monkeypatch.setattr(shop.buyer, "record_observation", cancel_after_commit)

    async def run():
        task = asyncio.create_task(reconciler(shop.buyer).reconcile("task"))
        with pytest.raises(asyncio.CancelledError) as error:
            await task
        assert MARKER not in "".join(traceback.format_exception(error.value))

    asyncio.run(run())
    report = shop.buyer.report("task")
    assert report["phase"] == "fixture_settled"
    assert report["budget"]["spent"] == 10_000 and report["budget"]["reserved"] == 0
    assert terminal_events(shop.buyer) == 1
    assert shop.authority.counts() == (1, 1, 10_000)


def load_version2(path):
    fixture = Path(__file__).parent / "fixtures" / "phase3b2_v2_ledger.sql"
    with sqlite3.connect(path) as db:
        db.executescript(fixture.read_text())
        assert db.execute("PRAGMA user_version").fetchone()[0] == 2
        return list(db.iterdump())


def test_version2_upgrade_preserves_exact_historical_rows_and_bindings(tmp_path):
    from test_x402_gate import policy_for_test

    path = tmp_path / "buyer.sqlite3"
    before = load_version2(path)
    store = OfflineGateStore(path, policy_for_test())
    with sqlite3.connect(path) as db:
        assert list(db.iterdump()) == before  # All tables, identities and JSON are unchanged.
        assert db.execute("PRAGMA user_version").fetchone()[0] == 4
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
    assert store.report("task")["phase"] == "fixture_settled"
    assert store.report("task")["budget"] == {"budget": 30_000, "reserved": 10_000, "spent": 10_000}
    assert store.report("still-reserved")["phase"] == "reconciliation_required"


@pytest.mark.parametrize("failure", ["version", "commit"])
def test_version2_upgrade_failure_rolls_back_marker_and_history(tmp_path, monkeypatch, failure):
    from test_x402_gate import policy_for_test

    path = tmp_path / "buyer.sqlite3"
    before = load_version2(path)
    original = Database.connect

    @contextmanager
    def connection(self, **kwargs):
        with original(self, **kwargs) as db:

            def deny(action, a, b, *_):
                if (
                    failure == "version"
                    and action == sqlite3.SQLITE_PRAGMA
                    and a == "user_version"
                    and b == "4"
                ) or (
                    failure == "commit" and action == sqlite3.SQLITE_TRANSACTION and a == "COMMIT"
                ):
                    return sqlite3.SQLITE_DENY
                return sqlite3.SQLITE_OK

            db.set_authorizer(deny)
            yield db

    monkeypatch.setattr(Database, "connect", connection)
    with pytest.raises(sqlite3.DatabaseError):
        OfflineGateStore(path, policy_for_test())
    monkeypatch.undo()
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 2
        assert list(db.iterdump()) == before
    OfflineGateStore(path, policy_for_test())


@pytest.mark.parametrize(
    "boundary,version", [("migrate_before_commit", 2), ("migrate_after_commit", 4)]
)
def test_interrupted_version2_upgrade_then_explicit_reopen(tmp_path, boundary, version):
    from test_x402_gate import policy_for_test

    path = tmp_path / "buyer.sqlite3"
    before = load_version2(path)
    child = subprocess.run(
        [sys.executable, str(WORKER), str(path), boundary], capture_output=True, timeout=15
    )
    assert child.returncode == 96, child.stderr
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == version
        assert list(db.iterdump()) == before
    OfflineGateStore(path, policy_for_test())
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 4
        assert list(db.iterdump()) == before


def test_concurrent_version2_openers_apply_one_upgrade_without_history_changes(tmp_path):
    path = tmp_path / "buyer.sqlite3"
    before = load_version2(path)
    children = [
        subprocess.Popen(
            [sys.executable, str(WORKER), str(path), "migrate_only"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        for _ in range(4)
    ]
    results = [(child, child.communicate(timeout=20)) for child in children]
    assert all(child.returncode == 0 for child, _ in results), results
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 4
        assert list(db.iterdump()) == before
