"""Offline side effects measured at independent doubles, never inferred from reports."""

import asyncio
import base64
import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass, field

import pytest
from x402.http.utils import decode_payment_signature_header

from agentic_payments_lab.buyer_schema import SchemaError
from agentic_payments_lab.domain import Grant, PurchaseConflict, PurchaseRequest, digest
from agentic_payments_lab.x402_adapter.gate import GateUnavailable, OfflineGate
from agentic_payments_lab.x402_adapter.protocol import OfflineSignature
from agentic_payments_lab.x402_contracts import (
    FixtureEvidence,
    GateDenied,
    OperatorPolicy,
    SigningTerms,
)
from agentic_payments_lab.x402_store import OfflineGateStore

NOW = 1_800_000_000
PAYER = "0x" + "11" * 20
RECIPIENT = "0x" + "22" * 20
MARKER = "offline-test-signature:SENTINEL_NOT_FOR_RETENTION"


def policy_for_test():
    return OperatorPolicy(
        grant=Grant(
            grant_id="offline-grant",
            operator_id="operator",
            service_ids=("test-report",),
            purpose="research",
            budget=30_000,
            per_purchase_limit=10_000,
            issued_at=NOW - 1,
            expires_at=NOW + 600,
        ),
        service_id="test-report",
        resource="https://merchant.invalid/report",
        method="POST",
        payer=PAYER,
        recipient=RECIPIENT,
        amount=10_000,
        max_timeout_seconds=60,
    )


def wire_data():
    # Independent literal fixture; do not ask the validator to generate its own test input.
    return {
        "x402Version": 2,
        "resource": {"url": "https://merchant.invalid/report"},
        "accepts": [
            {
                "scheme": "exact",
                "network": "eip155:84532",
                "asset": "0x036CbD53842c5426634e7929541eC2318f3dCF7e",
                "amount": "10000",
                "payTo": RECIPIENT,
                "maxTimeoutSeconds": 60,
                "extra": {"assetTransferMethod": "eip3009", "name": "USDC", "version": "2"},
            }
        ],
    }


def header_for(data=None):
    return base64.b64encode(json.dumps(wire_data() if data is None else data).encode()).decode()


@dataclass
class SigningDouble:
    mode = "offline_double"
    calls: list = field(default_factory=list)

    async def sign(self, terms):
        self.calls.append(terms)
        return OfflineSignature(MARKER)


@dataclass
class DispatchDouble:
    mode = "offline_double"
    calls: list = field(default_factory=list)

    async def dispatch(self, **kwargs):
        self.calls.append(kwargs)
        return {"status": 200, "facilitator": {"success": True, "transaction": "0xunverified"}}


@pytest.fixture
def offline(tmp_path):
    policy = policy_for_test()
    store = OfflineGateStore.initialize(tmp_path / "buyer.sqlite3", policy)
    request = PurchaseRequest(service_id="test-report", subject="synthetic", purpose="research")
    store.create_task("task", request, NOW)
    signer, dispatcher = SigningDouble(), DispatchDouble()
    return store, signer, dispatcher, OfflineGate(store, signer, dispatcher, lambda: NOW)


def run_gate(gate, task="task", header=None, **kwargs):
    arguments = {"resource": gate.store.policy.resource, "method": gate.store.policy.method}
    arguments.update(kwargs)
    return asyncio.run(gate.run(task, header or header_for(), **arguments))


def record(store):
    with store.db.connect() as db:
        return dict(db.execute("SELECT * FROM x402_attempts").fetchone())


