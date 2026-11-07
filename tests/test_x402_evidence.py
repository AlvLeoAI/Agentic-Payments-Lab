import asyncio
import copy
import json
import sqlite3
import traceback
from contextlib import contextmanager

import pytest
from evidence_support import (
    HEAD_HASH,
    OTHER_HASH,
    TX_HASH,
    evidence_data,
    paid,
    reconcile,
    reconciler,
    source_for,
    terminal_events,
    track_dispatches,
)
from merchant_support import scenario
from pydantic import ValidationError
from test_x402_gate import MARKER, NOW, record

from agentic_payments_lab.domain import PurchaseRequest, digest
from agentic_payments_lab.x402_adapter.observer import FixtureSource, OfflineEvidenceObserver
from agentic_payments_lab.x402_contracts import GateDenied
from agentic_payments_lab.x402_evidence import Observation, ObservationLimits
from agentic_payments_lab.x402_reconciliation import ReconciliationUnavailable
from agentic_payments_lab.x402_store import OfflineGateStore


@pytest.fixture
def shop(tmp_path, monkeypatch):
    shop = scenario(tmp_path)
    shop.dispatch_entries = track_dispatches(monkeypatch)
    paid(shop)
    assert shop.authority.counts() == (1, 1, 10_000)
    return shop


def assert_reserved(store):
    report = store.report("task")
    assert report["budget"] == {"budget": 30_000, "reserved": 10_000, "spent": 0}
    assert terminal_events(store) == 0
    return report


def test_transfer_response_lost_then_explicit_reconciliation_accounts_once(tmp_path):
    shop = scenario(tmp_path)
    dispatches = paid(shop, lose_response=True)
    assert_reserved(shop.buyer)
    assert shop.authority.counts() == (1, 1, 10_000)
    before = record(shop.buyer)
    report, source = reconcile(shop.buyer)
    assert len(source.calls) == 8
    assert [method for method, _ in source.calls] == [
        "profile",
        "finalized_head",
        "nonce_state",
        "authorization_events",
        "transaction",
        "receipt",
        "canonical_block",
        "finalized_head_check",
    ]
    assert source.calls[2][1] == {
        "asset": shop.buyer.policy.asset,
        "payer": shop.buyer.policy.payer,
        "nonce": before["nonce"],
        "block_hash": HEAD_HASH,
    }
    assert report["phase"] == "fixture_settled"
    assert report["budget"] == {"budget": 30_000, "reserved": 0, "spent": 10_000}
    assert report["delivery_state"] == "not_requested"
    assert report["blockchain_settlement_verified"] is False
    assert report["reconciliation"]["last_observation"]["outcome"] == "transfer"
    assert json.loads(record(shop.buyer)["evidence"])["transaction_hash"] == TX_HASH
    assert terminal_events(shop.buyer) == 1
    reopened = OfflineGateStore(shop.buyer.db.path, shop.buyer.policy)
    assert reconcile(reopened)[0] == report
    asyncio.run(shop.run())  # Existing purchase remains a status-only operation.
    assert len(shop.signer.calls) == len(dispatches) == 1
    assert shop.authority.counts() == (1, 1, 10_000)
    assert record(reopened)["nonce"] == before["nonce"]


@pytest.mark.parametrize(
    "stamp,expected", [(NOW + 59, "incomplete"), (NOW + 60, "incomplete"), (NOW + 61, "unused")]
)
def test_expiry_requires_finalized_nonuse_after_deadline(tmp_path, stamp, expected):
    shop = scenario(tmp_path)
    shop.payment()  # Claimed; no settlement or transfer in the authority.
    data = evidence_data(shop.buyer.reconciliation_terms("task"), unused=True)
    data["finalized_head"]["timestamp"] = data["finalized_head_check"]["timestamp"] = stamp
    report, source = reconcile(shop.buyer, data)
    assert len(source.calls) == 4
    assert report["reconciliation"]["last_observation"]["outcome"] == expected
    if expected == "unused":
        assert report["budget"]["spent"] == report["budget"]["reserved"] == 0
        assert terminal_events(shop.buyer) == 1
        assert report["phase"] == "fixture_unused"
    else:
        assert_reserved(shop.buyer)
    assert shop.authority.counts() == (0, 0, 0)


