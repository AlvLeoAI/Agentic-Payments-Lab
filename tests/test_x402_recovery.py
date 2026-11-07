import asyncio
import json
import sqlite3
import subprocess
import sys
import threading
from contextlib import contextmanager
from pathlib import Path

import pytest
from test_x402_gate import (
    NOW,
    DispatchDouble,
    SigningDouble,
    assert_counts,
    evidence_for,
    header_for,
    policy_for_test,
    record,
    run_gate,
)
from test_x402_gate import (
    offline as offline_fixture,
)

from agentic_payments_lab.buyer_schema import SchemaError
from agentic_payments_lab.catalog import default_catalog
from agentic_payments_lab.domain import PurchaseConflict, PurchaseRequest
from agentic_payments_lab.ledger import Ledger
from agentic_payments_lab.policy import Policy
from agentic_payments_lab.storage import Database
from agentic_payments_lab.x402_adapter.gate import OfflineGate
from agentic_payments_lab.x402_store import OfflineGateStore

FIXTURE = Path(__file__).parent / "fixtures" / "phase2_ledger.sql"
offline = offline_fixture


def load_phase2(path):
    with sqlite3.connect(path) as db:
        db.executescript(FIXTURE.read_text())


def original_rows(path):
    with sqlite3.connect(path) as db:
        return {
            table: db.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()
            for table in ("grants", "tasks", "quotes", "purchases", "events", "agent_bindings")
        }


def test_versioned_additive_upgrade_preserves_phase2_finance_and_bindings(tmp_path):
    path = tmp_path / "ledger.sqlite3"
    load_phase2(path)
    before = original_rows(path)
    ledger = Ledger(path, Policy(default_catalog()))
    assert (
        original_rows(path) == before
    )  # Byte-identical JSON, IDs, receipt, resource and event rows.
    assert ledger.budget("demo-grant").model_dump() == {
        "limit": 40_000,
        "reserved": 10_000,
        "spent": 10_000,
        "available": 20_000,
    }
    assert "original-fixture-intent" in ledger.agent_binding("fixture-agent")
    with ledger.db.connect() as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 3
        assert (
            json.loads(db.execute("SELECT data FROM buyer_profile").fetchone()[0])["mode"]
            == "simulated"
        )
        assert db.execute("SELECT count(*) FROM x402_attempts").fetchone()[0] == 0
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
    Ledger(path, Policy(default_catalog()))  # Idempotent reopen, no duplicate migration.
    assert original_rows(path) == before
    for row in before["purchases"]:
        export = ledger.export_audit(row[0])
        assert export["mode"] == "simulated" and "receipt" not in export


@pytest.mark.parametrize("deny", ["index", "version"])
def test_failed_upgrade_rolls_back_all_ddl_and_version(tmp_path, monkeypatch, deny):
    path = tmp_path / "ledger.sqlite3"
    load_phase2(path)
    before = original_rows(path)
    original = Database.connect

    @contextmanager
    def connect(self, **kwargs):
        with original(self, **kwargs) as db:

            def authorizer(action, a, b, *_):
                if (deny == "index" and action == sqlite3.SQLITE_CREATE_INDEX) or (
                    deny == "version"
                    and action == sqlite3.SQLITE_PRAGMA
                    and a == "user_version"
                    and b == "3"
                ):
                    return sqlite3.SQLITE_DENY
                return sqlite3.SQLITE_OK

            db.set_authorizer(authorizer)
            yield db

    monkeypatch.setattr(Database, "connect", connect)
    with pytest.raises(sqlite3.DatabaseError):
        Ledger(path, Policy(default_catalog()))
    monkeypatch.undo()
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 0
        assert not db.execute("SELECT 1 FROM sqlite_master WHERE name='buyer_profile'").fetchall()
    assert original_rows(path) == before
    Ledger(path, Policy(default_catalog()))
    assert original_rows(path) == before


