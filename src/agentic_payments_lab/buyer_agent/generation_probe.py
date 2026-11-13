"""Offline-only two-generation compatibility rehearsal; no live adapter or key input.

One shared, operator-selected authority owns the prior inventory and the sole probe
allocation. A new enrollment cannot reset that authority. This is not protection
against a malicious host creating another authority, cloning or restoring files.
All costs are conditional estimates, never invoice facts or provider spending caps.
"""

import asyncio
import hashlib
import json
import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

import httpx2

from ..domain import canonical, digest
from ..runtime_guard import require_pilot_runtime
from .call_budget import BudgetDenied
from .campaign import dependencies, source_witness
from .comparison import ComparisonLimits
from .comparison_protocol import (
    PROVIDERS,
    RATES,
    RESERVATION_POLICY,
    count_request_body,
    counted_estimate,
    prospective_observed_estimate,
    request_body,
)
from .comparison_runner import MockExchange, NativeMockModel

LIMITS = ComparisonLimits(requests_per_episode=1, requests_per_provider=1, requests_total=2)
TRANSCRIPT = [{"kind": "user", "text": "Synthetic compatibility check. No action is requested."}]
MISSING = [
    "explicit_live_generation_authorization",
    "complete_prior_inventory_and_account_pricing_scope_review",
    "conditional_context_and_rate_assumptions_acceptance",
    "provider_side_spend_limit_review_including_overshoot",
    "reviewed_live_adapter_and_current_source_preflight_lineage",
]
SCHEMA = """
PRAGMA user_version=2;
CREATE TABLE policy(singleton INTEGER PRIMARY KEY CHECK(singleton=1),data TEXT NOT NULL);
CREATE TABLE allocation(singleton INTEGER PRIMARY KEY CHECK(singleton=1),probe_id TEXT UNIQUE,
 policy TEXT NOT NULL);
CREATE TABLE state(singleton INTEGER PRIMARY KEY CHECK(singleton=1),status TEXT NOT NULL
 CHECK(status IN ('prepared','running','stopped','complete')),owner TEXT,next_slot INTEGER NOT NULL,
 acknowledged INTEGER NOT NULL DEFAULT 0 CHECK(acknowledged IN(0,1)));
CREATE TABLE counts(id TEXT PRIMARY KEY,slot INTEGER NOT NULL UNIQUE,provider TEXT NOT NULL,
 generation_hash TEXT NOT NULL,payload_hash TEXT NOT NULL,status TEXT NOT NULL
 CHECK(status IN('pending','complete')),input_tokens INTEGER,estimated_nano INTEGER,
 consumed INTEGER NOT NULL DEFAULT 0 CHECK(consumed IN(0,1)));
CREATE TABLE requests(id TEXT PRIMARY KEY,slot INTEGER NOT NULL UNIQUE,
 provider TEXT NOT NULL UNIQUE,
 body_hash TEXT NOT NULL,reserved_nano INTEGER NOT NULL,status TEXT NOT NULL
 CHECK(status IN('pending','complete','uncertain')),evidence TEXT,observed_nano INTEGER);
CREATE TRIGGER policy_no_update BEFORE UPDATE ON policy BEGIN SELECT RAISE(ABORT,'immutable'); END;
CREATE TRIGGER policy_no_delete BEFORE DELETE ON policy BEGIN SELECT RAISE(ABORT,'retain'); END;
CREATE TRIGGER allocation_no_update BEFORE UPDATE ON allocation
 BEGIN SELECT RAISE(ABORT,'one allocation'); END;
CREATE TRIGGER allocation_no_delete BEFORE DELETE ON allocation
 BEGIN SELECT RAISE(ABORT,'retain'); END;
CREATE TRIGGER state_no_delete BEFORE DELETE ON state BEGIN SELECT RAISE(ABORT,'retain'); END;
CREATE TRIGGER state_no_reset BEFORE UPDATE ON state WHEN
 (OLD.owner IS NOT NULL AND NEW.owner IS NOT OLD.owner) OR NEW.next_slot<OLD.next_slot OR
 (OLD.status!='prepared' AND NEW.status='prepared') OR
 (OLD.status IN('stopped','complete') AND NEW.status!=OLD.status) OR
 NEW.acknowledged<OLD.acknowledged
 BEGIN SELECT RAISE(ABORT,'retain fence'); END;
CREATE TRIGGER requests_no_delete BEFORE DELETE ON requests BEGIN SELECT RAISE(ABORT,'retain'); END;
CREATE TRIGGER requests_final BEFORE UPDATE ON requests WHEN OLD.status!='pending'
 BEGIN SELECT RAISE(ABORT,'immutable evidence'); END;
CREATE TRIGGER counts_no_delete BEFORE DELETE ON counts BEGIN SELECT RAISE(ABORT,'retain'); END;
CREATE TRIGGER counts_final BEFORE UPDATE ON counts WHEN OLD.status='complete' AND NOT
 (NEW.id=OLD.id AND NEW.slot=OLD.slot AND NEW.provider=OLD.provider AND
 NEW.generation_hash=OLD.generation_hash AND NEW.payload_hash=OLD.payload_hash AND
 NEW.status=OLD.status AND NEW.input_tokens=OLD.input_tokens AND
 NEW.estimated_nano=OLD.estimated_nano AND OLD.consumed=0 AND NEW.consumed=1)
 BEGIN SELECT RAISE(ABORT,'immutable count'); END;
"""