@pytest.mark.parametrize(
    "path,value",
    [
        (("profile", "source"), "rpc"),
        (("profile", "network"), "eip155:8453"),
        (("profile", "asset"), "0x" + "00" * 20),
        (("profile", "chain_id"), "84532"),
        (("profile", "chain_id"), 84532.0),
        (("profile", "domain_name"), "Other"),
        (("profile", "domain_version"), "1"),
        (("finalized_head", "timestamp"), NOW + 101),
        (("finalized_head", "number"), True),
        (("finalized_head_check", "hash"), OTHER_HASH),
        (("nonce_state", "block_hash"), OTHER_HASH),
        (("nonce_state", "block_number"), 99),
        (("nonce_state", "payer"), "0x" + "33" * 20),
        (("nonce_state", "nonce"), OTHER_HASH),
        (("nonce_state", "asset"), "0x" + "33" * 20),
        (("nonce_state", "used"), 1),
        (("authorization_events", "from_block"), 1),
        (("authorization_events", "to_block"), 99),
        (("authorization_events", "complete"), False),
        (("authorization_events", "finalized_head_hash"), OTHER_HASH),
        (("authorization_events", "events", 0, "event"), "AuthorizationCanceled"),
        (("authorization_events", "events", 0, "payer"), "0x" + "33" * 20),
        (("authorization_events", "events", 0, "nonce"), OTHER_HASH),
        (("authorization_events", "events", 0, "transaction_hash"), OTHER_HASH),
        (("authorization_events", "events", 0, "block_hash"), OTHER_HASH),
        (("transaction", "hash"), OTHER_HASH),
        (("transaction", "to"), "0x" + "33" * 20),
        (("transaction", "call", "method"), "receiveWithAuthorization"),
        (("transaction", "call", "payer"), "0x" + "33" * 20),
        (("transaction", "call", "recipient"), "0x" + "33" * 20),
        (("transaction", "call", "amount"), 9999),
        (("transaction", "call", "amount"), "10000"),
        (("transaction", "call", "valid_after"), 1),
        (("transaction", "call", "valid_before"), NOW + 61),
        (("transaction", "call", "nonce"), OTHER_HASH),
        (("receipt", "success"), False),
        (("receipt", "transaction_hash"), OTHER_HASH),
        (("receipt", "block_hash"), OTHER_HASH),
        (("receipt", "block_number"), 91),
        (("receipt", "logs", 0, "log_index"), 8),
        (("receipt", "logs", 1, "amount"), 10001),
        (("receipt", "logs", 1, "amount"), 10000.0),
        (("receipt", "logs", 1, "amount"), True),
        (("receipt", "logs", 1, "asset"), "0x" + "33" * 20),
        (("receipt", "logs", 1, "payer"), "0x" + "33" * 20),
        (("receipt", "logs", 1, "recipient"), "0x" + "33" * 20),
        (("receipt", "logs", 1, "transaction_hash"), OTHER_HASH),
        (("receipt", "logs", 1, "block_number"), 91),
        (("canonical_block", "block", "hash"), OTHER_HASH),
        (("canonical_block", "block", "number"), 101),
        (("canonical_block", "block", "timestamp"), NOW + 60),
        (("canonical_block", "finalized_head_hash"), OTHER_HASH),
    ],
)
def test_substituted_or_coerced_evidence_never_changes_accounting(shop, path, value):
    data = evidence_data(shop.buyer.reconciliation_terms("task"))
    item = data
    for key in path[:-1]:
        item = item[key]
    item[path[-1]] = value
    report, source = reconcile(shop.buyer, data)
    assert len(source.calls) <= 8
    assert report["reconciliation"]["last_observation"]["outcome"] == "incomplete"
    assert not report["reconciliation"]["operator_incident"]
    assert_reserved(shop.buyer)
    assert shop.authority.counts() == (1, 1, 10_000)