@pytest.mark.parametrize("damage", ["future", "missing_table", "missing_profile", "unknown_legacy"])
def test_unknown_or_incomplete_state_is_not_implicitly_repaired(tmp_path, damage):
    path = tmp_path / "ledger.sqlite3"
    load_phase2(path)
    if damage != "unknown_legacy":
        Ledger(path, Policy(default_catalog()))
    with sqlite3.connect(path) as db:
        if damage == "future":
            db.execute("PRAGMA user_version=99")
        elif damage == "missing_table":
            db.execute("DROP TABLE x402_attempts")
        elif damage == "missing_profile":
            db.execute("DROP TRIGGER buyer_profile_no_delete")
            db.execute("DELETE FROM buyer_profile")
        else:
            db.execute("CREATE TABLE unexpected(data TEXT)")
        before = list(db.iterdump())
    with pytest.raises(SchemaError):
        Ledger(path, Policy(default_catalog()))
    with sqlite3.connect(path) as db:
        assert list(db.iterdump()) == before


def test_simulation_and_offline_x402_never_adopt_each_others_state(tmp_path):
    path = tmp_path / "simulation.sqlite3"
    load_phase2(path)
    with pytest.raises(SchemaError):
        OfflineGateStore(path, policy_for_test())
    Ledger(path, Policy(default_catalog()))
    with pytest.raises(SchemaError):
        OfflineGateStore(path, policy_for_test())
    with pytest.raises(SchemaError):
        OfflineGateStore.initialize(path, policy_for_test())
    offline_path = tmp_path / "offline.sqlite3"
    OfflineGateStore.initialize(offline_path, policy_for_test())
    with pytest.raises(SchemaError):
        Ledger(offline_path, Policy(default_catalog()))
    changed = policy_for_test().model_copy(update={"amount": 1})
    with pytest.raises(SchemaError):
        OfflineGateStore(offline_path, changed)
    with pytest.raises(SchemaError):
        OfflineGateStore(tmp_path / "missing.sqlite3", policy_for_test())
    assert not (tmp_path / "missing.sqlite3").exists()


def test_initial_task_binding_and_authorization_are_immutable(offline):
    store, signer, dispatcher, gate = offline
    request = PurchaseRequest(service_id="test-report", subject="synthetic", purpose="research")
    original = store.create_task("task", request, NOW)
    assert original.purchase_id == store.report("task")["purchase_id"]
    with pytest.raises(PurchaseConflict):
        store.create_task("task", request.model_copy(update={"subject": "new"}), NOW)
    run_gate(gate)
    before = record(store)
    with pytest.raises(sqlite3.IntegrityError), store.db.transaction() as db:
        db.execute("UPDATE x402_attempts SET nonce=?", ("0x" + "ff" * 32,))
    with pytest.raises(sqlite3.IntegrityError), store.db.transaction() as db:
        db.execute("DELETE FROM x402_attempts")
    assert record(store) == before
    assert_counts(signer, dispatcher, (1, 1))


@pytest.mark.parametrize(
    ("boundary", "signs", "dispatches"),
    [
        ("signature_lost", 1, 0),
        ("dispatch_committed", 1, 0),
        ("dispatch_entered", 1, 1),
        ("response_lost", 1, 1),
    ],
)
def test_abrupt_exit_leaves_durable_identity_and_reserved_exposure(
    offline, tmp_path, boundary, signs, dispatches
):
    store, _, _, _ = offline
    counts = tmp_path / "independent-double-calls.txt"
    child = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).with_name("x402_crash_worker.py")),
            str(store.db.path),
            str(counts),
            boundary,
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert child.returncode == 91, child.stderr
    lines = counts.read_text().splitlines()
    assert (lines.count("signer"), lines.count("dispatcher")) == (signs, dispatches)
    before = record(store)
    signer, dispatcher = SigningDouble(), DispatchDouble()
    reopened = OfflineGateStore(store.db.path, store.policy)
    report = run_gate(OfflineGate(reopened, signer, dispatcher, lambda: NOW))
    assert_counts(signer, dispatcher, (0, 0))
    assert record(store) == before
    assert report["reconciliation_required"] and report["budget"]["reserved"] == 10_000
    assert report["budget"]["spent"] == 0
    assert report["delivery_state"] == "not_requested"


