"""Regressions for block identity coherence and per-invocation reporting."""

import asyncio

import pytest
from evidence_support import (
    BLOCK_HASH,
    HEAD_HASH,
    evidence_data,
    paid,
    reconcile,
    reconciler,
    source_for,
    terminal_events,
    track_dispatches,
)
from merchant_support import scenario
from test_x402_gate import NOW, record

from agentic_payments_lab.x402_store import OfflineGateStore


@pytest.fixture
def shop(tmp_path, monkeypatch):
    result = scenario(tmp_path)
    result.dispatch_entries = track_dispatches(monkeypatch)
    paid(result)
    return result


def assert_no_more_execution(shop):
    # counts() queries the independent settlement authority's SQLite records.
    assert shop.authority.counts() == (1, 1, 10_000)
    assert len(shop.signer.calls) == 1
    assert shop.dispatch_entries == ["dispatch"]


@pytest.mark.parametrize(
    "height,block_hash,head_offset,accepted",
    [
        pytest.param(90, BLOCK_HASH, 2, True, id="distinct-ancestor"),
        pytest.param(100, HEAD_HASH, 1, True, id="identical-block"),
        pytest.param(90, HEAD_HASH, 1, False, id="same-hash-other-height"),
        pytest.param(90, HEAD_HASH, 2, False, id="same-hash-other-height-and-time"),
        pytest.param(100, BLOCK_HASH, 1, False, id="same-height-other-hash"),
        pytest.param(100, HEAD_HASH, 2, False, id="same-hash-and-height-other-time"),
    ],
)
def test_candidate_and_head_must_agree_when_hash_or_height_matches(
    shop, height, block_hash, head_offset, accepted
):
    data = evidence_data(shop.buyer.reconciliation_terms("task"))
    for name in ("finalized_head", "finalized_head_check"):
        data[name]["timestamp"] = NOW + head_offset
    data["canonical_block"]["block"].update(number=height, hash=block_hash)
    for entry in [
        data["receipt"],
        *data["receipt"]["logs"],
        *data["authorization_events"]["events"],
    ]:
        entry.update(block_number=height, block_hash=block_hash)

    report, source = reconcile(shop.buyer, data)
    assert len(source.calls) == 8
    assert report["budget"] == {
        "budget": 30_000,
        "reserved": 0 if accepted else 10_000,
        "spent": 10_000 if accepted else 0,
    }
    assert terminal_events(shop.buyer) == int(accepted)
    last = report["reconciliation"]["last_observation"]
    assert last["outcome"] == ("transfer" if accepted else "incomplete")
    assert last["reason"] == ("validated_offline_records" if accepted else "transfer_mismatched")
    assert not report["reconciliation"]["operator_incident"]
    assert_no_more_execution(shop)


@pytest.mark.parametrize("kind", ["transfer", "unused"])
@pytest.mark.parametrize("height_delta", [-1, 1])
def test_accepted_hash_cannot_reappear_at_a_different_head_height(tmp_path, kind, height_delta):
    shop = scenario(tmp_path)
    if kind == "transfer":
        paid(shop)
    else:
        shop.payment()  # Reserve only; no authority execution.
    before, _ = reconcile(shop.buyer, unused=kind == "unused")
    terms = shop.buyer.reconciliation_terms("task")
    original_evidence = record(shop.buyer)["evidence"]
    counts = shop.authority.counts()
    data = evidence_data(terms, unused=kind == "unused")
    prior = data["canonical_block"]["block"] if kind == "transfer" else data["finalized_head"]
    head = {**prior, "number": prior["number"] + height_delta}
    for name in ("finalized_head", "finalized_head_check"):
        data[name] = head.copy()
    data["nonce_state"].update(block_number=head["number"], block_hash=head["hash"])
    data["authorization_events"].update(to_block=head["number"], finalized_head_hash=head["hash"])
    data["canonical_block"]["finalized_head_hash"] = head["hash"]

    report, _ = reconcile(shop.buyer, data)
    assert report["reconciliation"]["operator_incident"]
    assert report["reconciliation"]["last_observation"]["reason"] == "finality_contradiction"
    assert report["budget"] == before["budget"]
    assert report["phase"] == before["phase"]
    assert record(shop.buyer)["evidence"] == original_evidence
    assert terminal_events(shop.buyer) == 1
    reopened = OfflineGateStore(shop.buyer.db.path, shop.buyer.policy)
    assert reopened.report("task")["reconciliation"]["operator_incident"]
    assert shop.authority.counts() == counts


