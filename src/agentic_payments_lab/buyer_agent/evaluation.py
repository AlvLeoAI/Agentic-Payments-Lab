"""Deterministic offline evaluation against quiescent, independently read records.

Model prose is deliberately excluded. The fixture authority counts actual double
entries/transfer rows, not blockchain charges or buyer-visible success claims.
"""

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from ..domain import Grant, PurchaseRequest
from ..x402_adapter.observer import FixtureSource, OfflineEvidenceObserver
from ..x402_adapter.protocol import OfflineSignature
from ..x402_contracts import ASSET, OperatorPolicy
from ..x402_delivery import OfflineDeliveryAcceptor
from ..x402_merchant_contracts import MerchantOrder, SettlementObservation
from ..x402_provider import OfflineMerchant, OfflineMerchantClient
from ..x402_reconciliation import OfflineReconciler
from ..x402_store import OfflineGateStore
from .contracts import Goal, Settings
from .offline_x402 import OfflineX402Buyer
from .runner import run_buyer
from .store import AgentStore
from .wire_evaluation import evaluate_wire

NOW = 1_900_000_000
TX = "0x" + "ab" * 32
BLOCK = "0x" + "bb" * 32
HEAD = "0x" + "cc" * 32


@contextmanager
def read_snapshot(path):
    db = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    try:
        db.execute("BEGIN")
        yield db
    finally:
        db.close()


def score_case(root: Path, report, *, name: str, phase: str, delivered=False, incident=False):
    """Read after writers stop. No cross-file atomic snapshot is claimed."""
    with read_snapshot(root / "buyer.sqlite3") as db:
        task = db.execute("SELECT * FROM tasks WHERE task_id='evaluation-task'").fetchone()
        row = db.execute(
            "SELECT * FROM x402_attempts WHERE purchase_id=?", (task["purchase_id"],)
        ).fetchone()
        grant = db.execute("SELECT * FROM grants WHERE grant_id=?", (task["grant_id"],)).fetchone()
        events = list(db.execute("SELECT * FROM events ORDER BY sequence"))
    with read_snapshot(root / "authority.sqlite3") as db:
        calls = [r[0] for r in db.execute("SELECT kind FROM calls ORDER BY rowid")]
        transfers = [dict(r) for r in db.execute("SELECT * FROM transfers ORDER BY rowid")]
    with read_snapshot(root / "agent.sqlite3") as db:
        trace = [
            json.loads(r[0])
            for r in db.execute(
                "SELECT data FROM tool_events WHERE run_id=? ORDER BY sequence", (report.run_id,)
            )
        ]
        persisted = db.execute("SELECT data FROM runs WHERE run_id=?", (report.run_id,)).fetchone()
    accepted = [e for e in events if e["event_type"] == "offline_delivery_accepted"]
    incidents = [e for e in events if e["event_type"] == "offline_reconciliation_incident"]
    terminal = [e for e in events if e["event_type"] == "offline_fixture_applied"]
    expected_tools = ["get_purchase_status"]
    if name == "new_unknown":
        expected_tools += ["list_services", "get_quote", "purchase"]
    settled = phase == "fixture_settled"
    expected_reserved, expected_spent = (0, 10_000) if settled else (10_000, 0)
    summary = report.purchase.model_dump(mode="json")
    acceptance_hash = json.loads(accepted[0]["data"])["envelope_hash"] if accepted else None
    checks = {
        "completed_host_run": report.termination == "completed"
        and report.run_complete
        and report.metadata_persisted,
        "original_identity": summary["purchase_id"] == task["purchase_id"] == row["purchase_id"],
        "financial_report_binding": report.task_id == task["task_id"]
        and summary["amount_atomic"] == row["amount"] == 10_000
        and summary["authorization_id"] == row["authorization_id"]
        and summary["dispatch_may_have_occurred"] == bool(row["dispatch_authorized"])
        and report.decision
        == (
            "operator_reconciliation_required" if incident or not delivered else "purchase_recorded"
        ),
        "authoritative_phase": summary["payment_state"] == row["phase"] == phase,
        "authoritative_exposure": (grant["reserved"], grant["spent"])
        == (summary["reserved_atomic"], summary["spent_atomic"])
        == (expected_reserved, expected_spent),
        "one_double_execution": calls == ["signer", "dispatcher", "settler"],
        "one_bound_synthetic_transfer": transfers == [{"nonce": row["nonce"], "amount": 10_000}],
        "single_accounting_transition": len(terminal) == int(settled),
        "delivery_truth": summary["delivery_state"]
        == ("delivered" if delivered else "not_requested")
        and len(accepted) == int(delivered)
        and summary["delivery_acceptance_hash"] == acceptance_hash,
        "incident_visible": bool(incidents) == summary["operator_incident"] == incident,
        "operator_required": summary["operator_reconciliation_required"]
        == (incident or not delivered),
        "fixture_labels": summary.get("payment_backend") == "offline_x402"
        and summary.get("evidence_source") == "offline_fixture"
        and summary.get("blockchain_settlement_verified") is False
        and "simulated_receipt_id" not in summary,
        "actual_tool_trace": [e["tool"] for e in trace] == expected_tools
        and all(e["outcome"] == "ok" for e in trace)
        and [e["attempt"] for e in trace] == list(range(1, len(trace) + 1))
        and report.tool_attempts == len(trace)
        and trace == [e.model_dump() for e in report.events],
        "durable_report": persisted is not None
        and json.loads(persisted[0])
        == report.model_dump(mode="json", exclude={"model_explanation"}),
    }
    return {
        "case": name,
        "passed": all(checks.values()),
        "checks": checks,
        "observed": {
            "purchase_id": task["purchase_id"],
            "phase": row["phase"],
            "reserved": grant["reserved"],
            "spent": grant["spent"],
            "double_calls": calls,
            "synthetic_transfers": len(transfers),
            "transfer_atomic": sum(t["amount"] for t in transfers),
            "accounting_events": len(terminal),
            "delivery_events": len(accepted),
            "incident_events": len(incidents),
            "tool_trace": trace,
        },
    }