def _reject():
    raise BudgetDenied("generation_probe_gate_rejected")


def _sha(path):
    path = Path(path)
    if not path.is_file() or path.is_symlink() or path.stat().st_size > 16 * 1024 * 1024:
        _reject()
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def _inventory(records):
    """Hash-bound operator-supplied inventory, not an assertion it is exhaustive."""
    if type(records) is not list or not 1 <= len(records) <= 32:
        _reject()
    seen = set()
    total = {p: 0 for p in PROVIDERS}
    for row in records:
        if type(row) is not dict or set(row) != {
            "provider",
            "source_file",
            "source_sha256",
            "known_estimated_nano",
            "unresolved_nano",
        }:
            _reject()
        if row["provider"] not in PROVIDERS:
            _reject()
        for key in ("known_estimated_nano", "unresolved_nano"):
            if type(row[key]) is not int or not 0 <= row[key] <= 3_000_000_000:
                _reject()
        path = str(Path(row["source_file"]).resolve())
        if path in seen or path != row["source_file"] or _sha(path) != row["source_sha256"]:
            _reject()
        seen.add(path)
        total[row["provider"]] += row["known_estimated_nano"] + row["unresolved_nano"]
    return total


def create_mock_authority(path, *, authorization_id, prior_inventory):
    """Fresh-only shared authority. No live authority can be issued by this API."""
    require_pilot_runtime()
    if (
        type(authorization_id) is not str
        or re.fullmatch(r"[A-Za-z0-9_-]{1,80}", authorization_id) is None
    ):
        _reject()
    prior = _inventory(prior_inventory)
    if any(prior[p] > 1_500_000_000 for p in PROVIDERS) or (sum(prior.values()) > 3_000_000_000):
        _reject()
    path = Path(path).resolve()
    # Exclusive creation, no replacement/recovery of even an interrupted initialization.
    with path.open("xb"):
        pass
    policy = {
        "kind": "offline_generation_probe_authority_v2",
        "path": str(path),
        "authorization_id": authorization_id,
        "prior_inventory": prior_inventory,
        "prior_exposure_nano": prior,
        "total_ceiling_nano": 3_000_000_000,
        "arm_ceiling_nano": 1_500_000_000,
        "inventory_completeness_verified": False,
        "invoice_billing_verified": False,
        "source": source_witness(),
        "dependencies": dependencies(),
        "runtime": require_pilot_runtime(),
        "rates": RATES,
        "reservation_policy": RESERVATION_POLICY,
    }
    with sqlite3.connect(path) as db:
        db.executescript(SCHEMA)
        db.execute("INSERT INTO policy VALUES(1,?)", (canonical(policy),))
    return path


def prepare_mock_probe(authority_path, *, authorization_id, expected_authority_hash):
    probe = Probe(authority_path)
    probe.verify_source()
    if (
        authorization_id != probe.policy["authorization_id"]
        or expected_authority_hash != probe.authority_hash
    ):
        _reject()
    allocation = {
        "authority_hash": probe.authority_hash,
        "limits": LIMITS.model_dump(),
        "transcript": TRANSCRIPT,
        "providers": list(PROVIDERS),
        "reservation_policy": RESERVATION_POLICY,
        "reservation_timing": "atomic_after_exact_request_count",
        "execution_mode": "mock_only_no_live_adapter",
        "tool_execution": False,
    }
    with probe.connect(write=True) as db:
        # UNIQUE singleton commits the sole allocation in the same authority journal.
        if db.execute("SELECT 1 FROM allocation").fetchone():
            _reject()
        db.execute("INSERT INTO allocation VALUES(1,?,?)", (uuid4().hex, canonical(allocation)))
        db.execute("INSERT INTO state VALUES(1,'prepared',NULL,0,0)")
    return Probe(authority_path)