def assert_counts(signer, dispatcher, expected):
    assert (len(signer.calls), len(dispatcher.calls)) == expected


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("x402Version",), 1),
        (("x402Version",), 2.0),
        (("x402Version",), "2"),
        (("accepts", 0, "scheme"), "upto"),
        (("accepts", 0, "network"), "eip155:8453"),
        (("accepts", 0, "network"), "simulation:local"),
        (("accepts", 0, "asset"), "SIM-USDC"),
        (("accepts", 0, "payTo"), PAYER),
        (("accepts", 0, "amount"), "10001"),
        (("accepts", 0, "amount"), "010000"),
        (("accepts", 0, "amount"), 10000),
        (("accepts", 0, "amount"), "1e4"),
        (("accepts", 0, "amount"), "-1"),
        (("accepts", 0, "maxTimeoutSeconds"), 61),
        (("accepts", 0, "maxTimeoutSeconds"), 60.0),
        (("accepts", 0, "maxTimeoutSeconds"), True),
        (("accepts", 0, "maxTimeoutSeconds"), 0),
        (("accepts", 0, "extra", "assetTransferMethod"), "permit2"),
        (("accepts", 0, "extra", "name"), "other"),
        (("accepts", 0, "extra", "version"), "1"),
        (("resource", "url"), "https://evil.invalid/report"),
        (("resource", "url"), "https://merchant.invalid/report?redirect=evil"),
        (("extensions",), {"erc20ApprovalGasSponsoring": {}}),
        (("accepts", 0, "extra", "paymentFlow"), "deferred"),
        (("extra",), "unknown"),
    ],
)
def test_unapproved_requirements_rejected_before_any_side_effect(offline, path, value):
    store, signer, dispatcher, gate = offline
    data = wire_data()
    target = data
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    with pytest.raises(GateDenied):
        run_gate(gate, header=header_for(data))
    assert_counts(signer, dispatcher, (0, 0))
    assert store.report("task")["budget"] == {"budget": 30_000, "reserved": 0, "spent": 0}
    with store.db.connect() as db:
        assert db.execute("SELECT count(*) FROM x402_attempts").fetchone()[0] == 0


@pytest.mark.parametrize(
    "raw",
    [
        b'{"x402Version":1,"x402Version":2}',
        b"{",
        b"[]",
        b"null",
        b"NaN",
        b"\xff",
        b" " * 6200,
        b"[" * 1500 + b"]" * 1500,
    ],
)
def test_malformed_or_oversized_input(offline, raw):
    _, signer, dispatcher, gate = offline
    with pytest.raises(GateDenied):
        run_gate(gate, header=base64.b64encode(raw).decode())
    assert_counts(signer, dispatcher, (0, 0))


@pytest.mark.parametrize("header", ["not-base64!", "A" * 8193])
def test_invalid_header(offline, header):
    _, signer, dispatcher, gate = offline
    with pytest.raises(GateDenied):
        run_gate(gate, header=header)
    assert_counts(signer, dispatcher, (0, 0))


@pytest.mark.parametrize("change", ["missing_version", "two_choices", "empty_choices"])
def test_no_protocol_defaults_or_payment_choice_fallback(offline, change):
    _, signer, dispatcher, gate = offline
    data = wire_data()
    if change == "missing_version":
        del data["x402Version"]
    else:
        data["accepts"] = data["accepts"] * 2 if change == "two_choices" else []
    with pytest.raises(GateDenied):
        run_gate(gate, header=header_for(data))
    assert_counts(signer, dispatcher, (0, 0))


@pytest.mark.parametrize(
    "kwargs",
    [{"method": "GET"}, {"status_code": 200}, {"resource": "https://merchant.invalid/redirect"}],
)
def test_resource_method_and_http_status(offline, kwargs):
    _, signer, dispatcher, gate = offline
    with pytest.raises(GateDenied):
        run_gate(gate, **kwargs)
    assert_counts(signer, dispatcher, (0, 0))


