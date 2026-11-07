"""Regressions from the independent Phase 3B.1 review; no real side effects."""

import asyncio
import sqlite3
import subprocess
import sys
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path

import pytest
from test_x402_gate import (
    MARKER,
    NOW,
    DispatchDouble,
    SigningDouble,
    assert_counts,
    deny_sql,
    header_for,
    policy_for_test,
    run_gate,
)
from test_x402_gate import offline as offline_fixture
from test_x402_recovery import load_phase2, original_rows

from agentic_payments_lab.buyer_schema import SchemaError
from agentic_payments_lab.catalog import default_catalog
from agentic_payments_lab.domain import PurchaseRequest
from agentic_payments_lab.ledger import Ledger
from agentic_payments_lab.policy import Policy
from agentic_payments_lab.storage import Database
from agentic_payments_lab.x402_adapter.gate import OfflineGate
from agentic_payments_lab.x402_contracts import GateDenied
from agentic_payments_lab.x402_store import OfflineGateStore

offline = offline_fixture


def test_cancellation_before_claim_persists_a_fence(offline):
    store, signer, dispatcher, _ = offline
    store.cancel("task", NOW)
    reopened = OfflineGateStore(store.db.path, store.policy)
    report = run_gate(OfflineGate(reopened, signer, dispatcher, lambda: NOW))
    assert_counts(signer, dispatcher, (0, 0))
    assert report["phase"] == "canceled_before_claim"
    assert report["authorization_id"] is None
    assert report["budget"] == {"budget": 30_000, "reserved": 0, "spent": 0}
    assert not report["dispatch_may_have_occurred"]
    assert not report["reconciliation_required"]
    assert report["delivery_state"] == "not_requested"
    with reopened.db.connect() as db:
        assert db.execute("SELECT count(*) FROM x402_attempts").fetchone()[0] == 0


def test_cancel_before_claim_in_an_already_started_run(offline, monkeypatch):
    store, signer, dispatcher, gate = offline
    reached, proceed = threading.Event(), threading.Event()
    original = store.claim

    def paused(*args):
        reached.set()
        assert proceed.wait(5)
        return original(*args)

    monkeypatch.setattr(store, "claim", paused)
    with ThreadPoolExecutor(max_workers=1) as pool:
        running = pool.submit(run_gate, gate)
        try:
            assert reached.wait(5)
            store.cancel("task", NOW)
        finally:
            proceed.set()
        report = running.result(timeout=5)
    assert_counts(signer, dispatcher, (0, 0))
    assert report["phase"] == "canceled_before_claim"
    assert report["budget"]["reserved"] == 0


def test_cancellation_exception_cannot_expose_transient_header(offline):
    store, signer, dispatcher, gate = offline

    async def dispatch(**kwargs):
        dispatcher.calls.append(kwargs)
        error = asyncio.CancelledError(MARKER + " " + kwargs["payment_header"])
        error.add_note(MARKER)
        raise error

    dispatcher.dispatch = dispatch
    with pytest.raises(asyncio.CancelledError) as caught:
        run_gate(gate)
    exposed = (
        str(caught.value) + repr(caught.value) + "".join(traceback.format_exception(caught.value))
    )
    assert MARKER not in exposed
    assert dispatcher.calls[0]["payment_header"] not in exposed
    assert not getattr(caught.value, "__notes__", [])
    assert_counts(signer, dispatcher, (1, 1))
    report = store.report("task")
    assert report["dispatch_may_have_occurred"] and report["reconciliation_required"]
    assert report["budget"] == {"budget": 30_000, "reserved": 10_000, "spent": 0}


def test_unknown_schema_rejection_does_not_modify_journal(tmp_path):
    path = tmp_path / "unknown.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE future(data TEXT)")
        db.execute("INSERT INTO future VALUES('preserve this data')")
        db.execute("PRAGMA user_version=99")
        before_journal = db.execute("PRAGMA journal_mode").fetchone()[0]
        before = list(db.iterdump())
    before_bytes = path.read_bytes()
    with pytest.raises(SchemaError):
        Ledger(path, Policy(default_catalog()))
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA journal_mode").fetchone()[0] == before_journal
        assert db.execute("PRAGMA user_version").fetchone()[0] == 99
        assert list(db.iterdump()) == before
    assert path.read_bytes() == before_bytes


