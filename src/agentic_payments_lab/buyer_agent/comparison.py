"""One MOCK-ONLY owner, schedule, stop fence and independent/aggregate budgets.

The journal owns generation admission and evidence, never financial accounting.
Cross-file snapshots are quiescent witnesses, not distributed transactions. Any
incomplete owner/episode/tool/result fences restart; there is no resume/reset API.
"""

import hashlib
import json
import os
import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Annotated, Literal
from uuid import uuid4

from pydantic import Field, model_validator

from ..domain import Grant, PurchaseRequest, canonical, digest
from ..payments import PaymentFault
from ..provider import DeliveryFault
from ..runtime import Runtime
from ..runtime_guard import require_pilot_runtime
from ..x402_contracts import StrictModel
from .call_budget import BudgetDenied
from .campaign import dependencies, source_witness, state_witness
from .campaign_cases import frozen_plan
from .comparison_protocol import (
    LIVE_BLOCKERS,
    MODELS,
    PROVIDERS,
    RATES,
    RESERVATION_POLICY,
    backstop,
    count_request_body,
    counted_estimate,
    final_schema,
    prospective_observed_estimate,
    tool_schemas,
)
from .contracts import Goal
from .runner import INSTRUCTIONS
from .store import AgentStore


class ComparisonLimits(StrictModel):
    kind: Literal["paired_simulator_mock_v1", "paired_simulator_live_v1"] = (
        "paired_simulator_mock_v1"
    )
    execution_mode: Literal["http_mock_only", "live_simulator"] = "http_mock_only"
    requests_per_episode: Annotated[int, Field(strict=True, ge=1, le=6)] = 6
    tools_per_episode: Annotated[int, Field(strict=True, ge=1, le=8)] = 8
    requests_per_provider: Annotated[int, Field(strict=True, ge=1, le=144)] = 144
    requests_total: Annotated[int, Field(strict=True, ge=1, le=288)] = 288
    max_output_tokens: Literal[512] = 512
    max_request_bytes: Annotated[int, Field(strict=True, ge=1, le=32768)] = 32768
    timeout_seconds: Annotated[int, Field(strict=True, ge=1, le=30)] = 30
    openai_ceiling_nano: Annotated[int, Field(strict=True, ge=0, le=1_500_000_000)] = 1_500_000_000
    anthropic_ceiling_nano: Annotated[int, Field(strict=True, ge=0, le=1_500_000_000)] = (
        1_500_000_000
    )
    total_ceiling_nano: Annotated[int, Field(strict=True, ge=0, le=3_000_000_000)] = 3_000_000_000

    @model_validator(mode="after")
    def mode_binding(self):
        if (self.kind == "paired_simulator_mock_v1") != (self.execution_mode == "http_mock_only"):
            raise ValueError("comparison_mode_binding")
        return self


def paired_plan():
    """Alternate arms per case; reverse the first arm in alternate trials."""
    entries = []
    for entry in frozen_plan():
        providers = PROVIDERS if entry["trial"] % 2 else tuple(reversed(PROVIDERS))
        for provider in providers:
            fixture = provider + "-" + entry["fixture"]
            entries.append(
                {
                    **entry,
                    "slot": len(entries),
                    "provider": provider,
                    "id": provider + "." + entry["id"],
                    "fixture": fixture,
                    "task_id": fixture,
                }
            )
    return entries