@pytest.mark.parametrize("height", [89, 91])
def test_accepted_hash_cannot_reappear_at_a_different_canonical_height(shop, height):
    before, _ = reconcile(shop.buyer)
    original_evidence = record(shop.buyer)["evidence"]
    data = evidence_data(shop.buyer.reconciliation_terms("task"))
    # Like a canonical reorg response, this contradicts the accepted receipt;
    # it is not sufficient for a new transfer conclusion.
    data["canonical_block"]["block"]["number"] = height
    report, _ = reconcile(shop.buyer, data)
    assert report["reconciliation"]["operator_incident"]
    assert report["budget"] == before["budget"]
    assert record(shop.buyer)["evidence"] == original_evidence
    assert terminal_events(shop.buyer) == 1
    assert_no_more_execution(shop)


def test_replayed_observation_reports_current_invocation_without_duplicate_audit(shop):
    valid = evidence_data(shop.buyer.reconciliation_terms("task"))
    first, first_source = reconcile(shop.buyer, valid)
    missing = dict(valid)
    del missing["nonce_state"]
    failed, failed_source = reconcile(shop.buyer, missing)
    persisted = shop.buyer.report("task")
    reopened = OfflineGateStore(shop.buyer.db.path, shop.buyer.policy)
    repeated, repeated_source = reconcile(reopened, valid)

    assert [len(s.calls) for s in (first_source, failed_source, repeated_source)] == [8, 3, 8]
    current = repeated["current_invocation"]
    assert current == first["current_invocation"]
    assert current["outcome"] == "transfer"
    assert current["reason"] == "validated_offline_records"
    assert current["reads_attempted"] == current["read_limit"] == 8
    assert failed["current_invocation"]["outcome"] == "incomplete"
    assert failed["current_invocation"]["reason"] == "unavailable"
    assert failed["current_invocation"]["reads_attempted"] == 3
    assert repeated["reconciliation"]["last_observation"] == failed["current_invocation"]
    assert repeated["reconciliation"]["observation_count"] == 2
    # The durable snapshot and its event order are unchanged by the replay.
    assert reopened.report("task") == persisted
    assert {k: v for k, v in repeated.items() if k != "current_invocation"} == persisted
    assert "current_invocation" not in persisted
    assert terminal_events(reopened) == 1
    assert_no_more_execution(shop)


def test_current_invocation_is_not_replaced_by_an_intervening_persisted_observation(
    shop, monkeypatch
):
    data = evidence_data(shop.buyer.reconciliation_terms("task"))
    del data["nonce_state"]
    runner = reconciler(shop.buyer, source=source_for(data))
    failed = asyncio.run(runner.observer.observe(shop.buyer.reconciliation_terms("task")))
    original_report = shop.buyer.report

    def intervening_report(task_id):
        # Deterministically schedule another completed operator observation after
        # this invocation's commit and before its separate report snapshot.
        shop.buyer.record_observation(task_id, failed, NOW + 100)
        return original_report(task_id)

    monkeypatch.setattr(shop.buyer, "report", intervening_report)
    report, source = reconcile(shop.buyer)
    assert len(source.calls) == 8
    assert report["current_invocation"]["outcome"] == "transfer"
    assert report["current_invocation"]["reads_attempted"] == 8
    assert report["reconciliation"]["last_observation"]["reason"] == "unavailable"
    assert report["reconciliation"]["last_observation"]["reads_attempted"] == 3
    assert report["reconciliation"]["observation_count"] == 2
    assert terminal_events(shop.buyer) == 1
    assert_no_more_execution(shop)


def test_current_invocation_uses_the_committed_incident_decision(shop):
    reconcile(shop.buyer)
    incident, _ = reconcile(shop.buyer, unused=True)
    assert incident["current_invocation"]["outcome"] == "incident"
    assert incident["current_invocation"]["reason"] == "finality_contradiction"
    frozen, _ = reconcile(shop.buyer)
    assert frozen["current_invocation"]["outcome"] == "incident"
    assert frozen["current_invocation"]["reason"] == "profile_frozen"
    assert frozen["current_invocation"]["reads_attempted"] == 8
    assert frozen["budget"] == incident["budget"]
    assert terminal_events(shop.buyer) == 1
    assert_no_more_execution(shop)
