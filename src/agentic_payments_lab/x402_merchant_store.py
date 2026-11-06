"""Separate, explicitly initialized offline merchant journal and resource cache.

No buyer accounting, signing, settlement calls, lease takeover or automatic repair.
Identity and nonce tombstones outlive cached content. No signatures are stored.
"""

import json
import re
import sqlite3
from contextlib import closing
from pathlib import Path
from uuid import uuid4

from pydantic import TypeAdapter

from .buyer_schema import SchemaError, _ddl, _expected, _objects
from .domain import Resource, ResourceEnvelope, canonical, digest
from .storage import Database
from .x402_contracts import GateDenied, Hash, OperatorPolicy
from .x402_merchant_contracts import (
    MerchantOrder,
    MerchantRequest,
    MerchantSubmission,
    SettlementObservation,
    order_fingerprint,
    payment_identifier,
)

VERSION = 1  # A new merchant file; never a buyer/simulator database migration.
CACHE_SECONDS = 7 * 24 * 60 * 60
SCHEMA = """
CREATE TABLE merchant_profile (
    singleton INTEGER PRIMARY KEY CHECK(singleton=1), data TEXT NOT NULL
);
CREATE TRIGGER merchant_profile_no_update BEFORE UPDATE ON merchant_profile
BEGIN SELECT RAISE(ABORT, 'merchant profile is immutable'); END;
CREATE TRIGGER merchant_profile_no_delete BEFORE DELETE ON merchant_profile
BEGIN SELECT RAISE(ABORT, 'merchant profile is immutable'); END;
CREATE TABLE orders (
    payment_id TEXT PRIMARY KEY, purchase_id TEXT NOT NULL UNIQUE,
    task_id TEXT NOT NULL UNIQUE, grant_id TEXT NOT NULL,
    data TEXT NOT NULL, fingerprint TEXT NOT NULL
);
CREATE TRIGGER orders_no_update BEFORE UPDATE ON orders
BEGIN SELECT RAISE(ABORT, 'merchant order is immutable'); END;
CREATE TRIGGER orders_no_delete BEFORE DELETE ON orders
BEGIN SELECT RAISE(ABORT, 'merchant order cannot be deleted'); END;
CREATE TABLE settlements (
    payment_id TEXT PRIMARY KEY REFERENCES orders(payment_id), owner TEXT NOT NULL UNIQUE,
    network TEXT NOT NULL, asset TEXT NOT NULL, payer TEXT NOT NULL, nonce TEXT NOT NULL,
    authorization TEXT NOT NULL, fingerprint TEXT NOT NULL,
    outcome TEXT NOT NULL DEFAULT 'unknown'
        CHECK(outcome IN ('unknown','pending','reported_success','reported_failure')),
    observation TEXT,
    UNIQUE(network,asset,payer,nonce)
);
CREATE TRIGGER settlements_identity BEFORE UPDATE OF
    payment_id,owner,network,asset,payer,nonce,authorization,fingerprint ON settlements
BEGIN SELECT RAISE(ABORT, 'settlement identity is immutable'); END;
CREATE TRIGGER settlements_no_delete BEFORE DELETE ON settlements
BEGIN SELECT RAISE(ABORT, 'settlement claim cannot be deleted'); END;
CREATE TRIGGER settlements_observation BEFORE UPDATE OF outcome,observation ON settlements
WHEN OLD.observation IS NOT NULL
BEGIN SELECT RAISE(ABORT, 'settlement observation is immutable'); END;
CREATE TABLE deliveries (
    payment_id TEXT PRIMARY KEY REFERENCES settlements(payment_id),
    content TEXT, content_hash TEXT NOT NULL,
    expires_at INTEGER NOT NULL CHECK(typeof(expires_at)='integer' AND expires_at>=0)
);
CREATE TRIGGER deliveries_requires_success BEFORE INSERT ON deliveries
WHEN NOT EXISTS (SELECT 1 FROM settlements WHERE payment_id=NEW.payment_id
    AND outcome='reported_success' AND observation IS NOT NULL)
BEGIN SELECT RAISE(ABORT, 'delivery requires a reported success'); END;
CREATE TRIGGER deliveries_identity BEFORE UPDATE OF payment_id,content_hash,expires_at ON deliveries
BEGIN SELECT RAISE(ABORT, 'delivery identity is immutable'); END;
CREATE TRIGGER deliveries_expire_only BEFORE UPDATE OF content ON deliveries
WHEN NEW.content IS NOT NULL
BEGIN SELECT RAISE(ABORT, 'cached content can only expire'); END;
CREATE TRIGGER deliveries_no_delete BEFORE DELETE ON deliveries
BEGIN SELECT RAISE(ABORT, 'delivery tombstone cannot be deleted'); END;
"""


class MerchantConflict(GateDenied):
    pass


