import asyncio
import json
import sqlite3
import subprocess
import sys
import threading
import time
import traceback
from contextlib import contextmanager
from pathlib import Path

import pytest
from delivery_support import (
    accept,
    acceptance_rows,
    acceptor,
    assert_no_new_execution,
    financial_rows,
    merchant_rows,
    settled_shop,
    write_evidence,
)
from evidence_support import reconcile, reconciler
from test_x402_gate import MARKER, NOW, policy_for_test

from agentic_payments_lab.buyer_schema import SchemaError
from agentic_payments_lab.ledger import Ledger
from agentic_payments_lab.storage import Database
from agentic_payments_lab.x402_contracts import GateDenied
from agentic_payments_lab.x402_delivery import DeliveryUnavailable
from agentic_payments_lab.x402_delivery_contracts import DeliveryLimits
from agentic_payments_lab.x402_merchant_store import CACHE_SECONDS
from agentic_payments_lab.x402_provider import OfflineMerchantClient
from agentic_payments_lab.x402_store import OfflineGateStore

WORKER = Path(__file__).with_name("x402_delivery_worker.py")


@pytest.fixture
def shop(tmp_path, monkeypatch):
    return settled_shop(tmp_path, monkeypatch)


@pytest.mark.parametrize(
    "boundary",
    [
        "before_retrieval",
        "after_response",
        "before_record",
        "after_insert",
        "before_commit",
        "after_commit",
        "report_lost",
    ],
)
def test_process_death_preserves_finance_and_reopen_accepts_without_new_payment(shop, boundary):
    finance, merchant = financial_rows(shop.buyer), merchant_rows(shop)
    child = subprocess.run(
        [sys.executable, str(WORKER), str(shop.buyer.db.path), boundary],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert child.returncode == 96, child.stderr
    assert not list(shop.buyer.db.path.parent.glob("forbidden-*"))
    committed = boundary in ("after_commit", "report_lost")
    reopened = OfflineGateStore(shop.buyer.db.path, shop.buyer.policy)
    assert len(acceptance_rows(reopened)) == int(committed)
    assert reopened.report("task")["delivery_state"] == (
        "delivered" if committed else "not_requested"
    )
    assert financial_rows(reopened) == finance
    result = accept(shop, store=reopened)
    assert result["current_invocation"]["retrieval_attempts"] == (0 if committed else 1)
    assert len(acceptance_rows(reopened)) == 1
    assert accept(shop, store=reopened)["current_invocation"]["retrieval_attempts"] == 0
    assert financial_rows(reopened) == finance
    assert merchant_rows(shop) == merchant
    assert_no_new_execution(shop)


@pytest.mark.parametrize("failure", ["insert", "after_insert", "commit", "lost_ack", "report"])
def test_write_failure_and_lost_ack_never_split_delivery_from_audit(shop, monkeypatch, failure):
    finance = financial_rows(shop.buyer)
    with monkeypatch.context() as patch:
        if failure in ("insert", "commit"):
            original = shop.buyer.db.connect

            @contextmanager
            def connection(**kwargs):
                with original(**kwargs) as db:

                    def deny(action, a, b, *_):
                        if (
                            failure == "insert"
                            and action == sqlite3.SQLITE_INSERT
                            and a == "events"
                        ) or (
                            failure == "commit"
                            and action == sqlite3.SQLITE_TRANSACTION
                            and a == "COMMIT"
                        ):
                            return sqlite3.SQLITE_DENY
                        return sqlite3.SQLITE_OK

                    db.set_authorizer(deny)
                    yield db

            patch.setattr(shop.buyer.db, "connect", connection)
        elif failure == "after_insert":
            original_event = Ledger._event

            def event(db, purchase_id, event_type, data, now):
                original_event(db, purchase_id, event_type, data, now)
                if event_type == "offline_delivery_accepted":
                    raise sqlite3.OperationalError(MARKER)

            patch.setattr(Ledger, "_event", event)
        elif failure == "lost_ack":
            original_tx = shop.buyer.db.transaction

            @contextmanager
            def transaction():
                with original_tx() as db:
                    yield db
                raise sqlite3.OperationalError(MARKER)

            patch.setattr(shop.buyer.db, "transaction", transaction)
        else:
            patch.setattr(
                shop.buyer, "report", lambda _: (_ for _ in ()).throw(RuntimeError(MARKER))
            )
        with pytest.raises(DeliveryUnavailable) as error:
            accept(shop)
        assert MARKER not in "".join(traceback.format_exception(error.value))
    committed = failure in ("lost_ack", "report")
    assert len(acceptance_rows(shop.buyer)) == int(committed)
    assert financial_rows(shop.buyer) == finance
    assert accept(shop)["current_invocation"]["retrieval_attempts"] == (0 if committed else 1)
    assert len(acceptance_rows(shop.buyer)) == 1
    assert_no_new_execution(shop)


def run_workers(path, boundary):
    children = [
        subprocess.Popen(
            [sys.executable, str(WORKER), str(path), boundary],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for _ in range(4)
    ]
    try:
        deadline = time.monotonic() + 12
        while len(list(path.parent.glob("ready-*"))) != 4:
            assert time.monotonic() < deadline, "All workers must reach the overlap barrier"
            assert all(child.poll() is None for child in children), "Worker failed before barrier"
            time.sleep(0.01)
        with sqlite3.connect(path, timeout=0.2) as db:
            db.execute("BEGIN IMMEDIATE")
            assert db.execute("SELECT spent,reserved FROM grants").fetchone() == (
                10_000,
                10_000 if boundary.startswith("migrate") else 0,
            )
        path.with_name("release-workers").touch()
        results = [(child, child.communicate(timeout=20)) for child in children]
        assert all(child.returncode == 0 for child, _ in results), results
    finally:
        path.with_name("release-workers").touch()
        for child in children:
            if child.poll() is None:
                child.terminate()
            child.communicate(timeout=5)


def test_separate_processes_accept_one_delivery_after_overlapping_retrievals(shop):
    finance, merchant = financial_rows(shop.buyer), merchant_rows(shop)
    run_workers(shop.buyer.db.path, "concurrent")
    assert not list(shop.buyer.db.path.parent.glob("forbidden-*"))
    assert len(shop.buyer.db.path.with_name("retrieval-calls").read_text().splitlines()) == 4
    assert len(acceptance_rows(shop.buyer)) == 1
    assert shop.buyer.report("task")["delivery_state"] == "delivered"
    assert financial_rows(shop.buyer) == finance
    assert merchant_rows(shop) == merchant
    assert_no_new_execution(shop)


@pytest.mark.parametrize(
    "boundary", ["before_response", "after_response", "late_response", "after_commit"]
)
def test_cancellation_is_sanitized_and_repeated_invocation_preserves_original_purchase(
    shop, monkeypatch, boundary, caplog
):
    finance = financial_rows(shop.buyer)
    method = OfflineMerchantClient.retrieve

    async def run():
        entered = asyncio.Event()

        async def retrieve(self, purchase_id):
            if boundary == "after_response":
                result = await method(self, purchase_id)
                asyncio.current_task().cancel(MARKER)
                return result
            if boundary in ("before_response", "late_response"):
                entered.set()
                try:
                    await asyncio.Future()
                except asyncio.CancelledError:
                    if boundary != "late_response":
                        raise
                return await method(self, purchase_id)
            return await method(self, purchase_id)

        with monkeypatch.context() as patch:
            patch.setattr(OfflineMerchantClient, "retrieve", retrieve)
            if boundary == "after_commit":
                original = shop.buyer.record_delivery

                def record(*args):
                    result = original(*args)
                    asyncio.current_task().cancel(MARKER)
                    return result

                patch.setattr(shop.buyer, "record_delivery", record)
            task = asyncio.create_task(acceptor(shop).accept("task"))
            if boundary in ("before_response", "late_response"):
                await asyncio.wait_for(entered.wait(), 3)
                task.cancel(MARKER)
            with pytest.raises(asyncio.CancelledError) as error:
                await task
            assert MARKER not in "".join(traceback.format_exception(error.value))

    asyncio.run(run())
    committed = boundary == "after_commit"
    assert len(acceptance_rows(shop.buyer)) == int(committed)
    assert financial_rows(shop.buyer) == finance
    reopened = OfflineGateStore(shop.buyer.db.path, shop.buyer.policy)
    assert accept(shop, store=reopened)["current_invocation"]["retrieval_attempts"] == (
        0 if committed else 1
    )
    assert len(acceptance_rows(reopened)) == 1
    assert MARKER not in caplog.text + json.dumps(reopened.report("task"))
    for path in shop.buyer.db.path.parent.iterdir():
        if path.is_file():
            assert MARKER.encode() not in path.read_bytes()
    assert_no_new_execution(shop)


@pytest.mark.parametrize("late", [False, True])
def test_cooperative_timeout_records_failure_and_rejects_late_result(shop, monkeypatch, late):
    method = OfflineMerchantClient.retrieve

    async def retrieve(self, purchase_id):
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            if not late:
                raise
        return await method(self, purchase_id)

    monkeypatch.setattr(OfflineMerchantClient, "retrieve", retrieve)
    report = accept(shop, limits=DeliveryLimits(timeout_seconds=1))
    assert report["delivery_state"] == "failed"
    assert report["current_invocation"] == {
        "outcome": "failed",
        "reason": "timeout",
        "retrieval_attempts": 1,
    }
    assert not acceptance_rows(shop.buyer)
    assert report["budget"]["spent"] == 10_000 and report["budget"]["reserved"] == 0
    assert_no_new_execution(shop)


def test_incident_before_acceptance_rejects_before_any_retrieval(shop, monkeypatch):
    reconcile(shop.buyer, unused=True)
    calls = []

    async def retrieve(*args):
        calls.append(True)
        raise AssertionError("Incident must fence retrieval")

    monkeypatch.setattr(OfflineMerchantClient, "retrieve", retrieve)
    finance = financial_rows(shop.buyer)
    with pytest.raises(GateDenied):
        accept(shop)
    assert not calls and not acceptance_rows(shop.buyer)
    assert financial_rows(shop.buyer) == finance
    assert_no_new_execution(shop)


def test_incident_committed_by_another_connection_during_retrieval_wins(shop, monkeypatch):
    method = OfflineMerchantClient.retrieve
    finance = financial_rows(shop.buyer)

    async def run():
        fetched, release = asyncio.Event(), asyncio.Event()

        async def retrieve(self, purchase_id):
            report = await method(self, purchase_id)
            fetched.set()
            await release.wait()
            return report

        monkeypatch.setattr(OfflineMerchantClient, "retrieve", retrieve)
        task = asyncio.create_task(acceptor(shop).accept("task"))
        await asyncio.wait_for(fetched.wait(), 3)

        def incident():
            other = OfflineGateStore(shop.buyer.db.path, shop.buyer.policy)
            reconcile(other, unused=True)

        try:
            await asyncio.to_thread(incident)
        finally:
            release.set()
        with pytest.raises(GateDenied):
            await task

    asyncio.run(run())
    assert not acceptance_rows(shop.buyer)
    assert shop.buyer.report("task")["reconciliation"]["operator_incident"]
    assert financial_rows(shop.buyer) == finance
    assert_no_new_execution(shop)


def test_acceptance_commits_before_racing_incident_and_history_remains(shop, monkeypatch):
    method = shop.buyer.report
    incident = reconciler(shop.buyer, unused=True)

    def report(task_id):
        errors = []

        def run():
            try:
                asyncio.run(incident.reconcile(task_id))
            except BaseException as exc:
                errors.append(exc)

        # Another connection records the incident after acceptance commit, before snapshot.
        worker = threading.Thread(target=run)
        worker.start()
        worker.join(5)
        assert not worker.is_alive() and not errors
        return method(task_id)

    # The incident runner uses a separate store so its report cannot recurse into this hook.
    incident.store = OfflineGateStore(shop.buyer.db.path, shop.buyer.policy)
    with monkeypatch.context() as patch:
        patch.setattr(shop.buyer, "report", report)
        result = accept(shop)
    assert result["current_invocation"]["outcome"] == "accepted"
    assert result["delivery_state"] == "delivered"
    assert result["reconciliation"]["operator_incident"]
    assert accept(shop)["current_invocation"]["retrieval_attempts"] == 0
    assert len(acceptance_rows(shop.buyer)) == 1
    assert_no_new_execution(shop)


def test_report_uses_one_snapshot_when_acceptance_commits_between_reads(shop, monkeypatch):
    at_events, committed = threading.Event(), threading.Event()
    original = shop.buyer.db.connect
    reader = threading.get_ident()
    errors = []

    @contextmanager
    def connection(**kwargs):
        with original(**kwargs) as db:

            def trace(sql):
                if threading.get_ident() == reader and sql.startswith("SELECT * FROM events"):
                    at_events.set()
                    assert committed.wait(5)

            db.set_trace_callback(trace)
            yield db

    def writer():
        try:
            assert at_events.wait(5)
            accept(shop, store=OfflineGateStore(shop.buyer.db.path, shop.buyer.policy))
        except BaseException as exc:
            errors.append(exc)
        finally:
            committed.set()

    with monkeypatch.context() as patch:
        patch.setattr(shop.buyer.db, "connect", connection)
        thread = threading.Thread(target=writer)
        thread.start()
        before = shop.buyer.report("task")
        thread.join(6)
    assert not thread.is_alive() and not errors
    assert before["delivery_state"] == "not_requested" and before["delivery"]["acceptance"] is None
    assert not any(e["event_type"] == "offline_delivery_accepted" for e in before["events"])
    assert shop.buyer.report("task")["delivery_state"] == "delivered"
    assert len(acceptance_rows(shop.buyer)) == 1
    assert_no_new_execution(shop)


def test_reopened_failure_replay_distinguishes_current_result_from_last_distinct_failure(
    shop, monkeypatch, tmp_path
):
    shop.now[0] = NOW + CACHE_SECONDS
    first = accept(shop)
    assert first["current_invocation"]["reason"] == "gone"

    async def timeout(*args):
        raise TimeoutError(MARKER)

    with monkeypatch.context() as patch:
        patch.setattr(OfflineMerchantClient, "retrieve", timeout)
        assert accept(shop)["current_invocation"]["reason"] == "timeout"
    reopened = OfflineGateStore(shop.buyer.db.path, shop.buyer.policy)
    before = reopened.report("task")
    final = accept(shop, store=reopened)
    assert final["current_invocation"] == {
        "outcome": "failed",
        "reason": "gone",
        "retrieval_attempts": 1,
    }
    assert final["delivery"]["last_failure"]["reason"] == "timeout"
    assert reopened.report("task") == before
    assert len([e for e in final["events"] if e["event_type"] == "offline_delivery_failed"]) == 2
    assert not acceptance_rows(reopened)
    assert_no_new_execution(shop)
    write_evidence(tmp_path / "failure-replay-evidence.json", shop, final)


def load_version3(path):
    with sqlite3.connect(path) as db:
        db.executescript(
            (Path(__file__).parent / "fixtures" / "phase3b3_v3_ledger.sql").read_text()
        )
        assert db.execute("PRAGMA user_version").fetchone()[0] == 3
        return list(db.iterdump())


def test_version3_upgrade_preserves_every_row_evidence_incident_and_binding(tmp_path):
    path = tmp_path / "buyer.sqlite3"
    before = load_version3(path)
    store = OfflineGateStore(path, policy_for_test())
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 4
        assert list(db.iterdump()) == before
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
    report = store.report("task")
    assert report["budget"] == {"budget": 30_000, "reserved": 10_000, "spent": 10_000}
    assert (
        report["reconciliation"]["operator_incident"]
        and report["reconciliation"]["observation_count"] == 2
    )
    assert store.report("still-reserved")["phase"] == "signing_claimed"
    assert store.report("canceled-before-claim")["phase"] == "canceled_before_claim"
    with pytest.raises(GateDenied):
        store.delivery_request("task")


@pytest.mark.parametrize("failure", ["version", "commit"])
def test_failed_version3_upgrade_preserves_original_marker_and_history(
    tmp_path, monkeypatch, failure
):
    path = tmp_path / "buyer.sqlite3"
    before = load_version3(path)
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

    with monkeypatch.context() as patch:
        patch.setattr(Database, "connect", connection)
        with pytest.raises(sqlite3.DatabaseError):
            OfflineGateStore(path, policy_for_test())
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 3
        assert list(db.iterdump()) == before
    OfflineGateStore(path, policy_for_test())


@pytest.mark.parametrize(
    "boundary,version", [("migrate_before_commit", 3), ("migrate_after_commit", 4)]
)
def test_version3_upgrade_process_exit_then_reopen_keeps_history(tmp_path, boundary, version):
    path = tmp_path / "buyer.sqlite3"
    before = load_version3(path)
    child = subprocess.run(
        [sys.executable, str(WORKER), str(path), boundary],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert child.returncode == 96, child.stderr
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == version
        assert list(db.iterdump()) == before
    OfflineGateStore(path, policy_for_test())
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 4
        assert list(db.iterdump()) == before


def test_concurrent_version3_openers_keep_all_original_records(tmp_path):
    path = tmp_path / "buyer.sqlite3"
    before = load_version3(path)
    run_workers(path, "migrate_concurrent")
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 4
        assert list(db.iterdump()) == before


def test_missing_or_unsupported_history_never_initializes_during_delivery_reopen(tmp_path):
    path = tmp_path / "buyer.sqlite3"
    with pytest.raises(SchemaError):
        OfflineGateStore(path, policy_for_test())
    assert not path.exists()
    load_version3(path)
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA user_version=99")
    before = path.read_bytes()
    with pytest.raises(SchemaError):
        OfflineGateStore(path, policy_for_test())
    assert path.read_bytes() == before
    assert not path.with_name(path.name + "-wal").exists()


@pytest.mark.parametrize("release", [True, False])
def test_post_commit_wal_contention_does_not_replay_migration(tmp_path, monkeypatch, release):
    path = tmp_path / "buyer.sqlite3"
    before = load_version3(path)
    original = Database.connect
    switches, busy_errors, version_writes, commits = [], [], [], []
    blocker = sqlite3.connect(path, isolation_level=None)

    @contextmanager
    def connection(self, **kwargs):
        with original(self, **kwargs) as db:

            class ObservedConnection:
                def __getattr__(self, name):
                    return getattr(db, name)

                def commit(self):
                    commits.append(True)
                    return db.commit()

                def execute(self, sql, parameters=()):
                    if sql == "PRAGMA user_version=4":
                        version_writes.append(sql)
                    if sql == "PRAGMA journal_mode=WAL":
                        switches.append(kwargs.get("timeout"))
                        if len(switches) == 1:
                            # A genuine second SQLite connection acquires its lock
                            # after marker commit, before this opener configures WAL.
                            assert not db.in_transaction
                            blocker.execute("BEGIN IMMEDIATE")
                        try:
                            return db.execute(sql, parameters)
                        except sqlite3.OperationalError as error:
                            busy_errors.append(error.sqlite_errorcode)
                            if release:
                                blocker.rollback()
                            raise
                    return db.execute(sql, parameters)

            yield ObservedConnection()

    try:
        with monkeypatch.context() as patch:
            patch.setattr(Database, "connect", connection)
            if release:
                OfflineGateStore(path, policy_for_test())
            else:
                with pytest.raises(sqlite3.OperationalError) as error:
                    OfflineGateStore(path, policy_for_test())
                assert error.value.sqlite_errorcode == sqlite3.SQLITE_BUSY
        assert len(switches) >= 2
        assert busy_errors and set(busy_errors) == {sqlite3.SQLITE_BUSY}
        assert len(version_writes) == len(commits) == 1
        assert all(0 <= limit <= 0.2 for limit in switches)
        assert all(later <= earlier for earlier, later in zip(switches, switches[1:], strict=False))
    finally:
        blocker.rollback()
        blocker.close()
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 4
        assert list(db.iterdump()) == before
        assert db.execute("PRAGMA journal_mode").fetchone()[0] == ("wal" if release else "delete")
    # A deadline failure still leaves the committed history available for an
    # explicit reopen, without repeating any migration or financial transition.
    OfflineGateStore(path, policy_for_test())
    with sqlite3.connect(path) as db:
        assert list(db.iterdump()) == before


@pytest.mark.parametrize("failure", ["io_error", "wrong_mode"])
def test_wal_noncontention_failure_propagates_without_retry(tmp_path, monkeypatch, failure):
    path = tmp_path / "buyer.sqlite3"
    before = load_version3(path)
    original = Database.connect
    switches = []

    @contextmanager
    def connection(self, **kwargs):
        with original(self, **kwargs) as db:

            class ObservedConnection:
                def __getattr__(self, name):
                    return getattr(db, name)

                def execute(self, sql, parameters=()):
                    if sql == "PRAGMA journal_mode=WAL":
                        switches.append(sql)
                        if failure == "io_error":
                            error = sqlite3.OperationalError("synthetic WAL I/O failure")
                            error.sqlite_errorcode = sqlite3.SQLITE_IOERR
                            raise error
                        return db.execute("PRAGMA journal_mode")  # Still delete; not success.
                    return db.execute(sql, parameters)

            yield ObservedConnection()

    with monkeypatch.context() as patch:
        patch.setattr(Database, "connect", connection)
        with pytest.raises(sqlite3.OperationalError if failure == "io_error" else SchemaError):
            OfflineGateStore(path, policy_for_test())
    assert len(switches) == 1
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 4
        assert db.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
        assert list(db.iterdump()) == before
    OfflineGateStore(path, policy_for_test())