class EvaluationAuthority:
    mode = "offline_double"

    def __init__(self, path):
        self.path = path
        with sqlite3.connect(path) as db:
            db.executescript(
                "CREATE TABLE calls(kind TEXT NOT NULL);"
                "CREATE TABLE transfers(nonce TEXT PRIMARY KEY,amount INTEGER NOT NULL);"
            )

    def entry(self, kind):
        with sqlite3.connect(self.path) as db:
            db.execute("INSERT INTO calls VALUES(?)", (kind,))

    async def sign(self, terms):
        self.entry("signer")
        return OfflineSignature("offline-test-signature:EVALUATION_TRANSIENT_ONLY")

    async def settle(self, *, payment_header, authorization):
        self.entry("settler")
        with sqlite3.connect(self.path) as db:
            db.execute(
                "INSERT INTO transfers VALUES(?,?)", (authorization.nonce, authorization.amount)
            )
        return SettlementObservation(outcome="reported_success", transaction=TX)


def fixture_records(terms, *, unused=False):
    """Synthetic public records for an explicit operator step, never a chain source."""
    head = {"number": 100, "hash": HEAD, "timestamp": NOW + 100}
    block = {"number": 90, "hash": BLOCK, "timestamp": NOW + 1}
    used = {
        "event": "AuthorizationUsed",
        "asset": ASSET,
        "payer": terms.payer,
        "nonce": terms.nonce,
        "transaction_hash": TX,
        "block_number": 90,
        "block_hash": BLOCK,
        "log_index": 1,
    }
    transfer = {
        "event": "Transfer",
        "asset": ASSET,
        "payer": terms.payer,
        "recipient": terms.recipient,
        "amount": terms.amount,
        "transaction_hash": TX,
        "block_number": 90,
        "block_hash": BLOCK,
        "log_index": 2,
    }
    return {
        "profile": {
            "source": "offline_fixture",
            "network": "eip155:84532",
            "asset": ASSET,
            "chain_id": 84532,
            "domain_name": "USDC",
            "domain_version": "2",
        },
        "finalized_head": head,
        "finalized_head_check": head,
        "nonce_state": {
            "asset": ASSET,
            "payer": terms.payer,
            "nonce": terms.nonce,
            "block_number": 100,
            "block_hash": HEAD,
            "used": not unused,
        },
        "authorization_events": {
            "from_block": 0,
            "to_block": 100,
            "finalized_head_hash": HEAD,
            "complete": True,
            "events": [used],
        },
        "transaction": {
            "hash": TX,
            "to": ASSET,
            "call": {
                "method": "transferWithAuthorization",
                "payer": terms.payer,
                "recipient": terms.recipient,
                "amount": terms.amount,
                "valid_after": terms.valid_after,
                "valid_before": terms.valid_before,
                "nonce": terms.nonce,
            },
        },
        "receipt": {
            "transaction_hash": TX,
            "block_number": 90,
            "block_hash": BLOCK,
            "success": True,
            "logs": [used, transfer],
        },
        "canonical_block": {"finalized_head_hash": HEAD, "block": block},
    }


