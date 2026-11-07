import asyncio
import json
import sqlite3
from pathlib import Path
from uuid import UUID

import pytest
from evidence_support import evidence_data, reconciler, source_for
from offline_buyer_support import configured, counters, drive, finance, quote, setup
from test_buyer_agent import SequenceModel

from agentic_payments_lab.buyer_agent.boundary import ARGUMENTS, Boundary
from agentic_payments_lab.buyer_agent.contracts import AgentReport, Settings
from agentic_payments_lab.buyer_agent.offline_x402 import OfflineBoundary, OfflineX402Buyer
from agentic_payments_lab.buyer_agent.runner import BoundedModel
from agentic_payments_lab.buyer_agent.scripted import ScriptedBuyer, final_response, tool_response
from agentic_payments_lab.buyer_agent.store import AgentStore
from agentic_payments_lab.domain import AgentMetadataError, PurchaseConflict
from agentic_payments_lab.x402_contracts import GateDenied
from agentic_payments_lab.x402_delivery import OfflineDeliveryAcceptor
from agentic_payments_lab.x402_store import OfflineGateStore


def test_scripted_bridge_and_separate_operator_actions(tmp_path, monkeypatch):
    shop, buyer, metadata, goal = setup(tmp_path)
    retrievals = []
    original = buyer.client.retrieve

    async def retrieve(purchase):
        retrievals.append(purchase)
        return await original(purchase)

    monkeypatch.setattr(buyer.client, "retrieve", retrieve)
    report = drive(buyer, metadata, goal)
    assert report.termination == "completed"
    assert report.model_usage.source == "scripted"
    assert (report.model_usage.requests_started, report.tool_attempts) == (5, 4)
    assert [e.tool for e in report.events] == [
        "get_purchase_status",
        "list_services",
        "get_quote",
        "purchase",
    ]
    assert report.purchase.payment_state == "reconciliation_required"
    assert report.purchase.delivery_state == "not_requested"
    assert report.purchase.payment_backend == "offline_x402"
    assert report.purchase.evidence_source == "offline_fixture"
    assert not report.purchase.blockchain_settlement_verified
    assert "simulated_receipt_id" not in report.purchase.model_dump()
    assert report.purchase.operator_reconciliation_required
    assert finance(shop) == (10_000, 0)
    assert counters(shop) == (1, 1, 1, 1, 10_000)
    assert not retrievals
    assert drive(buyer, metadata, goal).purchase == report.purchase

    # The model has finished. Only these separate, explicit operator calls settle
    # the fixture and accept delivery. Status/model continuations cannot do so.
    shop.now[0] += 120
    asyncio.run(reconciler(shop.buyer).reconcile("task"))
    settled = drive(buyer, metadata, goal)
    assert settled.purchase.payment_state == "fixture_settled"
    assert settled.purchase.delivery_state == "not_requested"
    assert not retrievals
    asyncio.run(
        OfflineDeliveryAcceptor(shop.buyer, buyer.client, lambda: shop.now[0]).accept("task")
    )
    accepted = drive(buyer, metadata, goal)
    assert accepted.purchase.delivery_state == "delivered"
    assert not accepted.purchase.operator_reconciliation_required
    assert len(retrievals) == 1
    assert counters(shop) == (1, 1, 1, 1, 10_000)
    assert finance(shop) == (0, 10_000)

    # An incident remains independently visible even after acceptance.
    data = evidence_data(shop.buyer.reconciliation_terms("task"), unused=True)
    asyncio.run(reconciler(shop.buyer, source=source_for(data)).reconcile("task"))
    frozen = drive(buyer, metadata, goal)
    assert frozen.purchase.operator_incident and frozen.purchase.operator_reconciliation_required
    assert frozen.purchase.delivery_state == "delivered"
    assert frozen.purchase.delivery_acceptance_hash == accepted.purchase.delivery_acceptance_hash
    assert frozen.purchase.spent_atomic == 10_000
    assert len(retrievals) == 1
    assert counters(shop) == (1, 1, 1, 1, 10_000)