def test_repeated_cancel_and_restart_keep_one_task_fence(offline):
    store, signer, dispatcher, gate = offline
    identity = store.report("task")["purchase_id"]
    store.create_task(
        "other", PurchaseRequest(service_id="test-report", subject="other", purpose="research"), NOW
    )
    store.cancel("task", NOW)
    before = store.report("task")
    for _ in range(3):
        reopened = OfflineGateStore(store.db.path, store.policy)
        reopened.cancel("task", NOW + 10)
        assert run_gate(OfflineGate(reopened, signer, dispatcher, lambda: NOW)) == before
    assert before["purchase_id"] == identity
    assert_counts(signer, dispatcher, (0, 0))
    with store.db.connect() as db:
        assert db.execute("SELECT count(*) FROM x402_cancellations").fetchone()[0] == 1
        assert (
            db.execute(
                "SELECT count(*) FROM events WHERE event_type='offline_cancellation'"
            ).fetchone()[0]
            == 1
        )
    for sql in ("DELETE FROM x402_cancellations", "UPDATE x402_cancellations SET recorded_at=1"):
        with pytest.raises(sqlite3.IntegrityError), store.db.transaction() as db:
            db.execute(sql)
    assert store.report("other")["phase"] == "not_started"
    run_gate(gate, task="other")
    assert_counts(signer, dispatcher, (1, 1))


@pytest.mark.parametrize("task", ["missing", "not-enrolled"])
def test_cancel_rejects_missing_or_unenrolled_task(offline, task):
    store, signer, dispatcher, _ = offline
    with store.db.transaction() as db:
        db.execute(
            "INSERT INTO tasks VALUES('not-enrolled',?,'other-purchase')",
            (store.policy.grant.grant_id,),
        )
    before = store.report("task")
    with pytest.raises(GateDenied, match="not_enrolled"):
        store.cancel(task, NOW)
    assert store.report("task") == before
    with store.db.connect() as db:
        assert db.execute("SELECT count(*) FROM x402_cancellations").fetchone()[0] == 0
    assert_counts(signer, dispatcher, (0, 0))


@pytest.mark.parametrize("failure", ["fence", "event", "commit"])
def test_cancellation_persistence_failure_does_not_acknowledge_success(
    offline, monkeypatch, failure
):
    store, signer, dispatcher, gate = offline
    before = store.report("task")
    deny_sql(
        monkeypatch,
        store,
        lambda action, a, b: (
            (action == sqlite3.SQLITE_INSERT and a == "x402_cancellations" and failure == "fence")
            or (action == sqlite3.SQLITE_INSERT and a == "events" and failure == "event")
            or (action == sqlite3.SQLITE_TRANSACTION and a == "COMMIT" and failure == "commit")
        ),
    )
    with pytest.raises(sqlite3.DatabaseError):
        store.cancel("task", NOW)
    monkeypatch.undo()
    reopened = OfflineGateStore(store.db.path, store.policy)
    assert reopened.report("task") == before
    with reopened.db.connect() as db:
        assert db.execute("SELECT count(*) FROM x402_cancellations").fetchone()[0] == 0
    # An explicit operator retry can commit the fence; no payment was attempted.
    reopened.cancel("task", NOW)
    run_gate(gate)
    assert_counts(signer, dispatcher, (0, 0))


def test_lost_cancellation_commit_acknowledgment_retains_fence(offline, monkeypatch):
    store, signer, dispatcher, gate = offline
    original = store.db.transaction

    @contextmanager
    def lost_ack():
        with original() as db:
            yield db
        raise sqlite3.OperationalError("lost commit acknowledgment")

    monkeypatch.setattr(store.db, "transaction", lost_ack)
    with pytest.raises(sqlite3.OperationalError):
        store.cancel("task", NOW)
    monkeypatch.undo()
    assert run_gate(gate)["phase"] == "canceled_before_claim"
    assert_counts(signer, dispatcher, (0, 0))


def test_claim_waits_for_cancellation_transaction_and_observes_committed_fence(
    offline, monkeypatch
):
    store, signer, dispatcher, gate = offline
    store.db.timeout = 5
    at_commit, claim_waiting, release = threading.Event(), threading.Event(), threading.Event()
    original = store.db.connect
    cancel_thread = None

    @contextmanager
    def connect(**kwargs):
        with original(**kwargs) as db:

            def trace(sql):
                if threading.get_ident() == cancel_thread and sql == "COMMIT":
                    at_commit.set()
                    assert release.wait(5)
                elif threading.get_ident() != cancel_thread and sql == "BEGIN IMMEDIATE":
                    claim_waiting.set()

            db.set_trace_callback(trace)
            yield db

    def cancel():
        nonlocal cancel_thread
        cancel_thread = threading.get_ident()
        store.cancel("task", NOW)

    monkeypatch.setattr(store.db, "connect", connect)
    with ThreadPoolExecutor(max_workers=2) as pool:
        canceled = pool.submit(cancel)
        try:
            assert at_commit.wait(5)
            running = pool.submit(run_gate, gate)
            assert claim_waiting.wait(5)
            assert_counts(signer, dispatcher, (0, 0))
        finally:
            release.set()
        canceled.result(timeout=5)
        report = running.result(timeout=5)
    assert report["phase"] == "canceled_before_claim" and report["budget"]["reserved"] == 0
    assert_counts(signer, dispatcher, (0, 0))