SCHEMA = """
CREATE TABLE policy(singleton INTEGER PRIMARY KEY CHECK(singleton=1), data TEXT NOT NULL);
CREATE TABLE campaign(singleton INTEGER PRIMARY KEY CHECK(singleton=1), status TEXT NOT NULL
 CHECK(status IN ('prepared','running','stopped','finished')),
 owner TEXT, next_slot INTEGER NOT NULL,
 reason TEXT NOT NULL);
CREATE TABLE episodes(slot INTEGER PRIMARY KEY, id TEXT NOT NULL UNIQUE, provider TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('running','finished')), result TEXT, witness TEXT);
CREATE TABLE counts(id TEXT PRIMARY KEY, slot INTEGER NOT NULL REFERENCES episodes(slot),
 provider TEXT NOT NULL, generation_hash TEXT NOT NULL, payload_hash TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('pending','complete','uncertain')),
 consumed INTEGER NOT NULL DEFAULT 0 CHECK(consumed IN (0,1)), input_tokens INTEGER,
 estimated_nano INTEGER);
CREATE TABLE requests(id TEXT PRIMARY KEY, slot INTEGER NOT NULL REFERENCES episodes(slot),
 provider TEXT NOT NULL, ordinal INTEGER NOT NULL,
 body_hash TEXT NOT NULL, body_bytes INTEGER NOT NULL,
 transcript TEXT NOT NULL, reserved_nano INTEGER NOT NULL, status TEXT NOT NULL
 CHECK(status IN ('pending','complete','uncertain')), http_complete INTEGER NOT NULL DEFAULT 0,
 evidence TEXT, settlement_hash TEXT, observed_nano INTEGER,
 tool_attempts INTEGER NOT NULL DEFAULT 0,
 UNIQUE(slot,ordinal));
CREATE TABLE tools(request_id TEXT PRIMARY KEY REFERENCES requests(id), slot INTEGER NOT NULL,
 call_id TEXT NOT NULL, name TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('pending','complete')),
 result TEXT, UNIQUE(slot,call_id));
CREATE TABLE wire_attempts(id TEXT PRIMARY KEY, kind TEXT NOT NULL, record_id TEXT NOT NULL,
 provider TEXT NOT NULL, endpoint TEXT NOT NULL, payload_hash TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('dispatch_authorized','headers_received','uncertain')),
 http_status INTEGER, error_code TEXT, UNIQUE(kind,record_id));
CREATE TRIGGER wire_no_delete BEFORE DELETE ON wire_attempts
 BEGIN SELECT RAISE(ABORT,'retain dispatch claim'); END;
CREATE TRIGGER policy_no_update BEFORE UPDATE ON policy
 BEGIN SELECT RAISE(ABORT,'immutable comparison'); END;
CREATE TRIGGER policy_no_delete BEFORE DELETE ON policy
 BEGIN SELECT RAISE(ABORT,'retain comparison'); END;
CREATE TRIGGER campaign_no_delete BEFORE DELETE ON campaign
 BEGIN SELECT RAISE(ABORT,'retain owner'); END;
CREATE TRIGGER campaign_no_reset BEFORE UPDATE ON campaign
 WHEN OLD.status != 'prepared' AND (NEW.status='prepared' OR NEW.owner IS NOT OLD.owner
 OR NEW.next_slot < OLD.next_slot
 OR (OLD.status IN ('stopped','finished') AND NEW.status!=OLD.status))
 BEGIN SELECT RAISE(ABORT,'retain comparison fence'); END;
CREATE TRIGGER episodes_no_delete BEFORE DELETE ON episodes
 BEGIN SELECT RAISE(ABORT,'retain episode'); END;
CREATE TRIGGER episodes_final BEFORE UPDATE ON episodes WHEN OLD.status='finished'
 BEGIN SELECT RAISE(ABORT,'immutable result'); END;
CREATE TRIGGER requests_no_delete BEFORE DELETE ON requests
 BEGIN SELECT RAISE(ABORT,'retain reservation'); END;
CREATE TRIGGER requests_final BEFORE UPDATE ON requests WHEN OLD.status!='pending'
 BEGIN SELECT RAISE(ABORT,'immutable response'); END;
CREATE TRIGGER counts_no_delete BEFORE DELETE ON counts
 BEGIN SELECT RAISE(ABORT,'retain counted witness'); END;
CREATE TRIGGER counts_final BEFORE UPDATE ON counts WHEN OLD.status!='pending' AND NOT (
 OLD.status='complete' AND NEW.status=OLD.status AND OLD.consumed=0 AND NEW.consumed=1
 AND OLD.id=NEW.id AND OLD.slot=NEW.slot AND OLD.provider=NEW.provider
 AND OLD.generation_hash=NEW.generation_hash AND OLD.payload_hash=NEW.payload_hash
 AND OLD.input_tokens=NEW.input_tokens AND OLD.estimated_nano=NEW.estimated_nano)
 BEGIN SELECT RAISE(ABORT,'immutable count'); END;
CREATE TRIGGER tools_no_delete BEFORE DELETE ON tools
 BEGIN SELECT RAISE(ABORT,'retain tool intent'); END;
CREATE TRIGGER tools_final BEFORE UPDATE ON tools WHEN OLD.status='complete'
 BEGIN SELECT RAISE(ABORT,'immutable tool result'); END;
"""