def test_identical_four_schemas_and_sdk_bounds(tmp_path, runtime):
    from conftest import authorize
    from test_buyer_agent import GOAL

    _, buyer, metadata, goal = setup(tmp_path)
    authorize(runtime)
    sim_metadata = AgentStore(tmp_path / "sim-agent.sqlite3")
    sim_metadata.initialize(runtime.ledger, "task", GOAL)
    baseline = BoundedModel(
        ScriptedBuyer(), Boundary(runtime.buyer("task"), sim_metadata, GOAL), Settings(), "sim"
    ).tools()
    bounded = BoundedModel(ScriptedBuyer(), OfflineBoundary(buyer, metadata, goal), Settings(), "x")
    tools = bounded.tools()
    assert [t.name for t in tools] == list(ARGUMENTS)
    for tool, original in zip(tools, baseline, strict=True):
        assert tool.strict_json_schema
        assert tool.params_json_schema == original.params_json_schema
        assert tool.params_json_schema["additionalProperties"] is False


def test_simulator_report_serialization_is_exactly_the_accepted_baseline(
    runtime, tmp_path, monkeypatch
):
    from conftest import authorize
    from test_buyer_agent import GOAL

    from agentic_payments_lab.buyer_agent.runner import run_buyer

    # Generated under isolation from accepted 2fce3e6 with only run_id fixed.
    # Compare the WHOLE serialized report, including its union member and roundtrip.
    expected = (
        (Path(__file__).parent / "fixtures/phase3b4_simulator_agent_report.json")
        .read_text()
        .strip()
    )
    monkeypatch.setattr(
        "agentic_payments_lab.buyer_agent.runner.uuid4",
        lambda: UUID("00000000-0000-0000-0000-000000000001"),
    )
    authorize(runtime)
    metadata = AgentStore(tmp_path / "sim-agent.sqlite3")
    metadata.initialize(runtime.ledger, "task", GOAL)
    report = asyncio.run(run_buyer(runtime.buyer("task"), metadata, GOAL, Settings()))
    assert report.model_dump_json() == expected
    assert AgentReport.model_validate_json(expected).model_dump_json() == expected


@pytest.mark.parametrize(
    "field,value", [("subject", "new"), ("purpose", "other"), ("quality", "standard")]
)
def test_goal_changes_reject_before_model_and_effects(tmp_path, field, value):
    shop, buyer, metadata, goal = setup(tmp_path)
    model = SequenceModel([final_response("forged success")])
    with pytest.raises(PurchaseConflict):
        drive(buyer, metadata, goal.model_copy(update={field: value}), model=model)
    assert not model.inputs
    assert counters(shop) == (0, 0, 0, 0, 0)


def test_live_mode_cannot_activate_this_backend(tmp_path, monkeypatch):
    shop, buyer, metadata, goal = setup(tmp_path)
    monkeypatch.setattr(
        "agentic_payments_lab.buyer_agent.runner.live_client", lambda *_: pytest.fail("live")
    )
    with pytest.raises(ValueError, match="scripted"):
        drive(buyer, metadata, goal, settings=Settings(mode="live", model="gpt-6-luna"))
    assert counters(shop) == (0, 0, 0, 0, 0)


@pytest.mark.parametrize(
    "kind", ["fabricated", "cross_task", "cross_profile", "changed_request", "expired"]
)
def test_quote_handles_fail_closed(tmp_path, kind):
    shop, buyer, metadata, goal = setup(tmp_path)
    handle = quote(buyer, metadata, goal)
    if kind == "fabricated":
        handle = "x402-quote-fabricated"
    elif kind == "cross_task":
        shop.enroll("other")
        buyer, metadata, goal = configured(shop, tmp_path, task="other")
    elif kind in ("cross_profile", "changed_request"):
        with metadata.db.transaction() as db:
            if kind == "changed_request":
                db.execute("UPDATE quote_handles SET request=?", ("{}",))
            else:
                row = db.execute("SELECT quote FROM quote_handles").fetchone()
                data = json.loads(row[0])
                data["scope"]["policy_hash"] = "e" * 64
                db.execute("UPDATE quote_handles SET quote=?", (json.dumps(data),))
    else:
        shop.now[0] += shop.buyer.policy.max_timeout_seconds
    result = asyncio.run(
        OfflineBoundary(buyer, metadata, goal).invoke("purchase", json.dumps({"quote_id": handle}))
    )
    assert result.error == "invalid_quote_handle"
    assert counters(shop) == (0, 0, 0, 0, 0)
    assert finance(shop) == (0, 0)