def test_exact_terms_committed_before_signing_and_dispatch_without_write_lock(offline):
    store, signer, dispatcher, gate = offline

    async def sign(terms):
        signer.calls.append(terms)
        row = record(store)
        assert row["phase"] == "signing_claimed" and row["owner"]
        assert SigningTerms.model_validate_json(row["terms"]) == terms
        assert row["nonce"] == terms.nonce and row["authorization_id"] == terms.authorization_id
        assert store.report("task")["budget"]["reserved"] == 10_000
        with store.db.transaction() as db:
            assert db.in_transaction  # An independent writer can acquire the lock.
        return OfflineSignature(MARKER)

    async def dispatch(**kwargs):
        dispatcher.calls.append(kwargs)
        assert record(store)["phase"] == "dispatch_authorized"
        with store.db.transaction():
            pass
        payload = decode_payment_signature_header(kwargs["payment_header"])
        terms = signer.calls[0]
        assert payload.x402_version == 2
        assert payload.accepted.model_dump(by_alias=True) == wire_data()["accepts"][0]
        assert payload.payload == {"signature": MARKER, "authorization": terms.authorization()}
        assert terms.typed_data()["domain"] == {
            "name": "USDC",
            "version": "2",
            "chainId": 84532,
            "verifyingContract": store.policy.asset,
        }
        assert terms.valid_after == 0 and terms.valid_before == NOW + 60
        assert kwargs["request"].subject == "synthetic"
        return {"status": 200, "success": True}

    signer.sign, dispatcher.dispatch = sign, dispatch
    report = run_gate(gate)
    assert_counts(signer, dispatcher, (1, 1))
    assert report["reconciliation_required"] and not report["blockchain_settlement_verified"]
    assert report["budget"] == {"budget": 30_000, "reserved": 10_000, "spent": 0}
    assert report["delivery_state"] == "not_requested"


def test_100_concurrent_invocations_have_one_owner_and_no_replacement(offline):
    store, signer, dispatcher, gate = offline
    # The gate uses a short SQLite wait so cancellation cleanup cannot wait 30s.
    # For this contention test allow normal operations more time; no cancellation here.
    store.db.timeout = 5
    with ThreadPoolExecutor(max_workers=20) as pool:
        reports = list(pool.map(lambda _: run_gate(gate), range(100)))
    assert len({r["purchase_id"] for r in reports}) == 1
    assert len({r["authorization_id"] for r in reports}) == 1
    assert_counts(signer, dispatcher, (1, 1))
    with store.db.connect() as db:
        assert db.execute("SELECT count(*) FROM x402_attempts").fetchone()[0] == 1
    reopened = OfflineGateStore(store.db.path, store.policy)
    again = OfflineGate(reopened, signer, dispatcher, lambda: NOW + 900)
    run_gate(again)  # Even expired ownership cannot be reset.
    assert_counts(signer, dispatcher, (1, 1))
    assert reopened.report("task")["budget"]["reserved"] == 10_000


def test_competing_tasks_share_atomic_budget(offline):
    store, signer, dispatcher, gate = offline
    store.db.timeout = 5
    request = PurchaseRequest(service_id="test-report", subject="synthetic", purpose="research")
    for number in range(20):
        store.create_task(f"task-{number}", request, NOW)

    def buy(number):
        try:
            return run_gate(gate, task=f"task-{number}")["phase"]
        except GateDenied as exc:
            return str(exc)

    with ThreadPoolExecutor(max_workers=20) as pool:
        outcomes = list(pool.map(buy, range(20)))
    assert outcomes.count("insufficient_budget") == 17
    assert_counts(signer, dispatcher, (3, 3))
    assert store.report("task")["budget"] == {"budget": 30_000, "reserved": 30_000, "spent": 0}


def deny_sql(monkeypatch, store, predicate):
    original = store.db.connect

    @contextmanager
    def connect(**kwargs):
        with original(**kwargs) as db:
            db.set_authorizer(
                lambda action, a, b, *_: (
                    sqlite3.SQLITE_DENY if predicate(action, a, b) else sqlite3.SQLITE_OK
                )
            )
            yield db

    monkeypatch.setattr(store.db, "connect", connect)