def test_cancellation_survives_process_exit_and_repeated_invocation(offline, tmp_path):
    store, signer, dispatcher, _ = offline
    identity = store.report("task")["purchase_id"]
    calls = tmp_path / "calls.txt"
    child = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).with_name("x402_crash_worker.py")),
            str(store.db.path),
            str(calls),
            "cancel_before_claim",
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert child.returncode == 91, child.stderr
    assert not calls.exists()
    for _ in range(2):
        reopened = OfflineGateStore(store.db.path, store.policy)
        report = run_gate(OfflineGate(reopened, signer, dispatcher, lambda: NOW))
        assert report["purchase_id"] == identity
        assert report["phase"] == "canceled_before_claim"
        assert report["budget"]["reserved"] == 0
    assert_counts(signer, dispatcher, (0, 0))


@pytest.mark.parametrize(
    "origin", ["signer", "dispatcher", "external_signer", "external_dispatcher"]
)
@pytest.mark.parametrize("cleanup", ["success", "error", "cancellation"])
def test_cancellation_text_notes_and_cleanup_errors_are_sanitized(
    offline, monkeypatch, origin, cleanup
):
    store, signer, dispatcher, gate = offline
    if cleanup != "success":

        def fail(*args):
            error_type = RuntimeError if cleanup == "error" else asyncio.CancelledError
            raise error_type(MARKER)

        monkeypatch.setattr(store, "cancel", fail)

    async def scenario():
        entered = asyncio.Event()

        async def sign(terms):
            signer.calls.append(terms)
            if origin == "signer":
                error = asyncio.CancelledError(MARKER)
                error.add_note(MARKER)
                raise error
            entered.set()
            await asyncio.Event().wait()

        async def dispatch(**kwargs):
            dispatcher.calls.append(kwargs)
            if origin == "dispatcher":
                error = asyncio.CancelledError(MARKER + " " + kwargs["payment_header"])
                error.add_note(MARKER)
                raise error
            entered.set()
            await asyncio.Event().wait()

        if origin in ("dispatcher", "external_dispatcher"):
            dispatcher.dispatch = dispatch
        else:
            signer.sign = sign
        task = asyncio.create_task(
            gate.run(
                "task", header_for(), resource=store.policy.resource, method=store.policy.method
            )
        )
        if origin.startswith("external"):
            await entered.wait()
            task.cancel(MARKER)
        with pytest.raises(asyncio.CancelledError) as caught:
            await task
        assert task.cancelled()
        return caught.value

    error = asyncio.run(scenario())
    exposed = str(error) + repr(error) + "".join(traceback.format_exception(error))
    assert MARKER not in exposed
    if dispatcher.calls:
        assert dispatcher.calls[0]["payment_header"] not in exposed
    assert error.__cause__ is None and error.__suppress_context__
    assert not getattr(error, "__notes__", [])
    before = store.report("task")
    assert before["budget"] == {"budget": 30_000, "reserved": 10_000, "spent": 0}
    assert before["reconciliation_required"]
    monkeypatch.undo()
    reopened = OfflineGateStore(store.db.path, store.policy)
    assert run_gate(OfflineGate(reopened, signer, dispatcher, lambda: NOW)) == before
    assert_counts(signer, dispatcher, (1, 1 if "dispatcher" in origin else 0))


V1_FIXTURE = Path(__file__).parent / "fixtures" / "phase3b1_v1_ledger.sql"


def load_v1(path):
    with sqlite3.connect(path) as db:
        db.executescript(V1_FIXTURE.read_text())


def v1_rows(path):
    with sqlite3.connect(path) as db:
        return {
            **original_rows(path),
            **{
                table: db.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()
                for table in ("buyer_profile", "x402_tasks", "x402_attempts")
            },
        }


