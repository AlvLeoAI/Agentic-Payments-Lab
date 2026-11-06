"""Offline gate authority: one buyer DB, atomic budget + ownership, no SDK calls."""

import json
from pathlib import Path
from secrets import token_hex
from uuid import uuid4

from pydantic import TypeAdapter, ValidationError

from .buyer_schema import SchemaError, open_buyer_database
from .domain import Identifier, PurchaseConflict, PurchaseRequest, TaskContext, canonical, digest
from .ledger import SCHEMA, Ledger
from .x402_contracts import FixtureEvidence, GateDenied, OperatorPolicy, SigningTerms

TERMINAL = ("fixture_settled", "fixture_unused")


class OfflineGateStore:
    """Only explicitly enrolled offline fixtures. Never adopts a simulator task."""

    def __init__(self, path: Path, policy: OperatorPolicy):
        self.policy = policy
        self.db = open_buyer_database(path, SCHEMA, policy.profile(), initialize=False, timeout=0.2)
        with self.db.connect() as db:
            grant = db.execute(
                "SELECT data FROM grants WHERE grant_id=?", (policy.grant.grant_id,)
            ).fetchone()
            if not grant or grant["data"] != canonical(policy.grant):
                raise SchemaError("Offline grant missing/mismatched; operator recovery required")

    @classmethod
    def initialize(cls, path: Path, policy: OperatorPolicy):
        """Operator contract: a fresh path, no retained Phase 1/2 state."""
        # Exclusive creation prevents two initializers from adopting each other's
        # state. Failure may leave an empty/incomplete file: inspect it explicitly.
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        try:
            with Path(path).open("xb"):
                pass
        except FileExistsError:
            raise SchemaError("Offline initialization requires a fresh database path") from None
        db = open_buyer_database(path, SCHEMA, policy.profile(), initialize=True, timeout=0.2)
        with db.transaction() as connection:
            connection.execute(
                "INSERT INTO grants(grant_id,data,budget) VALUES(?,?,?)",
                (policy.grant.grant_id, canonical(policy.grant), policy.grant.budget),
            )
        # Profile and grant setup are separate local commits; incomplete setup
        # fails on reopen, never implicitly reconstructs a missing grant.
        return cls(path, policy)

    def create_task(self, task_id: str, request: PurchaseRequest, now: int) -> TaskContext:
        TypeAdapter(Identifier).validate_python(task_id)
        self.policy.validate_request(request)
        with self.db.transaction() as db:
            instance = db.execute("SELECT instance_id FROM buyer_profile").fetchone()[0]
            context = TaskContext(
                task_id=task_id,
                grant_id=self.policy.grant.grant_id,
                purchase_id="offline-purchase-" + digest({"instance": instance, "task": task_id}),
            )
            old = db.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
            binding = db.execute(
                "SELECT request FROM x402_tasks WHERE task_id=?", (task_id,)
            ).fetchone()
            if old:
                if (
                    dict(old) != context.model_dump()
                    or not binding
                    or binding["request"] != canonical(request)
                ):
                    raise PurchaseConflict(
                        "Missing or changed offline task history; recover explicitly"
                    )
                return context
            if binding:
                raise PurchaseConflict("Inconsistent offline task history")
            db.execute(
                "INSERT INTO tasks VALUES(?,?,?)", (task_id, context.grant_id, context.purchase_id)
            )
            db.execute("INSERT INTO x402_tasks VALUES(?,?)", (task_id, canonical(request)))
            Ledger._event(db, context.purchase_id, "offline_task_created", {}, now)
        return context

    def _task(self, db, task_id):
        task = db.execute(
            "SELECT tasks.*,x402_tasks.request FROM tasks JOIN x402_tasks USING(task_id) "
            "WHERE task_id=?",
            (task_id,),
        ).fetchone()
        if not task or task["grant_id"] != self.policy.grant.grant_id:
            raise GateDenied("task_history_missing_or_not_enrolled")
        self.policy.validate_request(PurchaseRequest.model_validate_json(task["request"]))
        return task

    def _grant(self, db, now):
        row = db.execute(
            "SELECT * FROM grants WHERE grant_id=?", (self.policy.grant.grant_id,)
        ).fetchone()
        if (
            not row
            or row["data"] != canonical(self.policy.grant)
            or row["revoked"]
            or not self.policy.grant.issued_at <= now < self.policy.grant.expires_at
        ):
            raise GateDenied("grant_missing_changed_revoked_or_expired")

    def claim(
        self, task_id: str, requirements_hash: str, now: int
    ) -> tuple[str, SigningTerms] | None:
        with self.db.transaction() as db:
            task = self._task(db, task_id)
            fingerprint = digest(
                {
                    "request": json.loads(task["request"]),
                    "requirements_hash": requirements_hash,
                    "policy_hash": digest(self.policy),
                }
            )
            old = db.execute(
                "SELECT fingerprint FROM x402_attempts WHERE purchase_id=?", (task["purchase_id"],)
            ).fetchone()
            if old:
                if old["fingerprint"] != fingerprint:
                    raise PurchaseConflict("Purchase already bound to different terms")
                return None  # Never reset a claim, regardless of time or process death.
            if db.execute(
                "SELECT 1 FROM x402_cancellations WHERE task_id=?", (task_id,)
            ).fetchone():
                return None  # Cancellation and ownership acquisition share this lock.
            self._grant(db, now)
            terms = SigningTerms(
                authorization_id="offline-auth-"
                + digest({"purchase": task["purchase_id"], "fingerprint": fingerprint}),
                purchase_id=task["purchase_id"],
                request_hash=digest(json.loads(task["request"])),
                requirements_hash=requirements_hash,
                policy_hash=digest(self.policy),
                payer=self.policy.payer,
                recipient=self.policy.recipient,
                amount=self.policy.amount,
                valid_after=0,
                valid_before=min(
                    now + self.policy.max_timeout_seconds, self.policy.grant.expires_at
                ),
                nonce="0x" + token_hex(32),  # Public EIP-3009 nonce, not a key.
            )
            terms.validate_policy(self.policy, now)
            changed = db.execute(
                "UPDATE grants SET reserved=reserved+? WHERE grant_id=? "
                "AND budget-spent-reserved>=?",
                (terms.amount, task["grant_id"], terms.amount),
            ).rowcount
            if changed != 1:
                raise GateDenied("insufficient_budget")
            owner = uuid4().hex
            db.execute(
                "INSERT INTO x402_attempts(purchase_id,grant_id,authorization_id,nonce,owner,"
                "fingerprint,terms,amount,phase,reason) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    terms.purchase_id,
                    task["grant_id"],
                    terms.authorization_id,
                    terms.nonce,
                    owner,
                    fingerprint,
                    canonical(terms),
                    terms.amount,
                    "signing_claimed",
                    "claim_committed",
                ),
            )
            Ledger._event(
                db,
                terms.purchase_id,
                "offline_signing_claimed",
                {"authorization_id": terms.authorization_id, "terms_hash": digest(terms)},
                now,
            )
        return owner, terms

    def authorize_dispatch(self, owner: str, terms: SigningTerms, now: int) -> bool:
        with self.db.transaction() as db:
            row = db.execute(
                "SELECT * FROM x402_attempts WHERE purchase_id=?", (terms.purchase_id,)
            ).fetchone()
            if (
                not row
                or row["owner"] != owner
                or row["terms"] != canonical(terms)
                or row["phase"] != "signing_claimed"
            ):
                return False
            self._grant(db, now)
            terms.validate_policy(self.policy, now)
            db.execute(
                "UPDATE x402_attempts SET phase='dispatch_authorized',dispatch_authorized=1,"
                "reason='dispatch_may_have_occurred' WHERE purchase_id=?",
                (terms.purchase_id,),
            )
            Ledger._event(db, terms.purchase_id, "offline_dispatch_authorized", {}, now)
        return True

    def cancel(self, task_id: str, now: int) -> None:
        """Operator fence for an enrolled task; acknowledge only after commit.

        Persistence errors propagate: they do not establish cancellation success
        or absence. A committed claim always keeps its reservation here.
        """
        with self.db.transaction() as db:
            task = self._task(db, task_id)
            row = db.execute(
                "SELECT * FROM x402_attempts WHERE purchase_id=?", (task["purchase_id"],)
            ).fetchone()
            if row and row["phase"] in TERMINAL:
                return
            inserted = db.execute(
                "INSERT INTO x402_cancellations VALUES(?,?) ON CONFLICT(task_id) DO NOTHING",
                (task_id, now),
            ).rowcount
            if not row:
                reason = "canceled_before_claim"
            elif not row["dispatch_authorized"]:
                phase, reason = "canceled_before_dispatch", "canceled_before_dispatch"
            else:
                phase, reason = "reconciliation_required", "canceled_after_dispatch_authorization"
            if row:
                db.execute(
                    "UPDATE x402_attempts SET phase=?,reason=? WHERE purchase_id=?",
                    (phase, reason, task["purchase_id"]),
                )
            if inserted:
                Ledger._event(
                    db, task["purchase_id"], "offline_cancellation", {"reason": reason}, now
                )

    def inconclusive(self, terms: SigningTerms, now: int) -> None:
        # No exception strings, raw HTTP responses, signatures or payloads accepted.
        with self.db.transaction() as db:
            changed = db.execute(
                "UPDATE x402_attempts SET phase='reconciliation_required',"
                "reason='outcome_unverified' "
                "WHERE purchase_id=? AND phase IN ('signing_claimed','dispatch_authorized')",
                (terms.purchase_id,),
            ).rowcount
            if changed:
                Ledger._event(db, terms.purchase_id, "offline_outcome_unverified", {}, now)

    def apply_fixture(self, task_id: str, evidence: FixtureEvidence, now: int) -> None:
        """Explicit operator action, not called by the dispatcher or at startup."""
        if type(evidence) is not FixtureEvidence:
            raise GateDenied("offline_fixture_required")
        try:
            evidence = FixtureEvidence.model_validate(evidence.model_dump())
        except ValidationError:
            raise GateDenied("invalid_offline_fixture") from None
        with self.db.transaction() as db:
            task = self._task(db, task_id)
            row = db.execute(
                "SELECT * FROM x402_attempts WHERE purchase_id=?", (task["purchase_id"],)
            ).fetchone()
            if not row:
                raise GateDenied("authorization_missing")
            terms = SigningTerms.model_validate_json(row["terms"])
            if (
                evidence.terms_hash != digest(terms)
                or evidence.nonce != terms.nonce
                or evidence.payer != terms.payer
                or evidence.recipient != terms.recipient
                or evidence.amount != terms.amount
                or evidence.finality != "finalized"
                or evidence.block_timestamp > now
            ):
                raise GateDenied("fixture_inconclusive_or_mismatched")
            if evidence.kind == "transfer":
                if (
                    not evidence.transaction_success
                    or not evidence.nonce_used
                    or evidence.transaction_hash is None
                    or not terms.valid_after < evidence.block_timestamp < terms.valid_before
                ):
                    raise GateDenied("fixture_transfer_not_proven")
                phase, spent = "fixture_settled", terms.amount
            else:
                if (
                    evidence.nonce_used
                    or evidence.block_timestamp <= terms.valid_before
                    or evidence.transaction_success
                    or evidence.transaction_hash is not None
                ):
                    raise GateDenied("fixture_nonuse_not_proven")
                phase, spent = "fixture_unused", 0
            if row["phase"] in TERMINAL:
                if row["phase"] != phase:
                    raise GateDenied("conflicting_terminal_fixture")
                return
            # Evidence can also fence a delayed signer; dispatch CAS then fails.
            db.execute(
                "UPDATE grants SET reserved=reserved-?,spent=spent+? WHERE grant_id=?",
                (terms.amount, spent, row["grant_id"]),
            )
            db.execute(
                "UPDATE x402_attempts SET phase=?,reason='offline_fixture_only',evidence=? "
                "WHERE purchase_id=?",
                (phase, canonical(evidence), terms.purchase_id),
            )
            Ledger._event(
                db,
                terms.purchase_id,
                "offline_fixture_applied",
                {"phase": phase, "evidence_hash": digest(evidence)},
                now,
            )

    def report(self, task_id: str) -> dict:
        """Allowlist export, one read snapshot for state, balance and ordered events."""
        with self.db.connect() as db, db:
            db.execute("BEGIN")
            task = self._task(db, task_id)
            row = db.execute(
                "SELECT * FROM x402_attempts WHERE purchase_id=?", (task["purchase_id"],)
            ).fetchone()
            canceled = db.execute(
                "SELECT 1 FROM x402_cancellations WHERE task_id=?", (task_id,)
            ).fetchone()
            grant = db.execute(
                "SELECT budget,reserved,spent FROM grants WHERE grant_id=?", (task["grant_id"],)
            ).fetchone()
            events = db.execute(
                "SELECT * FROM events WHERE purchase_id=? ORDER BY sequence", (task["purchase_id"],)
            ).fetchall()
        return {
            "mode": "offline_x402",
            "blockchain_settlement_verified": False,
            "purchase_id": task["purchase_id"],
            "network": self.policy.network,
            "asset": self.policy.asset,
            "amount": self.policy.amount,
            "authorization_id": row["authorization_id"] if row else None,
            "phase": row["phase"]
            if row
            else ("canceled_before_claim" if canceled else "not_started"),
            "reason": row["reason"] if row else ("canceled_before_claim" if canceled else None),
            "dispatch_may_have_occurred": bool(row and row["dispatch_authorized"]),
            "reconciliation_required": bool(row and row["phase"] not in TERMINAL),
            "delivery_state": "not_requested",
            "budget": dict(grant),
            "events": [{**dict(event), "data": json.loads(event["data"])} for event in events],
        }