@pytest.mark.parametrize("failure", ["insert", "commit"])
def test_claim_persistence_failure_prevents_signing_and_rolls_back_reservation(
    offline, monkeypatch, failure
):
    store, signer, dispatcher, gate = offline
    deny_sql(
        monkeypatch,
        store,
        lambda action, a, b: (
            (action == sqlite3.SQLITE_INSERT and a == "x402_attempts")
            if failure == "insert"
            else (action == sqlite3.SQLITE_TRANSACTION and a == "COMMIT")
        ),
    )
    with pytest.raises(GateUnavailable):
        run_gate(gate)
    monkeypatch.undo()
    assert_counts(signer, dispatcher, (0, 0))
    assert store.report("task")["budget"]["reserved"] == 0
    assert store.report("task")["phase"] == "not_started"


def test_dispatch_persistence_failure_retains_claim_and_never_dispatches(offline, monkeypatch):
    store, signer, dispatcher, gate = offline
    deny_sql(
        monkeypatch,
        store,
        lambda action, a, b: (
            action == sqlite3.SQLITE_UPDATE and a == "x402_attempts" and b == "phase"
        ),
    )
    with pytest.raises(GateUnavailable):
        run_gate(gate)
    monkeypatch.undo()
    before = record(store)
    assert_counts(signer, dispatcher, (1, 0))
    run_gate(
        OfflineGate(OfflineGateStore(store.db.path, store.policy), signer, dispatcher, lambda: NOW)
    )
    assert record(store) == before
    assert_counts(signer, dispatcher, (1, 0))
    assert store.report("task")["budget"]["reserved"] == 10_000


@pytest.mark.parametrize(
    ("boundary", "expected"), [("claim", (0, 0)), ("authorize_dispatch", (1, 0))]
)
def test_commit_succeeded_but_caller_lost_result_never_retries(
    offline, monkeypatch, boundary, expected
):
    store, signer, dispatcher, gate = offline
    original = getattr(store, boundary)

    def ambiguous(*args):
        original(*args)
        raise sqlite3.OperationalError("injected lost commit acknowledgement")

    monkeypatch.setattr(store, boundary, ambiguous)
    if boundary == "claim":
        with pytest.raises(GateUnavailable):
            run_gate(gate)
    else:
        run_gate(gate)
    monkeypatch.undo()
    run_gate(gate)
    assert_counts(signer, dispatcher, expected)
    assert store.report("task")["budget"]["reserved"] == 10_000


@pytest.mark.parametrize("when", ["signing", "dispatch"])
def test_external_cancellation_and_restart(offline, when):
    store, signer, dispatcher, gate = offline

    async def scenario():
        entered = asyncio.Event()

        async def sign(terms):
            signer.calls.append(terms)
            entered.set()
            await asyncio.Event().wait()

        async def dispatch(**kwargs):
            dispatcher.calls.append(kwargs)
            entered.set()
            await asyncio.Event().wait()

        if when == "signing":
            signer.sign = sign
        else:
            dispatcher.dispatch = dispatch
        task = asyncio.create_task(
            gate.run(
                "task", header_for(), resource=store.policy.resource, method=store.policy.method
            )
        )
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())
    before = record(store)
    run_gate(
        OfflineGate(OfflineGateStore(store.db.path, store.policy), signer, dispatcher, lambda: NOW)
    )
    assert_counts(signer, dispatcher, (1, 0 if when == "signing" else 1))
    assert record(store) == before
    assert store.report("task")["budget"] == {"budget": 30_000, "reserved": 10_000, "spent": 0}
    assert store.report("task")["reconciliation_required"]


@pytest.mark.parametrize("cancel_mode", ["operator", "swallowed_external"])
def test_cancellation_fences_delayed_signer(offline, cancel_mode):
    store, signer, dispatcher, gate = offline

    async def scenario():
        entered, finish = asyncio.Event(), asyncio.Event()

        async def sign(terms):
            signer.calls.append(terms)
            entered.set()
            try:
                await finish.wait()
            except asyncio.CancelledError:
                pass  # Deliberately ill-behaved late double.
            return OfflineSignature(MARKER)

        signer.sign = sign
        task = asyncio.create_task(
            gate.run(
                "task", header_for(), resource=store.policy.resource, method=store.policy.method
            )
        )
        await entered.wait()
        if cancel_mode == "operator":
            store.cancel("task", NOW)
            finish.set()
            await task
        else:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

    asyncio.run(scenario())
    assert_counts(signer, dispatcher, (1, 0))
    assert store.report("task")["phase"] == "canceled_before_dispatch"
    assert store.report("task")["budget"]["reserved"] == 10_000