@pytest.mark.parametrize(
    "case",
    [
        "no_events",
        "duplicate_events",
        "missing_transfer",
        "duplicate_transfer",
        "extra_signature",
        "merchant_success",
        "nonce_flag_only",
    ],
)
def test_missing_ambiguous_or_merchant_only_records_are_not_proof(shop, case):
    data = evidence_data(shop.buyer.reconciliation_terms("task"))
    if case == "no_events":
        data["authorization_events"]["events"] = []
    elif case == "duplicate_events":
        data["authorization_events"]["events"] *= 2
    elif case == "missing_transfer":
        data["receipt"]["logs"].pop()
    elif case == "duplicate_transfer":
        data["receipt"]["logs"].append(copy.deepcopy(data["receipt"]["logs"][-1]))
    elif case == "extra_signature":
        data["transaction"]["call"]["signature"] = MARKER
    elif case == "merchant_success":
        data["receipt"] = {"success": True, "transaction": TX_HASH}
    elif case == "nonce_flag_only":
        del data["receipt"]
    reconcile(shop.buyer, data)
    assert_reserved(shop.buyer)


@pytest.mark.parametrize(
    "raw",
    [
        b'{"chain_id":84532,"chain_id":84532}',
        b"null",
        b"[]",
        b"NaN",
        b"\xff",
        b"{" * 2000,
        b" " * 16385,
    ],
)
def test_malformed_fixture_is_bounded_and_sanitized(shop, raw):
    source = FixtureSource({"profile": raw})
    report, _ = reconcile(shop.buyer, source=source)
    assert len(source.calls) == 1
    assert report["reconciliation"]["last_observation"]["reason"] == "invalid_fixture"
    assert_reserved(shop.buyer)


@pytest.mark.parametrize("limit", [1, 3, 7, 8])
def test_actual_read_attempt_ceiling_no_retry(shop, limit):
    report, source = reconcile(shop.buyer, limits=ObservationLimits(max_reads=limit))
    assert len(source.calls) == limit
    last = report["reconciliation"]["last_observation"]
    assert last["reads_attempted"] == limit
    if limit < 8:
        assert last["reason"] == "read_limit"
        assert_reserved(shop.buyer)
    else:
        assert report["phase"] == "fixture_settled"


@pytest.mark.parametrize(
    "limits",
    [
        {"max_reads": 9},
        {"max_reads": True},
        {"timeout_seconds": 31},
        {"to_block": 1000},
        {"from_block": 2, "to_block": 1},
    ],
)
def test_invalid_limits_rejected(limits):
    with pytest.raises(ValidationError):
        ObservationLimits(**limits)


def test_no_live_source_or_unbounded_range_fallback(shop):
    with pytest.raises(GateDenied, match="offline_fixture_source_required"):
        OfflineEvidenceObserver(object())
    data = evidence_data(shop.buyer.reconciliation_terms("task"))
    data["authorization_events"]["events"] = []
    data["authorization_events"]["from_block"] = 91
    report, source = reconcile(
        shop.buyer, data, limits=ObservationLimits(from_block=91, to_block=100)
    )
    assert source.calls[3][1]["from_block"] == 91
    assert source.calls[3][1]["to_block"] == 100
    assert len(source.calls) == 5
    assert report["reconciliation"]["last_observation"]["outcome"] == "incomplete"
    assert_reserved(shop.buyer)


def test_100_concurrent_reconciliations_one_terminal_transition(shop):
    source = source_for(evidence_data(shop.buyer.reconciliation_terms("task")))
    original = source.read

    async def concurrent():
        ready = asyncio.Event()
        at_checkpoint = 0

        async def held(method, **selectors):
            nonlocal at_checkpoint
            value = await original(method, **selectors)
            if method == "finalized_head_check":
                at_checkpoint += 1
                if at_checkpoint == 100:
                    ready.set()
                await asyncio.wait_for(ready.wait(), 5)
            return value

        source.read = held
        runner = reconciler(shop.buyer, source=source)
        results = await asyncio.wait_for(
            asyncio.gather(*(runner.reconcile("task") for _ in range(100))), 10
        )
        assert at_checkpoint == 100
        assert all(r["phase"] == "fixture_settled" for r in results)

    asyncio.run(concurrent())
    assert len(source.calls) == 800
    assert terminal_events(shop.buyer) == 1
    assert shop.buyer.report("task")["reconciliation"]["observation_count"] == 1
    assert len(shop.signer.calls) == 1
    assert shop.dispatch_entries == ["dispatch"]
    assert shop.authority.counts() == (1, 1, 10_000)