class MerchantStore:
    def __init__(self, path: Path, policy: OperatorPolicy, requirements_hash: str):
        self.policy = policy
        TypeAdapter(Hash).validate_python(requirements_hash)
        self.profile = {
            "mode": "offline_x402_merchant",
            "policy": policy.model_dump(mode="json"),
            "requirements_hash": requirements_hash,
            "cache_seconds": CACHE_SECONDS,
        }
        path = Path(path)
        if not path.is_file():
            raise SchemaError("Merchant history missing; explicit recovery required")
        # Reject unsupported schemas before any persistent journal change.
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
            db.row_factory = sqlite3.Row
            db.execute("BEGIN")
            self._validate(db)
        self.db = Database(path, "", timeout=0.2, defer_setup=True)
        with self.db.transaction() as db:
            self._validate(db)
        with self.db.connect() as db:
            if db.execute("PRAGMA journal_mode=WAL").fetchone()[0] != "wal":
                raise SchemaError("Merchant WAL configuration failed; inspect existing state")

    def _validate(self, db):
        if db.execute("PRAGMA user_version").fetchone()[0] != VERSION or _objects(db) != _expected(
            SCHEMA
        ):
            raise SchemaError("Unsupported/incomplete merchant schema; explicit recovery required")
        rows = db.execute("SELECT data FROM merchant_profile").fetchall()
        if len(rows) != 1 or rows[0][0] != canonical(self.profile):
            raise SchemaError("Merchant profile changed or missing; refusing to rebind")

    @classmethod
    def initialize(cls, path: Path, policy: OperatorPolicy, requirements_hash: str):
        TypeAdapter(Hash).validate_python(requirements_hash)
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with path.open("xb"):
                pass
        except FileExistsError:
            raise SchemaError("Merchant initialization requires a fresh database path") from None
        profile = {
            "mode": "offline_x402_merchant",
            "policy": policy.model_dump(mode="json"),
            "requirements_hash": requirements_hash,
            "cache_seconds": CACHE_SECONDS,
        }
        database = Database(path, "", timeout=0.2, defer_setup=True)
        with database.transaction() as db:
            _ddl(db, SCHEMA)
            db.execute("INSERT INTO merchant_profile VALUES(1,?)", (canonical(profile),))
            db.execute(f"PRAGMA user_version={VERSION}")
        return cls(path, policy, requirements_hash)

    def enroll(self, order: MerchantOrder) -> str:
        """Explicit trusted-host operation before HTTP; never creates a buyer task."""
        order = MerchantOrder.model_validate(order.model_dump())
        self.policy.validate_request(order.request)
        if order.context.grant_id != self.policy.grant.grant_id or not re.fullmatch(
            r"offline-purchase-[0-9a-f]{64}", order.context.purchase_id
        ):
            raise GateDenied("merchant_grant_not_approved")
        payment_id = payment_identifier(order.context.purchase_id)
        fingerprint = order_fingerprint(
            order, self.policy.resource, self.policy.method, digest(self.profile)
        )
        with self.db.transaction() as db:
            old = db.execute(
                "SELECT * FROM orders WHERE purchase_id=? OR task_id=?",
                (order.context.purchase_id, order.context.task_id),
            ).fetchall()
            if old:
                if len(old) != 1 or old[0]["fingerprint"] != fingerprint:
                    raise MerchantConflict("merchant_order_conflict")
                return payment_id
            db.execute(
                "INSERT INTO orders VALUES(?,?,?,?,?,?)",
                (
                    payment_id,
                    order.context.purchase_id,
                    order.context.task_id,
                    order.context.grant_id,
                    canonical(order),
                    fingerprint,
                ),
            )
        return payment_id

    def _order(self, db, request: MerchantRequest):
        row = db.execute(
            "SELECT * FROM orders WHERE purchase_id=?", (request.purchase_id,)
        ).fetchone()
        if not row:
            raise GateDenied("merchant_order_not_enrolled")
        order = MerchantOrder.model_validate_json(row["data"])
        if canonical(order.request) != canonical(request.request):
            raise MerchantConflict("merchant_request_conflict")
        return row

    def check_request(self, request: MerchantRequest):
        with self.db.connect() as db:
            self._order(db, request)

    def claim(self, request: MerchantRequest, submission: MerchantSubmission, now: int):
        """One committed settlement owner; no leases, retries or restart takeover."""
        submission = MerchantSubmission.model_validate(submission.model_dump())
        with self.db.transaction() as db:
            order = self._order(db, request)
            if submission.payment_id != order["payment_id"]:
                raise MerchantConflict("merchant_payment_identifier_conflict")
            auth = submission.authorization
            fingerprint = digest(
                {"order": order["fingerprint"], "submission": submission.model_dump()}
            )
            old = db.execute(
                "SELECT fingerprint FROM settlements WHERE payment_id=?", (submission.payment_id,)
            ).fetchone()
            if old:
                if old[0] != fingerprint:
                    raise MerchantConflict("merchant_authorization_conflict")
                return None
            if (
                auth.payer != self.policy.payer
                or auth.recipient != self.policy.recipient
                or auth.amount != self.policy.amount
                or auth.valid_after != 0
                or not self.policy.grant.issued_at
                <= now
                < auth.valid_before
                <= self.policy.grant.expires_at
                or auth.valid_before - now > self.policy.max_timeout_seconds
            ):
                raise GateDenied("merchant_authorization_not_active")
            if db.execute(
                "SELECT 1 FROM settlements WHERE network=? AND asset=? AND payer=? AND nonce=?",
                (self.policy.network, self.policy.asset.lower(), auth.payer.lower(), auth.nonce),
            ).fetchone():
                raise MerchantConflict("merchant_nonce_already_bound")
            owner = uuid4().hex
            db.execute(
                "INSERT INTO settlements(payment_id,owner,network,asset,payer,nonce,authorization,"
                "fingerprint) VALUES(?,?,?,?,?,?,?,?)",
                (
                    submission.payment_id,
                    owner,
                    self.policy.network,
                    self.policy.asset.lower(),
                    auth.payer.lower(),
                    auth.nonce,
                    canonical(auth),
                    fingerprint,
                ),
            )
        return owner

    def observe(self, payment_id: str, owner: str, observation: SettlementObservation):
        if type(observation) is not SettlementObservation:
            raise GateDenied("offline_observation_required")
        observation = SettlementObservation.model_validate(observation.model_dump())
        with self.db.transaction() as db:
            row = db.execute(
                "SELECT * FROM settlements WHERE payment_id=?", (payment_id,)
            ).fetchone()
            if not row or row["owner"] != owner:
                raise MerchantConflict("merchant_settlement_owner_mismatch")
            if row["observation"] is not None:
                if row["observation"] != canonical(observation):
                    raise MerchantConflict("merchant_observation_conflict")
                return
            db.execute(
                "UPDATE settlements SET outcome=?,observation=? WHERE payment_id=?",
                (observation.outcome, canonical(observation), payment_id),
            )

    def prepare_delivery(self, purchase_id: str, now: int):
        """Explicit non-charging recovery also used after the first reported success.

        This deterministic local resource has no external generation side effect.
        Publication and its identity/digest are one commit; expired rows never regenerate.
        """
        with self.db.transaction() as db:
            row = db.execute(
                "SELECT orders.data,settlements.payment_id FROM orders "
                "JOIN settlements USING(payment_id) "
                "WHERE purchase_id=? AND outcome='reported_success'",
                (purchase_id,),
            ).fetchone()
            if not row:
                raise GateDenied("merchant_delivery_not_ready")
            if db.execute(
                "SELECT 1 FROM deliveries WHERE payment_id=?", (row["payment_id"],)
            ).fetchone():
                return
            order = MerchantOrder.model_validate_json(row["data"])
            resource = Resource(
                service_id=order.request.service_id,
                subject=order.request.subject,
                request_hash=digest(order.request),
                quality="basic",
                summary=f"Offline x402 fixture report for {order.request.subject}.",
            )
            envelope = ResourceEnvelope(
                purchase_id=purchase_id,
                resource=resource,
                resource_hash=digest(resource),
                receipt_id=None,
            )
            db.execute(
                "INSERT INTO deliveries VALUES(?,?,?,?)",
                (row["payment_id"], canonical(envelope), digest(envelope), now + CACHE_SECONDS),
            )

    def expire_cache(self, now: int):
        """Explicit content removal, never identity/nonce removal or financial recovery."""
        with self.db.transaction() as db:
            db.execute(
                "UPDATE deliveries SET content=NULL WHERE expires_at<=? AND content IS NOT NULL",
                (now,),
            )

    def report(self, purchase_id: str, now: int) -> dict:
        with self.db.connect() as db, db:
            db.execute("BEGIN")
            order = db.execute(
                "SELECT * FROM orders WHERE purchase_id=?", (purchase_id,)
            ).fetchone()
            if not order:
                raise GateDenied("merchant_order_not_enrolled")
            settlement = db.execute(
                "SELECT * FROM settlements WHERE payment_id=?", (order["payment_id"],)
            ).fetchone()
            delivery = db.execute(
                "SELECT * FROM deliveries WHERE payment_id=?", (order["payment_id"],)
            ).fetchone()
        resource = None
        delivery_state = "not_ready"
        if settlement and settlement["outcome"] == "reported_success":
            delivery_state = "pending"
        if delivery:
            delivery_state = "gone"
            if delivery["content"] is not None and now < delivery["expires_at"]:
                resource = json.loads(delivery["content"])
                if digest(resource) != delivery["content_hash"]:
                    raise GateDenied("merchant_cache_integrity_failure")
                delivery_state = "cached"
        return {
            "mode": "offline_x402_merchant",
            "blockchain_settlement_verified": False,
            "purchase_id": purchase_id,
            "payment_id": order["payment_id"],
            "settlement_may_have_occurred": settlement is not None,
            "settlement_observation": json.loads(settlement["observation"])
            if settlement and settlement["observation"]
            else None,
            "delivery_state": delivery_state,
            "resource": resource,
        }