class Probe:
    VERSION = 2
    KIND = "offline_generation_probe_authority_v2"
    RESERVATION_POLICY = RESERVATION_POLICY

    @staticmethod
    def estimate(provider, count):
        return counted_estimate(provider, count)

    @staticmethod
    def observed(provider, usage):
        return prospective_observed_estimate(provider, usage)

    def __init__(self, path):
        self.path = Path(path).resolve()
        self._owner = None
        self.limits = LIMITS.model_copy(deep=True)
        with self.connect() as db:
            if db.execute("PRAGMA user_version").fetchone()[0] != self.VERSION:
                _reject()
            self.policy = json.loads(
                db.execute("SELECT data FROM policy WHERE singleton=1").fetchone()[0]
            )
            row = db.execute("SELECT policy FROM allocation WHERE singleton=1").fetchone()
            self.allocation = json.loads(row[0]) if row else None
        self._authority_hash = digest(self.policy)
        self._allocation_hash = digest(self.allocation)
        if self.policy["path"] != str(self.path) or self.policy["kind"] != self.KIND:
            _reject()

    @property
    def authority_hash(self):
        return self._authority_hash

    @property
    def policy_hash(self):
        return self._allocation_hash

    @contextmanager
    def connect(self, *, write=False):
        db = sqlite3.connect(
            self.path.as_uri() + ("?mode=rw" if write else "?mode=ro"), uri=True, timeout=0.1
        )
        db.row_factory = sqlite3.Row
        try:
            db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield db
            if write:
                db.commit()
        finally:
            db.close()

    def verify_source(self):
        if (
            digest(self.policy) != self.authority_hash
            or digest(self.allocation) != self.policy_hash
            or source_witness() != self.policy["source"]
            or dependencies() != self.policy["dependencies"]
            or require_pilot_runtime() != self.policy["runtime"]
            or self.policy["rates"] != RATES
            or self.policy["reservation_policy"] != self.RESERVATION_POLICY
            or _inventory(self.policy["prior_inventory"]) != self.policy["prior_exposure_nano"]
            or self.limits != LIMITS
            or (
                self.allocation is not None
                and (
                    self.allocation["authority_hash"] != self.authority_hash
                    or self.allocation["limits"] != self.limits.model_dump()
                )
            )
        ):
            _reject()
        with self.connect() as db:
            if json.loads(db.execute("SELECT data FROM policy").fetchone()[0]) != self.policy:
                _reject()
            row = db.execute("SELECT policy FROM allocation").fetchone()
            if (json.loads(row[0]) if row else None) != self.allocation:
                _reject()

    def activate(self, approved_policy_hash):
        self.verify_source()
        if not self.allocation or approved_policy_hash != self.policy_hash:
            _reject()
        owner = uuid4().hex
        with self.connect(write=True) as db:
            if (
                db.execute(
                    "UPDATE state SET status='running',owner=? WHERE status='prepared' "
                    "AND owner IS NULL",
                    (owner,),
                ).rowcount
                != 1
            ):
                _reject()
        self._owner = owner

    def _owned(self, db, entry):
        state = db.execute("SELECT * FROM state").fetchone()
        if (
            not self._owner
            or state["owner"] != self._owner
            or state["status"] != "running"
            or state["next_slot"] >= 2
            or entry != {"slot": state["next_slot"], "provider": PROVIDERS[state["next_slot"]]}
        ):
            _reject()

    def begin_count(self, entry, generation, body):
        self.verify_source()
        expected = request_body(entry["provider"], TRANSCRIPT, self.limits)
        if (
            generation != expected
            or body != canonical(count_request_body(entry["provider"], expected)).encode()
        ):
            # SDK JSON whitespace can differ, but semantic JSON types cannot.
            if generation != expected or canonical(json.loads(body)) != canonical(
                count_request_body(entry["provider"], expected)
            ):
                _reject()
        identifier = uuid4().hex
        with self.connect(write=True) as db:
            self._owned(db, entry)
            db.execute(
                "INSERT INTO counts VALUES(?,?,?,?,?,'pending',NULL,NULL,0)",
                (
                    identifier,
                    entry["slot"],
                    entry["provider"],
                    digest(generation),
                    hashlib.sha256(body).hexdigest(),
                ),
            )
        return identifier

    def finish_count(self, entry, count_id, payload):
        fields = {"input_tokens", "object"} if entry["provider"] == "openai" else {"input_tokens"}
        count = payload.get("input_tokens") if type(payload) is dict else None
        valid = (
            type(payload) is dict
            and set(payload) == fields
            and type(count) is int
            and 0 <= count <= (1_050_000 if entry["provider"] == "openai" else 1_000_000)
            and (entry["provider"] != "openai" or payload.get("object") == "response.input_tokens")
        )
        with self.connect(write=True) as db:
            self._owned(db, entry)
            if valid:
                if (
                    db.execute(
                        "UPDATE counts SET "
                        "status='complete',input_tokens=?,estimated_nano=? "
                        "WHERE id=? AND slot=? AND provider=? AND status='pending'",
                        (
                            count,
                            self.estimate(entry["provider"], count),
                            count_id,
                            entry["slot"],
                            entry["provider"],
                        ),
                    ).rowcount
                    != 1
                ):
                    _reject()
            else:
                db.execute("UPDATE state SET status='stopped'")
        return valid

    def reserve(self, entry, body, transcript, count_id):
        self.verify_source()
        if transcript != TRANSCRIPT or len(body) > self.limits.max_request_bytes:
            _reject()
        expected = request_body(entry["provider"], TRANSCRIPT, self.limits)
        if canonical(json.loads(body)) != canonical(expected):
            _reject()
        identifier = uuid4().hex
        with self.connect(write=True) as db:
            self._owned(db, entry)
            count = db.execute(
                "SELECT * FROM counts WHERE id=? AND slot=? AND provider=? AND "
                "status='complete' AND consumed=0",
                (count_id, entry["slot"], entry["provider"]),
            ).fetchone()
            if count is None or count["generation_hash"] != digest(expected):
                _reject()
            reservation = self.estimate(entry["provider"], count["input_tokens"])
            rows = db.execute("SELECT * FROM requests").fetchall()
            committed = dict(self.policy["prior_exposure_nano"])
            for row in rows:
                committed[row["provider"]] += (
                    row["observed_nano"]
                    if row["status"] == "complete"
                    else max(row["reserved_nano"], row["observed_nano"] or 0)
                )
            committed[entry["provider"]] += reservation
            if (
                any(v > 1_500_000_000 for v in committed.values())
                or sum(committed.values()) > 3_000_000_000
            ):
                _reject()
            db.execute("UPDATE counts SET consumed=1 WHERE id=?", (count_id,))
            db.execute(
                "INSERT INTO requests VALUES(?,?,?,?,?,'pending',NULL,NULL)",
                (
                    identifier,
                    entry["slot"],
                    entry["provider"],
                    hashlib.sha256(body).hexdigest(),
                    reservation,
                ),
            )
        return identifier

    def observe(self, entry, request_id, evidence):
        with self.connect(write=True) as db:
            self._owned(db, entry)
            row = db.execute(
                "SELECT * FROM requests WHERE id=? AND slot=? AND provider=? AND status='pending'",
                (request_id, entry["slot"], entry["provider"]),
            ).fetchone()
            if row is None:
                _reject()
            cost = self.observed(entry["provider"], evidence["usage"])
            good = (
                evidence["control_error"] is None
                and evidence["response_valid"]
                and cost is not None
                and cost <= row["reserved_nano"]
            )
            db.execute(
                "UPDATE requests SET status=?,evidence=?,observed_nano=? WHERE id=?",
                ("complete" if good else "uncertain", canonical(evidence), cost, request_id),
            )
            if not good:
                db.execute("UPDATE state SET status='stopped'")
        return good

    def advance(self, entry):
        with self.connect(write=True) as db:
            self._owned(db, entry)
            if not db.execute(
                "SELECT 1 FROM requests WHERE slot=? AND status='complete'", (entry["slot"],)
            ).fetchone():
                _reject()
            db.execute("UPDATE state SET next_slot=next_slot+1")

    def finish(self):
        with self.connect(write=True) as db:
            row = db.execute("SELECT * FROM state").fetchone()
            if row["owner"] != self._owner or row["status"] != "running" or row["next_slot"] != 2:
                _reject()
            db.execute("UPDATE state SET status='complete'")

    def acknowledge(self):
        with self.connect(write=True) as db:
            if (
                db.execute(
                    "UPDATE state SET acknowledged=1 WHERE status='complete' AND owner=? "
                    "AND next_slot=2",
                    (self._owner,),
                ).rowcount
                != 1
            ):
                _reject()

    def stop(self):
        with self.connect(write=True) as db:
            db.execute(
                "UPDATE state SET status='stopped' WHERE status='running' AND owner=?",
                (self._owner,),
            )

    def report(self):
        with self.connect() as db:
            state = dict(db.execute("SELECT status,next_slot,acknowledged FROM state").fetchone())
            counts = [
                dict(r)
                for r in db.execute(
                    "SELECT slot,provider,status,input_tokens,estimated_nano FROM counts "
                    "ORDER BY slot"
                )
            ]
            rows = [
                dict(r)
                for r in db.execute(
                    "SELECT slot,provider,reserved_nano,status,observed_nano,evidence "
                    "FROM requests ORDER BY slot"
                )
            ]
        by_provider = {r["provider"]: r for r in rows}
        retained = {}
        for provider in PROVIDERS:
            row = by_provider.get(provider)
            retained[provider] = (
                row["observed_nano"]
                if row and row["status"] == "complete"
                else max((row or {}).get("reserved_nano", 0), (row or {}).get("observed_nano") or 0)
            )
        for row in rows:
            row["evidence"] = json.loads(row["evidence"]) if row["evidence"] else None
        return {
            "scope": "offline_mock_generation_compatibility_only",
            "state": state,
            "run_complete": state == {"status": "complete", "next_slot": 2, "acknowledged": 1},
            "counts": counts,
            "generations": rows,
            "generation_attempts": len(rows),
            "tool_executions": 0,
            "prior_exposure_nano": self.policy["prior_exposure_nano"],
            "retained_probe_allocation_nano": retained,
            "aggregate_conditional_commitment_nano": sum(
                self.policy["prior_exposure_nano"].values()
            )
            + sum(retained.values()),
            "unattempted_allocation_is_not_actual_spend": True,
            "live_generation_ready": False,
            "billing_verified": False,
            "reservation_policy": self.RESERVATION_POLICY,
            "missing_live_attestations": list(MISSING),
        }