def test_cancellation_after_dispatch_permission_remains_ambiguous(offline):
    store, signer, dispatcher, gate = offline
    original = store.authorize_dispatch

    def dispatch_wins(*args):
        authorized = original(*args)
        store.cancel("task", NOW)
        return authorized

    store.authorize_dispatch = dispatch_wins
    report = run_gate(gate)
    assert_counts(signer, dispatcher, (1, 1))
    assert report["phase"] == "reconciliation_required"
    assert report["reason"] == "canceled_after_dispatch_authorization"
    assert report["budget"]["reserved"] == 10_000


def test_cancel_cleanup_failure_does_not_hide_cancellation_or_allow_restart(offline, monkeypatch):
    store, signer, dispatcher, gate = offline

    async def cancel(terms):
        signer.calls.append(terms)
        raise asyncio.CancelledError

    def fail(*args):
        raise sqlite3.OperationalError("injected cleanup failure")

    signer.sign = cancel
    monkeypatch.setattr(store, "cancel", fail)
    with pytest.raises(asyncio.CancelledError):
        run_gate(gate)
    monkeypatch.undo()
    run_gate(gate)
    assert_counts(signer, dispatcher, (1, 0))
    assert store.report("task")["phase"] == "signing_claimed"
    assert store.report("task")["reconciliation_required"]


@pytest.mark.parametrize("late_change", ["expires", "revoked"])
def test_recheck_terms_and_grant_after_signer_before_dispatch(offline, late_change):
    store, signer, dispatcher, gate = offline

    async def sign(terms):
        signer.calls.append(terms)
        if late_change == "expires":
            gate.clock = lambda: NOW + 61
        else:
            with store.db.transaction() as db:
                db.execute("UPDATE grants SET revoked=1")
        return OfflineSignature(MARKER)

    signer.sign = sign
    run_gate(gate)
    assert_counts(signer, dispatcher, (1, 0))
    assert store.report("task")["budget"]["reserved"] == 10_000


def evidence_for(store, kind="transfer", **overrides):
    terms = SigningTerms.model_validate_json(record(store)["terms"])
    data = dict(
        source="offline_fixture",
        terms_hash=digest(terms),
        nonce=terms.nonce,
        kind=kind,
        finality="finalized",
        payer=terms.payer,
        recipient=terms.recipient,
        amount=terms.amount,
        block_hash="0x" + "33" * 32,
        block_number=123,
        block_timestamp=NOW + (61 if kind == "unused" else 1),
        transaction_hash="0x" + "44" * 32 if kind == "transfer" else None,
        transaction_success=kind == "transfer",
        nonce_used=kind == "transfer",
    )
    data.update(overrides)
    return FixtureEvidence(**data)


@pytest.mark.parametrize("kind", ["transfer", "unused"])
def test_explicit_fixture_resolution_is_atomic_idempotent_and_labeled(offline, kind):
    store, signer, dispatcher, gate = offline
    run_gate(gate)
    evidence = evidence_for(store, kind)
    with ThreadPoolExecutor(max_workers=10) as pool:
        list(pool.map(lambda _: store.apply_fixture("task", evidence, NOW + 100), range(10)))
    report = run_gate(gate)
    assert_counts(signer, dispatcher, (1, 1))
    assert report["phase"] == ("fixture_settled" if kind == "transfer" else "fixture_unused")
    assert report["budget"]["reserved"] == 0
    assert report["budget"]["spent"] == (10_000 if kind == "transfer" else 0)
    assert not report["blockchain_settlement_verified"]
    with pytest.raises(GateDenied):
        store.apply_fixture(
            "task", evidence_for(store, "unused" if kind == "transfer" else "transfer"), NOW + 100
        )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"nonce": "0x" + "ff" * 32},
        {"recipient": PAYER},
        {"payer": RECIPIENT},
        {"amount": 1},
        {"finality": "pending"},
        {"transaction_success": False},
        {"nonce_used": False},
        {"transaction_hash": None},
        {"terms_hash": "f" * 64},
        {"kind": "unused", "nonce_used": False, "block_timestamp": NOW + 60},
    ],
)
def test_inconclusive_or_mismatched_fixture_cannot_release_exposure(offline, kwargs):
    store, signer, dispatcher, gate = offline
    run_gate(gate)
    with pytest.raises(GateDenied):
        store.apply_fixture("task", evidence_for(store, **kwargs), NOW + 100)
    assert store.report("task")["budget"] == {"budget": 30_000, "reserved": 10_000, "spent": 0}
    assert_counts(signer, dispatcher, (1, 1))