def test_v1_upgrade_keeps_original_authorizations_and_new_fences(tmp_path):
    path = tmp_path / "v1.sqlite3"
    load_v1(path)
    before = v1_rows(path)
    store = OfflineGateStore(path, policy_for_test())
    assert v1_rows(path) == before
    signer, dispatcher = SigningDouble(), DispatchDouble()
    gate = OfflineGate(store, signer, dispatcher, lambda: NOW)
    for task in ("claimed", "canceled", "dispatched"):
        report = run_gate(gate, task=task)
        assert report["budget"] == {"budget": 30_000, "reserved": 30_000, "spent": 0}
    assert_counts(signer, dispatcher, (0, 0))
    with store.db.connect() as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 3
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
        assert db.execute("SELECT count(*) FROM x402_cancellations").fetchone()[0] == 0
    store.cancel("not-started", NOW)
    reopened = OfflineGateStore(path, store.policy)
    assert (
        run_gate(OfflineGate(reopened, signer, dispatcher, lambda: NOW), task="not-started")[
            "phase"
        ]
        == "canceled_before_claim"
    )
    assert_counts(signer, dispatcher, (0, 0))


@pytest.mark.parametrize("failure", ["table", "version", "commit"])
def test_failed_v1_upgrade_preserves_schema_journal_and_every_original_row(
    tmp_path, monkeypatch, failure
):
    path = tmp_path / "v1.sqlite3"
    load_v1(path)
    before = v1_rows(path)
    with sqlite3.connect(path) as db:
        before_dump = list(db.iterdump())
    original = Database.connect

    @contextmanager
    def connect(self, **kwargs):
        with original(self, **kwargs) as db:

            def authorize(action, a, b, *_):
                denied = (
                    (
                        failure == "table"
                        and action == sqlite3.SQLITE_CREATE_TABLE
                        and a == "x402_cancellations"
                    )
                    or (
                        failure == "version"
                        and action == sqlite3.SQLITE_PRAGMA
                        and a == "user_version"
                        and b == "3"
                    )
                    or (
                        failure == "commit"
                        and action == sqlite3.SQLITE_TRANSACTION
                        and a == "COMMIT"
                    )
                )
                return sqlite3.SQLITE_DENY if denied else sqlite3.SQLITE_OK

            db.set_authorizer(authorize)
            yield db

    monkeypatch.setattr(Database, "connect", connect)
    with pytest.raises(sqlite3.DatabaseError):
        OfflineGateStore(path, policy_for_test())
    monkeypatch.undo()
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 1
        assert db.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
        assert list(db.iterdump()) == before_dump
    assert v1_rows(path) == before
    OfflineGateStore(path, policy_for_test())
    assert v1_rows(path) == before


def test_concurrent_v1_upgrade_processes_preserve_history(tmp_path):
    path = tmp_path / "v1.sqlite3"
    load_v1(path)
    before = v1_rows(path)
    calls = tmp_path / "calls.txt"
    children = [
        subprocess.Popen(
            [
                sys.executable,
                str(Path(__file__).with_name("x402_crash_worker.py")),
                str(path),
                str(calls),
                "migrate_v1",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for _ in range(6)
    ]
    outputs = [child.communicate(timeout=10) for child in children]
    assert [child.returncode for child in children] == [0] * 6, outputs
    assert not calls.exists()
    assert v1_rows(path) == before
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 3


def test_schema_revalidated_under_lock_before_any_persistent_change(tmp_path, monkeypatch):
    path = tmp_path / "phase2.sqlite3"
    load_phase2(path)
    before = original_rows(path)
    original = Database.transaction

    @contextmanager
    def changed_after_preflight(self):
        with sqlite3.connect(path) as writer:
            writer.execute("PRAGMA user_version=99")
        with original(self) as db:
            yield db

    monkeypatch.setattr(Database, "transaction", changed_after_preflight)
    with pytest.raises(SchemaError):
        Ledger(path, Policy(default_catalog()))
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 99
        assert db.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
        assert not db.execute("SELECT 1 FROM sqlite_master WHERE name='buyer_profile'").fetchall()
    assert original_rows(path) == before


def test_wal_configuration_failure_keeps_committed_upgrade_reopenable(tmp_path, monkeypatch):
    path = tmp_path / "phase2.sqlite3"
    load_phase2(path)
    before = original_rows(path)
    original = Database.connect

    @contextmanager
    def connect(self, **kwargs):
        with original(self, **kwargs) as db:

            def authorize(action, a, b, *_):
                denied = action == sqlite3.SQLITE_PRAGMA and a == "journal_mode" and b == "WAL"
                return sqlite3.SQLITE_DENY if denied else sqlite3.SQLITE_OK

            db.set_authorizer(authorize)
            yield db

    monkeypatch.setattr(Database, "connect", connect)
    with pytest.raises(sqlite3.DatabaseError):
        Ledger(path, Policy(default_catalog()))
    monkeypatch.undo()
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 3
        assert db.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
    assert original_rows(path) == before
    ledger = Ledger(path, Policy(default_catalog()))
    with ledger.db.connect() as db:
        assert db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert original_rows(path) == before