def request_counts(rows):
    verified = sum(row["status"] == "complete" for row in rows)
    return {
        "request_attempts_reserved": len(rows),
        "http_responses_observed": sum(row["http_complete"] for row in rows),
        "verified_complete_responses": verified,
        "unknown_actual_outcomes": len(rows) - verified,
        "actual_generation_requests": len(rows) if verified == len(rows) else None,
    }


def accounting(rows):
    observed, partial_input, partial_output = 0, 0, 0
    usage_complete, pricing_verified, responses_valid = True, True, True
    for row in rows:
        observed += row["observed_nano"] or 0
        data = json.loads(row["evidence"]) if row["evidence"] else {}
        usage = data.get("usage", {})
        partial_input += usage.get("known_partial_prompt_tokens") or 0
        partial_output += usage.get("output_tokens") or 0
        usage_complete &= usage.get("complete", False)
        pricing_verified &= data.get("usage_pricing_verified", False)
        responses_valid &= data.get("response_valid", False)
    complete = all(row["status"] == "complete" for row in rows)
    return {
        **request_counts(rows),
        "usage_complete": bool(usage_complete),
        "pricing_identity_verified": bool(pricing_verified),
        "pricing_identity_interpretation": "response_model_tier_counter_regime_only",
        "account_pricing_verified": False,
        "billing_verified": False,
        "reservation_policy": RESERVATION_POLICY,
        "responses_valid": bool(responses_valid),
        "exposure_resolved": complete,
        "known_partial_prompt_tokens": partial_input,
        "known_partial_output_tokens": partial_output,
        "known_observed_estimate_nano_usd": observed,
        "estimated_total_nano_usd": observed if complete else None,
        "unresolved_reservation_nano_usd": sum(
            row["reserved_nano"] for row in rows if row["status"] != "complete"
        ),
        "unresolved_exposure_nano_usd": sum(
            max(row["reserved_nano"], row["observed_nano"] or 0)
            for row in rows
            if row["status"] != "complete"
        ),
        "known_observed_excess_nano_usd": sum(
            max(0, (row["observed_nano"] or 0) - row["reserved_nano"]) for row in rows
        ),
    }


def count_accounting(rows):
    complete = sum(row["status"] == "complete" for row in rows)
    return {
        "count_attempts_reserved": len(rows),
        "verified_count_responses": complete,
        "actual_count_requests": len(rows) if complete == len(rows) else None,
        "unknown_count_outcomes": len(rows) - complete,
    }