@pytest.mark.parametrize("fails", [False, True])
def test_no_signature_or_replayable_payload_in_db_wal_audit_or_logs(offline, caplog, capsys, fails):
    store, signer, dispatcher, gate = offline
    if fails:

        async def dispatch(**kwargs):
            dispatcher.calls.append(kwargs)
            raise RuntimeError(MARKER + kwargs["payment_header"])

        dispatcher.dispatch = dispatch
    # Hold a reader open so the test really inspects a live WAL, not only checkpointed DB bytes.
    with store.db.connect() as reader:
        reader.execute("BEGIN")
        reader.execute("SELECT * FROM grants").fetchall()
        report = run_gate(gate)
        payload = dispatcher.calls[0]["payment_header"]
        wal = store.db.path.with_name(store.db.path.name + "-wal")
        assert wal.is_file() and wal.stat().st_size > 0
        disk = store.db.path.read_bytes() + wal.read_bytes()
        for forbidden in (MARKER, payload, '"signature"', '"payload"'):
            assert forbidden.encode() not in disk
            assert forbidden not in json.dumps(report)
        assert "nonce" in record(store)["terms"]  # Non-secret recovery fields survive.
    output = capsys.readouterr()
    assert MARKER not in output.out + output.err + caplog.text
    assert payload not in output.out + output.err + caplog.text


def test_missing_task_binding_or_changed_intent_cannot_create_replacement(offline):
    store, signer, dispatcher, gate = offline
    run_gate(gate)
    original = record(store)
    changed = PurchaseRequest(service_id="test-report", subject="changed", purpose="research")
    with pytest.raises(PurchaseConflict):
        store.create_task("task", changed, NOW)
    with store.db.transaction() as db:
        db.execute(
            "DROP TRIGGER x402_tasks_no_delete"
        )  # Simulate damaged metadata, not application API.
        db.execute("DELETE FROM x402_tasks")
    with pytest.raises(GateDenied):
        run_gate(gate)
    with pytest.raises(PurchaseConflict):
        store.create_task("task", changed, NOW)
    with pytest.raises(SchemaError):
        OfflineGateStore(store.db.path, store.policy)
    assert record(store) == original
    assert_counts(signer, dispatcher, (1, 1))


def test_cancel_and_dispatch_authorization_race_serializes(offline):
    store, signer, dispatcher, _ = offline
    owner, terms = store.claim("task", digest(wire_data()), NOW)
    barrier = threading.Barrier(2)

    def dispatch_right():
        barrier.wait()
        return store.authorize_dispatch(owner, terms, NOW)

    def cancel():
        barrier.wait()
        store.cancel("task", NOW)

    with ThreadPoolExecutor(max_workers=2) as pool:
        right = pool.submit(dispatch_right)
        canceled = pool.submit(cancel)
        won = right.result()
        canceled.result()
    phase = store.report("task")["phase"]
    assert phase == ("reconciliation_required" if won else "canceled_before_dispatch")
    assert not store.authorize_dispatch(owner, terms, NOW)
    assert store.report("task")["budget"]["reserved"] == 10_000
    assert_counts(signer, dispatcher, (0, 0))