async def evaluate(root: Path) -> dict:
    """Own one fresh disposable fixture. Never open or migrate retained state."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=False)
    policy = OperatorPolicy(
        grant=Grant(
            grant_id="evaluation-grant",
            operator_id="offline-evaluator",
            service_ids=("evaluation-report",),
            purpose="research",
            budget=30_000,
            per_purchase_limit=10_000,
            issued_at=NOW - 1,
            expires_at=NOW + 600,
        ),
        service_id="evaluation-report",
        resource="https://merchant.invalid/report",
        method="POST",
        payer="0x" + "11" * 20,
        recipient="0x" + "22" * 20,
        amount=10_000,
        max_timeout_seconds=60,
    )
    store = OfflineGateStore.initialize(root / "buyer.sqlite3", policy)
    authority = EvaluationAuthority(root / "authority.sqlite3")
    merchant = OfflineMerchant.initialize(root / "merchant.sqlite3", policy, authority, lambda: NOW)
    request = PurchaseRequest(
        service_id=policy.service_id, subject="synthetic-evaluation", purpose="research"
    )
    context = store.create_task("evaluation-task", request, NOW)
    merchant.store.enroll(MerchantOrder(context=context, request=request))
    client = OfflineMerchantClient(merchant)
    dispatch = client.dispatch

    async def instrumented_dispatch(**kwargs):
        authority.entry("dispatcher")
        return await dispatch(**kwargs)

    client.dispatch = instrumented_dispatch
    buyer = OfflineX402Buyer(store, client, authority, lambda: NOW, context.task_id)
    metadata = AgentStore(root / "agent.sqlite3")
    goal = Goal(subject=request.subject, purpose=request.purpose, quality="basic")
    buyer.initialize(metadata, goal)
    settings = Settings(max_turns=6, tool_limit=8, max_output_tokens=512, timeout_seconds=30)
    results = []
    for name in ("new_unknown", "replay_unknown", "operator_delivered", "incident_visible"):
        if name in ("operator_delivered", "incident_visible"):
            records = fixture_records(
                store.reconciliation_terms(context.task_id), unused=name == "incident_visible"
            )
            observer = OfflineEvidenceObserver(
                FixtureSource({k: json.dumps(v).encode() for k, v in records.items()})
            )
            # Deliberately separate operator calls, outside every agent invocation.
            await OfflineReconciler(store, observer, lambda: NOW + 100).reconcile(context.task_id)
            if name == "operator_delivered":
                await OfflineDeliveryAcceptor(store, client, lambda: NOW + 100).accept(
                    context.task_id
                )
        report = await run_buyer(buyer, metadata, goal, settings)
        result = score_case(
            root,
            report,
            name=name,
            phase="reconciliation_required" if name.endswith("unknown") else "fixture_settled",
            delivered=name in ("operator_delivered", "incident_visible"),
            incident=name == "incident_visible",
        )
        results.append(result)
    wire = await evaluate_wire(root / "responses-mock")
    result = {
        "mode": "offline_evaluation",
        "model_behavior": "scripted_not_quality_evidence",
        "blockchain_settlement_verified": False,
        "passed": wire["passed"] and all(r["passed"] for r in results),
        "cases": results,
        "wire": wire,
    }
    (root / "evaluation.json").write_text(json.dumps(result, indent=2) + "\n")
    return result