class Comparison:
    def __init__(self, path):
        self.path = Path(path).resolve()
        self._owner = None
        with self.connect() as db:
            try:
                if db.execute("PRAGMA user_version").fetchone()[0] != 2:
                    raise ValueError
                self.policy = json.loads(db.execute("SELECT data FROM policy").fetchone()[0])
                self.limits = ComparisonLimits.model_validate(self.policy["limits"])
                if (
                    self.policy["root"] != str(self.path.parent)
                    or self.policy["entries"] != paired_plan()
                    or self.policy["rates"] != RATES
                    or self.policy.get("reservation_policy") != RESERVATION_POLICY
                    or self.policy["models"] != MODELS
                    or self.policy["tools"] != tool_schemas()
                    or self.policy["final_schema"] != final_schema()
                    or self.policy["instructions"] != INSTRUCTIONS
                    or self.policy["live_blockers"] != list(LIVE_BLOCKERS)
                ):
                    raise ValueError
                db.execute("SELECT id,evidence,status FROM requests")
                if db.execute("SELECT count(*) FROM campaign").fetchone()[0] != 1:
                    raise ValueError
                self._policy_hash = digest(self.policy)
            except (ValueError, TypeError, KeyError, sqlite3.Error):
                raise BudgetDenied("comparison_history_unavailable") from None

    @contextmanager
    def connect(self, *, write=False):
        # mode=rw never creates a replacement journal. Bounded lock waits in cleanup.
        db = sqlite3.connect(
            self.path.as_uri() + ("?mode=rw" if write else "?mode=ro"), uri=True, timeout=0.1
        )
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield db
            if write:
                db.commit()
        finally:
            db.close()

    @property
    def policy_hash(self):
        return digest(self.policy)

    def verify_source(self):
        if (
            digest(self.policy) != self._policy_hash
            or self.limits.model_dump() != self.policy["limits"]
            or source_witness() != self.policy["source"]
            or dependencies() != self.policy["dependencies"]
            or require_pilot_runtime() != self.policy["runtime"]
        ):
            raise BudgetDenied("comparison_source_or_runtime_changed")

    def verify_states(self):
        expected = dict(self.policy["initial_states"])
        with self.connect() as db:
            rows = db.execute("SELECT * FROM episodes ORDER BY slot").fetchall()
        for slot, row in enumerate(rows):
            entry = self.policy["entries"][slot]
            if (
                row["slot"] != slot
                or row["id"] != entry["id"]
                or row["status"] != "finished"
                or row["witness"] is None
                or row["result"] is None
            ):
                raise BudgetDenied("comparison_incomplete_history")
            expected[entry["fixture"]] = json.loads(row["witness"])
        for fixture, witness in expected.items():
            if state_witness(self.path.parent / "states" / fixture) != witness:
                raise BudgetDenied("comparison_fixture_changed")

    def activate(self, *, policy_hash, mode="http_mock_only"):
        if mode != self.limits.execution_mode or policy_hash != self.policy_hash:
            raise BudgetDenied("comparison_live_admission_blocked")
        if mode == "live_simulator":
            from .comparison_live import validate_generation_review

            validate_generation_review(self.policy.get("live_review"))
        self.verify_source()
        self.verify_states()
        owner = uuid4().hex
        with self.connect(write=True) as db:
            if (
                db.execute(
                    "UPDATE campaign SET status='running',owner=?,reason='active' "
                    "WHERE status='prepared' AND owner IS NULL AND next_slot=0",
                    (owner,),
                ).rowcount
                != 1
            ):
                raise BudgetDenied("comparison_owner_already_claimed")
        self._owner = owner

    def claim_wire(self, provider, request):
        """One durable send permission for an already reserved count/generation."""
        from .comparison_protocol import COUNT_ENDPOINTS, ENDPOINTS

        self.verify_source()
        if self.limits.execution_mode != "live_simulator" or request.method != "POST":
            raise BudgetDenied("comparison_live_admission_blocked")
        endpoint = str(request.url)
        if endpoint == COUNT_ENDPOINTS.get(provider):
            kind, table, field = "count", "counts", "payload_hash"
        elif endpoint == ENDPOINTS.get(provider):
            kind, table, field = "generation", "requests", "body_hash"
        else:
            raise BudgetDenied("comparison_endpoint_rejected")
        body_hash = hashlib.sha256(request.content).hexdigest()
        claim = uuid4().hex
        with self.connect(write=True) as db:
            state = self._owned(db)
            row = db.execute(
                f"SELECT id FROM {table} WHERE slot=? AND provider=? "
                f"AND status='pending' AND {field}=?",
                (state["next_slot"], provider, body_hash),
            ).fetchone()
            if row is None:
                raise BudgetDenied("comparison_unreserved_dispatch")
            db.execute(
                "INSERT INTO wire_attempts VALUES(?,?,?,?,?,?,'dispatch_authorized',NULL,NULL)",
                (claim, kind, row["id"], provider, endpoint, body_hash),
            )
        return claim

    def wire_status(self, claim, status):
        with self.connect(write=True) as db:
            self._owned(db)
            if (
                db.execute(
                    "UPDATE wire_attempts SET state='headers_received',http_status=? "
                    "WHERE id=? AND state='dispatch_authorized'",
                    (status, claim),
                ).rowcount
                != 1
            ):
                raise BudgetDenied("comparison_dispatch_not_owned")

    def wire_failure(self, claim, code):
        # Codes are fixed by the transport, never exception text or remote bodies.
        if code not in ("cancelled", "transport_failed", "close_failed"):
            raise BudgetDenied("comparison_invalid_error_code")
        with self.connect(write=True) as db:
            state = db.execute("SELECT owner FROM campaign").fetchone()
            if self._owner is None or state["owner"] != self._owner:
                raise BudgetDenied("comparison_not_owned_or_stopped")
            db.execute(
                "UPDATE wire_attempts SET state='uncertain',error_code=? WHERE id=?", (code, claim)
            )
            db.execute(
                "UPDATE campaign SET status='stopped',reason='transport_uncertain' "
                "WHERE status='running' AND owner=?",
                (self._owner,),
            )

    def _owned(self, db, slot=None):
        state = db.execute("SELECT * FROM campaign").fetchone()
        if (
            self._owner is None
            or state["owner"] != self._owner
            or state["status"] != "running"
            or (slot is not None and state["next_slot"] != slot)
        ):
            raise BudgetDenied("comparison_not_owned_or_stopped")
        return state

    def begin(self, entry):
        self.verify_source()
        self.verify_states()
        if entry != self.policy["entries"][entry["slot"]]:
            raise BudgetDenied("comparison_schedule_changed")
        with self.connect(write=True) as db:
            self._owned(db, entry["slot"])
            if db.execute("SELECT 1 FROM episodes WHERE status='running'").fetchone():
                raise BudgetDenied("comparison_episode_incomplete")
            db.execute(
                "INSERT INTO episodes(slot,id,provider,status) VALUES(?,?,?,'running')",
                (entry["slot"], entry["id"], entry["provider"]),
            )

    def _admit(self, db, entry, reservation=0):
        self._owned(db, entry["slot"])
        if entry != self.policy["entries"][entry["slot"]]:
            raise BudgetDenied("comparison_schedule_changed")
        episode = db.execute("SELECT * FROM episodes WHERE slot=?", (entry["slot"],)).fetchone()
        if (
            not episode
            or episode["id"] != entry["id"]
            or episode["provider"] != entry["provider"]
            or episode["status"] != "running"
        ):
            raise BudgetDenied("comparison_episode_not_owned")
        if (
            db.execute("SELECT 1 FROM requests WHERE status!='complete'").fetchone()
            or db.execute("SELECT 1 FROM tools WHERE status='pending'").fetchone()
        ):
            raise BudgetDenied("comparison_incomplete_history")
        rows = db.execute("SELECT * FROM requests").fetchall()
        provider = entry["provider"]
        arm = [r for r in rows if r["provider"] == provider]
        episode_rows = [r for r in rows if r["slot"] == entry["slot"]]
        limits = self.limits
        if (
            len(rows) >= limits.requests_total
            or len(arm) >= limits.requests_per_provider
            or len(episode_rows) >= limits.requests_per_episode
        ):
            raise BudgetDenied("comparison_request_limit")
        if (
            sum(r["observed_nano"] for r in arm) + reservation
            > getattr(limits, provider + "_ceiling_nano")
            or sum(r["observed_nano"] for r in rows) + reservation > limits.total_ceiling_nano
        ):
            raise BudgetDenied("comparison_estimate_ceiling")
        return len(episode_rows) + 1, reservation

    def begin_count(self, entry, generation, body):
        self.verify_source()
        if (
            len(body) > self.limits.max_request_bytes
            or len(canonical(generation).encode()) > self.limits.max_request_bytes
        ):
            raise BudgetDenied("comparison_serialized_request_limit")
        if canonical(json.loads(body)) != canonical(
            count_request_body(entry["provider"], generation)
        ):
            raise BudgetDenied("comparison_count_payload_mismatch")
        count_id = uuid4().hex
        with self.connect(write=True) as db:
            self._admit(db, entry)
            if db.execute("SELECT 1 FROM counts WHERE status!='complete' OR consumed=0").fetchone():
                raise BudgetDenied("comparison_count_incomplete")
            # Counts have their own durable ceilings, never count as generations.
            rows = db.execute("SELECT slot,provider FROM counts").fetchall()
            if (
                len(rows) >= self.limits.requests_total
                or sum(r["provider"] == entry["provider"] for r in rows)
                >= self.limits.requests_per_provider
                or sum(r["slot"] == entry["slot"] for r in rows) >= self.limits.requests_per_episode
            ):
                raise BudgetDenied("comparison_count_limit")
            db.execute(
                "INSERT INTO counts(id,slot,provider,generation_hash,payload_hash,status) "
                "VALUES(?,?,?,?,?,'pending')",
                (
                    count_id,
                    entry["slot"],
                    entry["provider"],
                    digest(generation),
                    hashlib.sha256(body).hexdigest(),
                ),
            )
        return count_id

    def finish_count(self, entry, count_id, payload):
        expected = {"input_tokens", "object"} if entry["provider"] == "openai" else {"input_tokens"}
        value = payload.get("input_tokens") if type(payload) is dict else None
        good = (
            type(payload) is dict
            and set(payload) == expected
            and type(value) is int
            and 0 <= value <= (1_050_000 if entry["provider"] == "openai" else 1_000_000)
            and (entry["provider"] != "openai" or payload.get("object") == "response.input_tokens")
        )
        with self.connect(write=True) as db:
            self._owned(db, entry["slot"])
            if (
                db.execute(
                    "UPDATE counts SET status=?,input_tokens=?,estimated_nano=? "
                    "WHERE id=? AND slot=? AND status='pending'",
                    (
                        "complete" if good else "uncertain",
                        value if type(value) is int and 0 <= value <= 10**9 else None,
                        counted_estimate(entry["provider"], value) if good else None,
                        count_id,
                        entry["slot"],
                    ),
                ).rowcount
                != 1
            ):
                raise BudgetDenied("comparison_count_not_owned")
            if not good:
                db.execute("UPDATE campaign SET status='stopped',reason='count_unverified'")
        return good

    def reserve(self, entry, body, transcript, count_id):
        self.verify_source()
        if type(body) is not bytes or len(body) > self.limits.max_request_bytes:
            raise BudgetDenied("comparison_serialized_request_limit")
        request_id = uuid4().hex
        with self.connect(write=True) as db:
            ordinal, _ = self._admit(db, entry)
            count = db.execute(
                "SELECT * FROM counts WHERE id=? AND slot=? AND status='complete' AND consumed=0",
                (count_id, entry["slot"]),
            ).fetchone()
            if not count or count["generation_hash"] != digest(json.loads(body)):
                raise BudgetDenied("comparison_stale_or_mismatched_count")
            reservation = counted_estimate(entry["provider"], count["input_tokens"])
            self._admit(db, entry, reservation)
            db.execute("UPDATE counts SET consumed=1 WHERE id=?", (count_id,))
            db.execute(
                "INSERT INTO requests(id,slot,provider,ordinal,body_hash,body_bytes,"
                "transcript,reserved_nano,status) "
                "VALUES(?,?,?,?,?,?,?,?,'pending')",
                (
                    request_id,
                    entry["slot"],
                    entry["provider"],
                    ordinal,
                    hashlib.sha256(body).hexdigest(),
                    len(body),
                    canonical(transcript),
                    reservation,
                ),
            )
        return request_id

    def observe(self, entry, request_id, evidence):
        settlement_hash = digest(evidence)
        evidence = json.loads(canonical(evidence))
        conflict = False
        with self.connect(write=True) as db:
            row = db.execute(
                "SELECT * FROM requests WHERE id=? AND slot=?",
                (request_id, entry["slot"]),
            ).fetchone()
            if row is None or row["provider"] != entry["provider"]:
                raise BudgetDenied("comparison_response_not_owned")
            if row["status"] != "pending":
                if row["settlement_hash"] == settlement_hash or row["evidence"] == canonical(
                    evidence
                ):
                    # No mutation; also after stop/finish/reopen.
                    return row["status"] == "complete"
                db.execute(
                    "UPDATE campaign SET status=CASE WHEN status='running' "
                    "THEN 'stopped' ELSE status END,"
                    "reason='conflicting_usage_replay'"
                )
                conflict = True
            else:
                self._owned(db, entry["slot"])
                tool_total = db.execute(
                    "SELECT coalesce(sum(tool_attempts),0) FROM requests WHERE slot=?",
                    (entry["slot"],),
                ).fetchone()[0]
                cost = prospective_observed_estimate(entry["provider"], evidence["usage"])
                if tool_total + evidence["tool_attempts"] > self.limits.tools_per_episode:
                    evidence["control_error"] = "comparison_tool_limit"
                if cost is not None and cost > row["reserved_nano"]:
                    evidence["control_error"] = "comparison_conditional_backstop_exceeded"
                good = evidence["control_error"] is None
                db.execute(
                    "UPDATE requests SET status=?,http_complete=1,evidence=?,settlement_hash=?,"
                    "observed_nano=?,tool_attempts=? WHERE id=?",
                    (
                        "complete" if good else "uncertain",
                        canonical(evidence),
                        settlement_hash,
                        cost,
                        evidence["tool_attempts"],
                        request_id,
                    ),
                )
                if not good:
                    db.execute("UPDATE campaign SET status='stopped',reason='response_uncertain'")
        if conflict:
            raise BudgetDenied("comparison_conflicting_usage_replay")
        return good

    def tool_intent(self, entry, request_id, call):
        with self.connect(write=True) as db:
            self._owned(db, entry["slot"])
            row = db.execute(
                "SELECT evidence,status FROM requests WHERE id=? AND slot=?",
                (request_id, entry["slot"]),
            ).fetchone()
            if (
                not row
                or row["status"] != "complete"
                or json.loads(row["evidence"])["call"] != call
            ):
                raise BudgetDenied("comparison_tool_not_owned")
            db.execute(
                "INSERT INTO tools VALUES(?,?,?,?,'pending',NULL)",
                (request_id, entry["slot"], call["call_id"], call["name"]),
            )

    def tool_result(self, entry, request_id, result):
        with self.connect(write=True) as db:
            self._owned(db, entry["slot"])
            if (
                db.execute(
                    "UPDATE tools SET status='complete',result=? "
                    "WHERE request_id=? AND slot=? AND status='pending'",
                    (canonical(result), request_id, entry["slot"]),
                ).rowcount
                != 1
            ):
                raise BudgetDenied("comparison_tool_result_not_owned")

    def finish(self, entry, result, witness):
        with self.connect(write=True) as db:
            state = db.execute("SELECT * FROM campaign").fetchone()
            if self._owner is None or state["owner"] != self._owner:
                raise BudgetDenied("comparison_result_not_owned")
            if result["control_passed"]:
                self._owned(db, entry["slot"])
                if (
                    db.execute("SELECT 1 FROM requests WHERE status!='complete'").fetchone()
                    or db.execute("SELECT 1 FROM tools WHERE status='pending'").fetchone()
                ):
                    raise BudgetDenied("comparison_incomplete_history")
            else:
                db.execute(
                    "UPDATE campaign SET status='stopped',reason='episode_interrupted_or_unsafe'"
                )
            if (
                db.execute(
                    "UPDATE episodes SET status='finished',result=?,witness=? "
                    "WHERE slot=? AND status='running'",
                    (canonical(result), canonical(witness), entry["slot"]),
                ).rowcount
                != 1
            ):
                raise BudgetDenied("comparison_result_already_recorded")
            if result["control_passed"]:
                next_slot = entry["slot"] + 1
                db.execute(
                    "UPDATE campaign SET next_slot=?,status=?,reason=?",
                    (
                        next_slot,
                        "finished" if next_slot == 48 else "running",
                        "schedule_complete" if next_slot == 48 else "active",
                    ),
                )

    def stop(self):
        with self.connect(write=True) as db:
            db.execute(
                "UPDATE campaign SET status='stopped',reason='interrupted' "
                "WHERE status='running' AND owner=?",
                (self._owner,),
            )

    def report(self):
        # One explicit read transaction: request totals, owner and results cannot mix epochs.
        with self.connect() as db:
            state = dict(db.execute("SELECT * FROM campaign").fetchone())
            rows = [dict(r) for r in db.execute("SELECT * FROM requests ORDER BY rowid")]
            episodes = [dict(r) for r in db.execute("SELECT * FROM episodes ORDER BY slot")]
            tools = [dict(r) for r in db.execute("SELECT * FROM tools ORDER BY rowid")]
            counts = [dict(r) for r in db.execute("SELECT * FROM counts ORDER BY rowid")]
            wire = [dict(r) for r in db.execute("SELECT * FROM wire_attempts ORDER BY rowid")]
        state.pop("owner")
        return {
            "mode": self.limits.execution_mode,
            "policy_hash": self.policy_hash,
            "campaign": state,
            "blocked": state["status"] in ("stopped", "finished")
            or (state["status"] == "running" and self._owner is None),
            "run_complete": state["status"] == "finished",
            "live_admission": "separately_enrolled"
            if self.limits.execution_mode == "live_simulator"
            else "blocked",
            "live_blockers": list(LIVE_BLOCKERS)
            if self.limits.execution_mode == "http_mock_only"
            else [],
            "reservation_basis": "count_plus_assumed_margin_PLACEHOLDER_prices",
            "reservation_policy": RESERVATION_POLICY,
            "uncounted_fallbacks_nano_usd": {p: backstop(p) for p in PROVIDERS},
            "human_review": "pending",
            "model_quality_established": False,
            **accounting(rows),
            **count_accounting(counts),
            "providers": {
                p: {
                    **accounting([r for r in rows if r["provider"] == p]),
                    **count_accounting([r for r in counts if r["provider"] == p]),
                }
                for p in PROVIDERS
            },
            "episodes": {
                e["id"]: {
                    **accounting([r for r in rows if r["slot"] == e["slot"]]),
                    **count_accounting([r for r in counts if r["slot"] == e["slot"]]),
                    "status": e["status"],
                    "result": json.loads(e["result"]) if e["result"] else None,
                }
                for e in episodes
            },
            "unattempted": [
                e["id"]
                for e in self.policy["entries"]
                if e["slot"] not in {r["slot"] for r in episodes}
            ],
            "requests": [
                {
                    **r,
                    "transcript": json.loads(r["transcript"]),
                    "evidence": json.loads(r["evidence"]) if r["evidence"] else None,
                }
                for r in rows
            ],
            "tools": [
                {**r, "result": json.loads(r["result"]) if r["result"] else None} for r in tools
            ],
            "count_attempts": counts,
            "wire_attempts": wire,
        }