class ProbeExchange(MockExchange):
    def __init__(self, probe, entry, transport):
        if type(transport) is not httpx2.MockTransport:
            _reject()
        super().__init__(probe, entry, None)
        self._wire = transport


class ProbeModel(NativeMockModel):
    exchange_type = ProbeExchange


async def run_mock_probe(probe, *, transports, approved_policy_hash):
    """Exact mock transports only; no key parameter, live factory or buyer tool call."""
    if (
        type(probe) is not Probe
        or type(transports) is not dict
        or set(transports) != set(PROVIDERS)
        or any(type(t) is not httpx2.MockTransport for t in transports.values())
    ):
        _reject()
    probe.activate(approved_policy_hash)
    caller = {"outcome": "incomplete", "reason": "not_finished", "report": None}
    try:
        for slot, provider in enumerate(PROVIDERS):
            entry = {"slot": slot, "provider": provider}
            model = ProbeModel(probe, entry, transports[provider])
            try:
                async with asyncio.timeout(probe.limits.timeout_seconds):
                    await model.respond(TRANSCRIPT)
            finally:
                await model.aclose()
            # Observe returned calls, but deliberately never execute a tool or continue.
            probe.advance(entry)
        probe.finish()
        probe.acknowledge()
        caller["report"] = probe.report()
        caller.update(outcome="complete", reason="returned")
    except asyncio.CancelledError:
        caller["reason"] = "cancelled"
    except Exception:
        caller["reason"] = "failed"
    finally:
        if caller["outcome"] != "complete":
            try:
                probe.stop()
            except Exception:
                pass
    try:
        later = probe.report()
    except Exception:
        later = None
    return {
        "caller": caller,
        "later_report": later,
        "overall_complete": caller["outcome"] == "complete" and later == caller["report"],
    }