def test_quote_binding_survives_restart_and_never_reenters_gate(tmp_path, monkeypatch):
    shop, buyer, metadata, goal = setup(tmp_path)
    handle = quote(buyer, metadata, goal)
    shop.buyer = OfflineGateStore(shop.buyer.db.path, shop.buyer.policy)
    buyer = OfflineX402Buyer(shop.buyer, buyer.client, buyer.gate.signer, buyer.clock, "task")
    metadata = AgentStore(metadata.db.path)
    boundary = OfflineBoundary(buyer, metadata, goal)
    initial = asyncio.run(boundary.invoke("purchase", json.dumps({"quote_id": handle})))
    monkeypatch.setattr(buyer.gate, "run", lambda *_args, **_kwargs: pytest.fail("gate replay"))
    shop.now[0] += 500
    replay = asyncio.run(boundary.invoke("purchase", json.dumps({"quote_id": handle})))
    assert replay == initial
    assert counters(shop) == (1, 1, 1, 1, 10_000)


@pytest.mark.parametrize("loss", ["row", "file"])
@pytest.mark.parametrize("paid", [False, True])
def test_metadata_loss_never_rebinds_and_recovery_uses_original_witness(tmp_path, loss, paid):
    shop, buyer, metadata, goal = setup(tmp_path)
    if paid:
        drive(buyer, metadata, goal)
    old_identity = buyer.context
    if loss == "row":
        with metadata.db.transaction() as db:
            db.execute("DELETE FROM bindings")
    else:
        metadata = AgentStore(tmp_path / "replacement-agent.sqlite3")
    for attempted in (goal, goal.model_copy(update={"subject": "new-goal"})):
        with pytest.raises((AgentMetadataError, PurchaseConflict)):
            drive(buyer, metadata, attempted)
        with pytest.raises((AgentMetadataError, PurchaseConflict)):
            buyer.initialize(metadata, attempted)
    buyer.recover(metadata)
    report = drive(buyer, metadata, goal)
    assert report.history_incomplete and buyer.context == old_identity
    assert counters(shop) == (1, 1, 1, 1, 10_000)


def test_interrupted_initial_binding_requires_explicit_recovery(tmp_path, monkeypatch):
    from merchant_support import scenario

    shop = scenario(tmp_path)
    buyer, metadata, goal = configured(shop, tmp_path, initialize=False)
    with pytest.raises(AgentMetadataError, match="initialization"):
        drive(buyer, metadata, goal)
    with monkeypatch.context() as patch:
        patch.setattr(
            metadata.db,
            "transaction",
            lambda: (_ for _ in ()).throw(sqlite3.OperationalError("fault")),
        )
        with pytest.raises(sqlite3.Error):
            buyer.initialize(metadata, goal)
    assert buyer.agent_binding("task") is not None
    with pytest.raises(AgentMetadataError, match="recovery"):
        drive(buyer, metadata, goal)
    buyer.recover(metadata)
    assert drive(buyer, metadata, goal).history_incomplete
    assert counters(shop) == (1, 1, 1, 1, 10_000)