def test_missing_task_claim_or_cross_task_observation_cannot_rebind(shop):
    runner = reconciler(shop.buyer)
    with pytest.raises(GateDenied):
        asyncio.run(runner.reconcile("missing"))
    request = PurchaseRequest(service_id="test-report", subject="different", purpose="research")
    shop.buyer.create_task("other", request, NOW)
    with pytest.raises(GateDenied):
        asyncio.run(runner.reconcile("other"))
    assert runner.observer.source.calls == []
    shop.buyer.claim("other", digest({"different": True}), NOW)
    observation = asyncio.run(runner.observer.observe(shop.buyer.reconciliation_terms("task")))
    with pytest.raises(GateDenied, match="observation_identity_mismatch"):
        shop.buyer.record_observation("other", observation, NOW + 100)
    assert terminal_events(shop.buyer) == 0
    with shop.buyer.db.connect() as db:
        assert db.execute("SELECT count(*) FROM tasks").fetchone()[0] == 2
        assert db.execute("SELECT count(*) FROM x402_attempts").fetchone()[0] == 2


def test_forged_observation_model_is_revalidated(shop):
    runner = reconciler(shop.buyer)
    observation = asyncio.run(runner.observer.observe(shop.buyer.reconciliation_terms("task")))
    raw = observation.model_dump(mode="json")
    raw["receipt"]["success"] = "true"
    forged = Observation.model_construct(**raw)
    with pytest.raises(GateDenied, match="invalid_offline_observation"):
        shop.buyer.record_observation("task", forged, NOW + 100)
    assert_reserved(shop.buyer)


@pytest.mark.parametrize("conflict", ["unused", "reorg", "another_transfer", "changed_timestamp"])
def test_post_finality_contradiction_latches_incident_without_rollback(shop, conflict):
    reconcile(shop.buyer)
    original_evidence = record(shop.buyer)["evidence"]
    data = evidence_data(shop.buyer.reconciliation_terms("task"), unused=conflict == "unused")
    if conflict == "reorg":
        data["canonical_block"]["block"]["hash"] = OTHER_HASH
    elif conflict == "changed_timestamp":
        data["canonical_block"]["block"]["timestamp"] = NOW + 2
    elif conflict == "another_transfer":
        data["transaction"]["hash"] = OTHER_HASH
        data["receipt"]["transaction_hash"] = OTHER_HASH
        for entry in [*data["authorization_events"]["events"], *data["receipt"]["logs"]]:
            entry["transaction_hash"] = OTHER_HASH
    report, _ = reconcile(shop.buyer, data)
    assert report["phase"] == "fixture_settled"
    assert report["budget"]["spent"] == 10_000 and report["budget"]["reserved"] == 0
    assert report["reconciliation"]["operator_incident"]
    assert record(shop.buyer)["evidence"] == original_evidence
    assert terminal_events(shop.buyer) == 1
    request = PurchaseRequest(service_id="test-report", subject="new", purpose="research")
    shop.buyer.create_task("new", request, NOW)
    with pytest.raises(GateDenied, match="operator_review_required"):
        shop.buyer.claim("new", digest({}), NOW)
    reopened = OfflineGateStore(shop.buyer.db.path, shop.buyer.policy)
    reconcile(reopened)
    assert reopened.report("task")["reconciliation"]["operator_incident"]
    with reopened.db.connect() as db:
        assert (
            db.execute(
                "SELECT count(*) FROM events WHERE event_type='offline_reconciliation_incident'"
            ).fetchone()[0]
            == 1
        )


def test_pending_and_stale_observations_cannot_downgrade_terminal_state(shop):
    report, _ = reconcile(shop.buyer)
    data = evidence_data(shop.buyer.reconciliation_terms("task"), unused=True)
    for name in ("finalized_head", "finalized_head_check"):
        data[name] = {"number": 80, "hash": OTHER_HASH, "timestamp": NOW}
    data["nonce_state"].update(block_number=80, block_hash=OTHER_HASH)
    stale, _ = reconcile(shop.buyer, data)
    assert stale["budget"] == report["budget"]
    assert not stale["reconciliation"]["operator_incident"]
    reconcile(shop.buyer, source=FixtureSource({}))
    assert terminal_events(shop.buyer) == 1