def test_event_index_and_split_audit_keep_order_and_use_index(tmp_path):
    ledger = Ledger(tmp_path / "buyer.sqlite3", Policy(default_catalog()))
    with ledger.db.transaction() as db:
        db.executemany(
            "INSERT INTO events(purchase_id,event_type,data,recorded_at) VALUES(?,?,?,?)",
            [(f"p{n % 100}", "test", "{}", NOW) for n in range(2000)],
        )
        Ledger._event(db, None, "global", {}, NOW)
        db.execute("ANALYZE")
        columns = [row["name"] for row in db.execute("PRAGMA index_info(events_purchase_id)")]
        assert columns == ["purchase_id"]
        plan = [
            row["detail"].lower()
            for row in db.execute(
                "EXPLAIN QUERY PLAN SELECT * FROM events WHERE purchase_id=? ORDER BY sequence",
                ("p42",),
            )
        ]
        assert any("search" in detail and "events_purchase_id" in detail for detail in plan)
        assert not any("temp b-tree" in detail for detail in plan)
    all_events = ledger.audit()
    assert len(all_events) == 2001
    assert ledger.audit("p42") == [row for row in all_events if row["purchase_id"] == "p42"]
    assert ledger.audit("absent") == []
    assert [e["sequence"] for e in all_events] == sorted(e["sequence"] for e in all_events)
    # Assert the actual audit operation avoids the optional-filter OR shape, too.
    statements = []
    original = ledger.db.connect

    @contextmanager
    def trace(**kwargs):
        with original(**kwargs) as db:
            db.set_trace_callback(statements.append)
            yield db

    ledger.db.connect = trace
    ledger.audit("p42")
    assert not any(" IS NULL OR " in sql for sql in statements)


def test_offline_report_snapshot_spans_accounting_and_events(offline, monkeypatch):
    store, _, _, gate = offline
    run_gate(gate)
    before = store.report("task")
    evidence = evidence_for(store)
    reading_events, written = threading.Event(), threading.Event()
    original = store.db.connect
    reader_thread = threading.get_ident()

    @contextmanager
    def paused(**kwargs):
        with original(**kwargs) as db:

            def trace(sql):
                if threading.get_ident() == reader_thread and sql.startswith(
                    "SELECT * FROM events"
                ):
                    reading_events.set()
                    assert written.wait(5)

            db.set_trace_callback(trace)
            yield db

    def reconcile():
        assert reading_events.wait(5)
        store.apply_fixture("task", evidence, NOW + 100)
        written.set()

    monkeypatch.setattr(store.db, "connect", paused)
    writer = threading.Thread(target=reconcile)
    writer.start()
    export = store.report("task")
    writer.join(5)
    assert not writer.is_alive()
    monkeypatch.undo()
    assert export == before
    assert store.report("task")["phase"] == "fixture_settled"


def test_expired_nonuse_fixture_fences_inflight_signer(offline):
    store, signer, dispatcher, gate = offline

    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()

        async def sign(terms):
            from agentic_payments_lab.x402_adapter.protocol import OfflineSignature

            signer.calls.append(terms)
            entered.set()
            await release.wait()
            return OfflineSignature("offline-test-signature:late")

        signer.sign = sign
        task = asyncio.create_task(
            gate.run(
                "task", header_for(), resource=store.policy.resource, method=store.policy.method
            )
        )
        await entered.wait()
        store.apply_fixture("task", evidence_for(store, "unused"), NOW + 100)
        release.set()
        await task

    asyncio.run(scenario())
    assert_counts(signer, dispatcher, (1, 0))
    assert store.report("task")["phase"] == "fixture_unused"
    assert store.report("task")["budget"]["reserved"] == 0