@pytest.mark.parametrize(
    "name,args",
    [
        ("purchase", {"quote_id": "fake", "amount": 0}),
        ("get_quote", {"service_id": "test-report", "backend": "simulation"}),
        ("get_purchase_status", {"reconcile": True}),
        ("list_services", {"task_id": "other"}),
    ],
)
def test_model_cannot_change_authority_arguments(tmp_path, name, args):
    shop, buyer, metadata, goal = setup(tmp_path)
    report = drive(
        buyer,
        metadata,
        goal,
        model=SequenceModel(
            [tool_response(name, args, "attack"), final_response("Settled on the real blockchain")]
        ),
    )
    assert report.events[0].outcome == "invalid_arguments"
    assert report.tool_attempts == 1
    assert report.purchase.payment_state == "not_started"
    assert not report.purchase.blockchain_settlement_verified
    assert counters(shop) == (0, 0, 0, 0, 0)


@pytest.mark.parametrize(
    "name", ["reconcile", "accept", "cancel", "recover", "create_task", "sign"]
)
def test_operator_operations_are_not_tools(tmp_path, name):
    shop, buyer, metadata, goal = setup(tmp_path)
    report = drive(buyer, metadata, goal, model=SequenceModel([tool_response(name, {}, "attack")]))
    assert report.termination == "unavailable_tool"
    assert counters(shop) == (0, 0, 0, 0, 0)


@pytest.mark.parametrize("restriction", ["expired", "revoked", "budget"])
def test_eligibility_is_deterministic_at_purchase_time(tmp_path, restriction):
    shop, buyer, metadata, goal = setup(tmp_path)
    handle = quote(buyer, metadata, goal)
    if restriction == "expired":
        shop.now[0] += 5000
    else:
        with shop.buyer.db.transaction() as db:
            db.execute(
                "UPDATE grants SET "
                + ("revoked=1" if restriction == "revoked" else "reserved=budget")
            )
    result = asyncio.run(
        OfflineBoundary(buyer, metadata, goal).invoke("purchase", json.dumps({"quote_id": handle}))
    )
    assert result.error in ("service_not_eligible", "invalid_quote_handle")
    assert counters(shop) == (0, 0, 0, 0, 0)


def test_100_overlapping_purchase_calls_single_execution(tmp_path):
    shop, buyer, metadata, goal = setup(tmp_path)
    handle = quote(buyer, metadata, goal)
    boundary = OfflineBoundary(buyer, metadata, goal)
    original = buyer.gate.run
    entered = 0

    async def run():
        ready = asyncio.Event()

        async def gate(*args, **kwargs):
            nonlocal entered
            entered += 1
            if entered == 100:
                ready.set()
            await asyncio.wait_for(ready.wait(), 10)
            return await original(*args, **kwargs)

        buyer.gate.run = gate
        return await asyncio.gather(
            *(boundary.invoke("purchase", json.dumps({"quote_id": handle})) for _ in range(100))
        )

    results = asyncio.run(run())
    assert entered == 100
    assert len({r.purchase_id for r in results}) == 1
    assert counters(shop) == (1, 1, 1, 1, 10_000)
    assert finance(shop) == (10_000, 0)


def test_simulator_quote_and_identity_cannot_be_adopted(tmp_path, runtime):
    from conftest import authorize
    from test_buyer_agent import GOAL

    shop, buyer, metadata, goal = setup(tmp_path)
    authorize(runtime)
    sim_metadata = AgentStore(tmp_path / "sim-agent.sqlite3")
    sim_metadata.initialize(runtime.ledger, "task", GOAL)
    simulator = Boundary(runtime.buyer("task"), sim_metadata, GOAL)
    offer = asyncio.run(simulator.invoke("get_quote", '{"service_id":"report-standard"}'))
    with sim_metadata.db.connect() as source, metadata.db.transaction() as target:
        row = source.execute("SELECT * FROM quote_handles").fetchone()
        target.execute("INSERT INTO quote_handles VALUES(?,?,?,?)", tuple(row))
    result = asyncio.run(
        OfflineBoundary(buyer, metadata, goal).invoke(
            "purchase", json.dumps({"quote_id": offer.quote_id})
        )
    )
    assert result.error == "invalid_quote_handle"
    with pytest.raises(GateDenied, match="offline_buyer_profile_required"):
        OfflineX402Buyer(runtime.ledger, buyer.client, shop.signer, buyer.clock, "task")
    assert counters(shop) == (0, 0, 0, 0, 0)