def test_late_cancellation_flag_is_not_itself_proof_of_transfer_or_contradiction(tmp_path):
    shop = scenario(tmp_path)
    shop.payment()
    reconcile(shop.buyer, unused=True)
    data = evidence_data(shop.buyer.reconciliation_terms("task"))
    data["authorization_events"]["events"][0]["event"] = "AuthorizationCanceled"
    data["receipt"]["logs"][0]["event"] = "AuthorizationCanceled"
    report, _ = reconcile(shop.buyer, data)
    assert report["phase"] == "fixture_unused"
    assert not report["reconciliation"]["operator_incident"]
    assert terminal_events(shop.buyer) == 1


def test_nonuse_finalized_checkpoint_cannot_silently_change_on_replay(tmp_path):
    shop = scenario(tmp_path)
    shop.payment()
    reconcile(shop.buyer, unused=True)
    data = evidence_data(shop.buyer.reconciliation_terms("task"), unused=True)
    for name in ("finalized_head", "finalized_head_check"):
        data[name]["hash"] = OTHER_HASH
    data["nonce_state"]["block_hash"] = OTHER_HASH
    report, _ = reconcile(shop.buyer, data)
    assert report["phase"] == "fixture_unused"
    assert report["reconciliation"]["operator_incident"]
    assert terminal_events(shop.buyer) == 1


@pytest.mark.parametrize("swallow", [False, True])
def test_cancellation_preserves_partial_observation_and_finance(shop, swallow):
    source = source_for(evidence_data(shop.buyer.reconciliation_terms("task")))
    original = source.read

    async def run():
        entered = asyncio.Event()

        async def blocked(method, **selectors):
            value = await original(method, **selectors)
            if method == "receipt":
                entered.set()
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    if not swallow:
                        raise asyncio.CancelledError(MARKER) from None
            return value

        source.read = blocked
        task = asyncio.create_task(reconciler(shop.buyer, source=source).reconcile("task"))
        await asyncio.wait_for(entered.wait(), 5)
        task.cancel(MARKER)
        with pytest.raises(asyncio.CancelledError) as error:
            await task
        assert MARKER not in "".join(traceback.format_exception(error.value))

    asyncio.run(run())
    report = assert_reserved(shop.buyer)
    assert len(source.calls) == 6
    assert report["reconciliation"]["last_observation"]["reason"] == "canceled"
    assert report["reconciliation"]["last_observation"]["reads_attempted"] == 6
    assert shop.authority.counts() == (1, 1, 10_000)
    reopened = OfflineGateStore(shop.buyer.db.path, shop.buyer.policy)
    reconcile(reopened)
    assert terminal_events(reopened) == 1
    assert shop.authority.counts() == (1, 1, 10_000)


def test_cooperative_timeout_keeps_reservation(shop):
    source = source_for(evidence_data(shop.buyer.reconciliation_terms("task")))
    original = source.read

    async def stall(method, **selectors):
        await original(method, **selectors)
        await asyncio.Event().wait()

    source.read = stall
    report, _ = reconcile(shop.buyer, source=source, limits=ObservationLimits(timeout_seconds=1))
    assert len(source.calls) == 1
    assert report["reconciliation"]["last_observation"]["reason"] == "timeout"
    assert_reserved(shop.buyer)


def test_source_and_storage_exception_markers_are_not_retained(shop, monkeypatch, caplog):
    source = source_for(evidence_data(shop.buyer.reconciliation_terms("task")))

    async def raises(*args, **kwargs):
        error = RuntimeError(MARKER)
        error.add_note(MARKER)
        raise error

    source.read = raises
    # Keep a WAL reader alive while examining the actual database and WAL bytes.
    with shop.buyer.db.connect() as reader:
        reader.execute("BEGIN")
        reader.execute("SELECT * FROM events").fetchall()
        report, _ = reconcile(shop.buyer, source=source)
        assert report["reconciliation"]["last_observation"]["reason"] == "unavailable"
        for path in shop.buyer.db.path.parent.glob("buyer.sqlite3*"):
            assert MARKER.encode() not in path.read_bytes()
        assert MARKER not in json.dumps(report) + caplog.text
    original = shop.buyer.db.transaction

    @contextmanager
    def fails():
        with original() as db:
            yield db
            raise sqlite3.OperationalError(MARKER)

    monkeypatch.setattr(shop.buyer.db, "transaction", fails)
    with pytest.raises(ReconciliationUnavailable) as error:
        reconcile(shop.buyer)
    assert MARKER not in "".join(traceback.format_exception(error.value))
    assert_reserved(shop.buyer)