@pytest.mark.parametrize("time", [NOW - 2, NOW + 600])
def test_grant_window_rejected_before_signing(offline, time):
    store, signer, dispatcher, gate = offline
    gate.clock = lambda: time
    with pytest.raises(GateDenied):
        run_gate(gate)
    assert_counts(signer, dispatcher, (0, 0))
    assert store.report("task")["budget"]["reserved"] == 0


@pytest.mark.parametrize(
    "field,value",
    [
        ("amount", 1),
        ("network", "eip155:8453"),
        ("asset", "SIM-USDC"),
        ("recipient", PAYER),
        ("payer", RECIPIENT),
        ("valid_after", NOW),
        ("valid_before", NOW + 601),
        ("nonce", "malformed"),
        ("domain_version", "1"),
        ("requirements_hash", "f" * 64),
    ],
)
def test_altered_signing_request_is_rejected_before_signer(offline, field, value):
    store, signer, dispatcher, gate = offline
    original = store.claim

    def corrupt(*args):
        owner, terms = original(*args)
        return owner, terms.model_copy(update={field: value})

    store.claim = corrupt
    report = run_gate(gate)
    assert_counts(signer, dispatcher, (0, 0))
    assert report["reconciliation_required"]


def test_failed_signer_never_implies_dispatch_and_error_text_is_discarded(offline, caplog):
    store, signer, dispatcher, gate = offline

    async def fails(terms):
        signer.calls.append(terms)
        raise RuntimeError(MARKER)

    signer.sign = fails
    report = run_gate(gate)
    assert not report["dispatch_may_have_occurred"]
    store.cancel("task", NOW)
    assert store.report("task")["reason"] == "canceled_before_dispatch"
    assert MARKER not in store.db.path.read_bytes().decode(errors="replace") + caplog.text
    assert_counts(signer, dispatcher, (1, 0))


def test_fixture_accounting_failure_rolls_back_financial_transition(offline, monkeypatch):
    store, signer, dispatcher, gate = offline
    run_gate(gate)
    evidence = evidence_for(store)
    deny_sql(
        monkeypatch,
        store,
        lambda action, a, b: (
            action == sqlite3.SQLITE_UPDATE and a == "x402_attempts" and b == "phase"
        ),
    )
    with pytest.raises(sqlite3.DatabaseError):
        store.apply_fixture("task", evidence, NOW + 100)
    monkeypatch.undo()
    assert store.report("task")["budget"] == {"budget": 30_000, "reserved": 10_000, "spent": 0}
    store.apply_fixture("task", evidence, NOW + 100)
    assert store.report("task")["budget"] == {"budget": 30_000, "reserved": 0, "spent": 10_000}
    assert_counts(signer, dispatcher, (1, 1))


def test_cancel_and_conclusive_fixture_cannot_double_release_or_downgrade(offline):
    store, signer, dispatcher, gate = offline
    run_gate(gate)
    evidence = evidence_for(store)
    barrier = threading.Barrier(2)

    def cancel():
        barrier.wait()
        store.cancel("task", NOW + 100)

    def settle():
        barrier.wait()
        store.apply_fixture("task", evidence, NOW + 100)

    with ThreadPoolExecutor(max_workers=2) as pool:
        canceled, settled = pool.submit(cancel), pool.submit(settle)
        canceled.result()
        settled.result()
    report = run_gate(gate)
    assert report["phase"] == "fixture_settled"
    assert report["budget"] == {"budget": 30_000, "reserved": 0, "spent": 10_000}
    assert_counts(signer, dispatcher, (1, 1))


def test_only_explicit_doubles_and_noncryptographic_markers_are_accepted(offline):
    store, signer, dispatcher, _ = offline
    signer.mode = "live"
    with pytest.raises(GateDenied):
        OfflineGate(store, signer, dispatcher, lambda: NOW)
    with pytest.raises(GateDenied):
        OfflineSignature("0x" + "00" * 65)
    assert_counts(signer, dispatcher, (0, 0))