def test_changed_merchant_terms_reject_before_quote_persistence(tmp_path):
    import base64

    from agentic_payments_lab.x402_adapter.protocol import required_data

    shop, buyer, metadata, goal = setup(tmp_path)
    data = required_data(shop.buyer.policy, identified=True)
    data["accepts"][0]["amount"] = "1"

    async def changed(*_):
        return base64.b64encode(json.dumps(data).encode()).decode()

    buyer.client.challenge = changed
    report = drive(buyer, metadata, goal)
    assert report.termination == "tool_error"
    with metadata.db.connect() as db:
        assert db.execute("SELECT count(*) FROM quote_handles").fetchone()[0] == 0
    assert counters(shop) == (0, 0, 0, 0, 0)
    assert finance(shop) == (0, 0)


def test_status_checkpoints_and_finalization_are_read_only(tmp_path, monkeypatch):
    shop, buyer, metadata, goal = setup(tmp_path)
    initial = drive(buyer, metadata, goal)

    def forbidden(*_args, **_kwargs):
        pytest.fail("Status entered a financial write or merchant operation")

    for target, names in (
        (shop.buyer.db, ("transaction",)),
        (buyer.client, ("retrieve", "challenge", "dispatch")),
        (buyer.gate, ("run",)),
        (buyer.gate.signer, ("sign",)),
    ):
        for name in names:
            monkeypatch.setattr(target, name, forbidden)
    timeouts = []
    original = shop.buyer.report

    def snapshot(task_id, *, timeout=None):
        timeouts.append(timeout)
        return original(task_id, timeout=timeout)

    monkeypatch.setattr(shop.buyer, "report", snapshot)
    report = drive(buyer, metadata, goal)
    assert report.purchase == initial.purchase
    assert timeouts[-1] == 0.1
    assert counters(shop) == (1, 1, 1, 1, 10_000)


def test_quote_from_another_fixture_instance_cannot_be_reused(tmp_path):
    first, buyer1, metadata1, goal1 = setup(tmp_path / "first")
    second, buyer2, metadata2, goal2 = setup(tmp_path / "second")
    handle = quote(buyer1, metadata1, goal1)
    with metadata1.db.connect() as source, metadata2.db.transaction() as target:
        row = source.execute("SELECT * FROM quote_handles").fetchone()
        target.execute("INSERT INTO quote_handles VALUES(?,?,?,?)", tuple(row))
    result = asyncio.run(
        OfflineBoundary(buyer2, metadata2, goal2).invoke(
            "purchase", json.dumps({"quote_id": handle})
        )
    )
    assert result.error == "invalid_quote_handle"
    assert buyer1.context != buyer2.context
    assert counters(first) == counters(second) == (0, 0, 0, 0, 0)


@pytest.mark.parametrize("history", ["claimed", "canceled", "legacy_binding"])
def test_initialization_cannot_adopt_inconsistent_or_preexecuted_history(tmp_path, history):
    from merchant_support import scenario

    from agentic_payments_lab.domain import canonical, digest
    from agentic_payments_lab.x402_adapter.protocol import required_data

    shop = scenario(tmp_path)
    buyer, metadata, goal = configured(shop, tmp_path, initialize=False)
    if history == "claimed":
        shop.buyer.claim(
            "task", digest(required_data(shop.buyer.policy, identified=True)), shop.now[0]
        )
    elif history == "canceled":
        shop.buyer.cancel("task", shop.now[0])
    else:
        with shop.buyer.db.transaction() as db:
            db.execute(
                "INSERT INTO agent_bindings VALUES(?,?)",
                (
                    "task",
                    canonical({"context": buyer.context.model_dump(), "goal": goal.model_dump()}),
                ),
            )
    before = shop.buyer.report("task")
    with pytest.raises(AgentMetadataError):
        buyer.initialize(metadata, goal)
    assert shop.buyer.report("task") == before
    assert counters(shop) == (0, 0, 0, 0, 0)