async def initialize_comparison(directory, *, live_review=None, **lower_limits):
    proof = require_pilot_runtime()
    limits = ComparisonLimits(**lower_limits)
    if limits.execution_mode == "live_simulator":
        from .comparison_live import validate_generation_review

        live_review = validate_generation_review(live_review)
    elif live_review is not None:
        raise BudgetDenied("comparison_mock_live_review_rejected")
    root = Path(directory).resolve()
    root.mkdir(mode=0o700)  # Explicit fresh root; even interrupted initialization is not reusable.
    entries, initial = paired_plan(), {}
    for entry in entries:
        fixture = entry["fixture"]
        if fixture in initial:
            continue
        path = root / "states" / fixture
        runtime = Runtime(
            path,
            payment_fault=PaymentFault.TIMEOUT_BEFORE_COMMIT
            if entry["seed"] == "uncertain"
            else PaymentFault.NONE,
            delivery_fault=DeliveryFault.UNAVAILABLE
            if entry["seed"] == "failed_delivery"
            else DeliveryFault.NONE,
        )
        grant = Grant(
            grant_id=fixture,
            operator_id="paired-evaluation-operator",
            service_ids=tuple(entry["services"]),
            purpose="research",
            budget=entry["budget"],
            per_purchase_limit=entry["cap"],
            issued_at=runtime.clock.now(),
            expires_at=runtime.clock.now() + 3600,
        )
        runtime.ledger.add_grant(grant, runtime.clock.now())
        runtime.ledger.create_task(entry["task_id"], fixture, runtime.clock.now())
        goal = Goal.model_validate(entry["goal"])
        AgentStore(path / "agent.sqlite3").initialize(runtime.ledger, entry["task_id"], goal)
        if entry["seed"] != "empty":
            request = PurchaseRequest(
                service_id="report-standard", subject=goal.subject, purpose=goal.purpose
            )
            session = runtime.buyer(entry["task_id"])
            await session.purchase(request, await session.quote(request))
        initial[fixture] = state_witness(path)
    policy = {
        "limits": limits.model_dump(),
        "entries": entries,
        "root": str(root),
        "initial_states": initial,
        "source": source_witness(),
        "runtime": proof,
        "dependencies": dependencies(),
        "rates": RATES,
        "reservation_policy": RESERVATION_POLICY,
        "models": MODELS,
        "instructions": INSTRUCTIONS,
        "tools": tool_schemas(),
        "final_schema": final_schema(),
        "live_blockers": list(LIVE_BLOCKERS),
        "live_review": live_review,
    }
    path = root / "comparison.sqlite3"
    os.close(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))
    with closing(sqlite3.connect(path)) as db, db:
        db.executescript("BEGIN IMMEDIATE;\n" + SCHEMA)
        db.execute("INSERT INTO policy VALUES(1,?)", (canonical(policy),))
        db.execute("INSERT INTO campaign VALUES(1,'prepared',NULL,0,'not_activated')")
        db.execute("PRAGMA user_version=2")
    return Comparison(path)
