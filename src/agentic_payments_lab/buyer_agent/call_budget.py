"""Explicit offline call-budget journal. Owns no purchase or payment authority.

Only fresh, operator-initialized batches exist. Reopen never initializes, resumes,
releases an uncertain reservation, or resets a counter. Costs use a labelled
synthetic or sourced card in USD nanounits, not provider billing guarantees.
"""

import hashlib
import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated, Literal
from uuid import uuid4

from pydantic import Field, model_validator

from ..domain import canonical
from ..x402_contracts import StrictModel
from .pilot_pricing import LunaRates

Count = Annotated[int, Field(strict=True, ge=0, le=1_000_000_000)]
Positive = Annotated[int, Field(strict=True, ge=1, le=1_000_000)]
Identifier = Annotated[str, Field(min_length=1, max_length=100, pattern=r"^[\w.:-]+$")]


class PlanningRates(StrictModel):
    source: Literal["synthetic_planning"] = "synthetic_planning"
    input_nano_usd: Positive
    cached_input_nano_usd: Count
    cache_write_surcharge_nano_usd: Count
    output_nano_usd: Positive

    @model_validator(mode="after")
    def conservative_discount(self):
        if self.cached_input_nano_usd > self.input_nano_usd:
            raise ValueError("Cached rate cannot exceed the uncached planning rate")
        return self


class CallLimits(StrictModel):
    batch_id: Identifier
    model: Identifier
    reasoning_effort: Literal["none", "minimal", "low", "medium", "high"] = "none"
    service_tier: Literal["default"] = "default"
    max_invocations: Annotated[int, Field(strict=True, ge=1, le=3)] = 3
    requests_per_invocation: Annotated[int, Field(strict=True, ge=1, le=6)] = 6
    max_requests: Annotated[int, Field(strict=True, ge=1, le=18)] = 18
    max_request_bytes: Annotated[int, Field(strict=True, ge=1, le=65_536)] = 32_768
    max_output_tokens: Annotated[int, Field(strict=True, ge=128, le=512)] = 512
    tool_attempts: Annotated[int, Field(strict=True, ge=1, le=8)] = 8
    timeout_seconds: Annotated[int, Field(strict=True, ge=1, le=30)] = 30
    # Explicit estimation assumptions, not a tokenizer or exact token upper bound.
    input_tokens_per_byte: Annotated[int, Field(strict=True, ge=1, le=4)] = 1
    framing_tokens: Annotated[int, Field(strict=True, ge=0, le=8192)] = 1024
    estimate_ceiling_nano_usd: Annotated[int, Field(strict=True, ge=0, le=10**15)]
    rates: PlanningRates | LunaRates

    @model_validator(mode="after")
    def direct_luna_assumptions(self):
        if isinstance(self.rates, LunaRates) and (
            self.model != "gpt-6-luna"
            or self.reasoning_effort != "none"
            or self.max_request_bytes > 32_768
            or self.input_tokens_per_byte != 1
            or self.framing_tokens != 1024
        ):
            raise ValueError("Unsupported direct Luna planning assumptions")
        return self


SCHEMA = """
CREATE TABLE policy (singleton INTEGER PRIMARY KEY CHECK(singleton=1), data TEXT NOT NULL);
CREATE TABLE invocations (
 id TEXT PRIMARY KEY, owner TEXT NOT NULL UNIQUE,
 status TEXT NOT NULL CHECK(status IN ('running','finished')),
 outcome TEXT NOT NULL
);
CREATE TABLE requests (
 id TEXT PRIMARY KEY, invocation TEXT NOT NULL REFERENCES invocations(id),
 ordinal INTEGER NOT NULL, body_hash TEXT NOT NULL, body_bytes INTEGER NOT NULL,
 estimated_input_tokens INTEGER NOT NULL, reserved_nano INTEGER NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('pending','complete','incomplete','overrun','unverified')),
 reason TEXT NOT NULL, usage TEXT, observed_estimate_nano INTEGER,
 requested_tier TEXT NOT NULL, returned_tier TEXT, returned_model TEXT,
 UNIQUE(invocation,ordinal)
);
CREATE TRIGGER policy_no_update BEFORE UPDATE ON policy
BEGIN SELECT RAISE(ABORT,'immutable policy'); END;
CREATE TRIGGER policy_no_delete BEFORE DELETE ON policy
BEGIN SELECT RAISE(ABORT,'immutable policy'); END;
CREATE TRIGGER requests_no_delete BEFORE DELETE ON requests
BEGIN SELECT RAISE(ABORT,'retain request history'); END;
CREATE TRIGGER invocations_no_delete BEFORE DELETE ON invocations
BEGIN SELECT RAISE(ABORT,'retain invocation history'); END;
"""


class BudgetDenied(RuntimeError):
    """Fixed application codes only; never includes prompts, responses or exceptions."""


def usage_evidence(raw, *, cache_lifetimes=False) -> tuple[dict, bool]:
    """Retain individually valid observed categories; missing is None, never zero."""
    raw = raw if type(raw) is dict else {}
    inputs = raw.get("input_tokens_details")
    outputs = raw.get("output_tokens_details")
    inputs = inputs if type(inputs) is dict else {}
    outputs = outputs if type(outputs) is dict else {}
    values = {
        "input_tokens": raw.get("input_tokens"),
        "output_tokens": raw.get("output_tokens"),
        "total_tokens": raw.get("total_tokens"),
        "cached_tokens": inputs.get("cached_tokens"),
        "cache_write_tokens": inputs.get("cache_write_tokens"),
        "reasoning_tokens": outputs.get("reasoning_tokens"),
    }
    lifetimes = inputs.get("cache_write_tokens_details")
    lifetimes = lifetimes if type(lifetimes) is dict else {}
    if cache_lifetimes:
        values.update(
            ttl_30m_tokens=lifetimes.get("ttl_30m_tokens"),
            ttl_12h_tokens=lifetimes.get("ttl_12h_tokens"),
        )
    known = {k: v if type(v) is int and 0 <= v <= 10**9 else None for k, v in values.items()}
    complete = (
        all(v is not None for v in known.values())
        and set(raw)
        == {
            "input_tokens",
            "output_tokens",
            "total_tokens",
            "input_tokens_details",
            "output_tokens_details",
        }
        and set(inputs)
        == {"cached_tokens", "cache_write_tokens"}
        | ({"cache_write_tokens_details"} if cache_lifetimes else set())
        and set(outputs) == {"reasoning_tokens"}
        and (not cache_lifetimes or set(lifetimes) == {"ttl_30m_tokens", "ttl_12h_tokens"})
    )
    if complete:
        complete = bool(
            known["total_tokens"] == known["input_tokens"] + known["output_tokens"]
            and known["cached_tokens"] <= known["input_tokens"]
            and known["cache_write_tokens"] <= known["input_tokens"]
            and known["cached_tokens"] + known["cache_write_tokens"] <= known["input_tokens"]
            and known["reasoning_tokens"] <= known["output_tokens"]
            and (
                not cache_lifetimes
                or known["ttl_30m_tokens"] + known["ttl_12h_tokens"] == known["cache_write_tokens"]
            )
        )
    return known, complete


class CallBudget:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._owners: dict[str, str] = {}
        with self.connect() as db:
            db.execute("BEGIN")
            if db.execute("PRAGMA user_version").fetchone()[0] != 1:
                raise BudgetDenied("budget_history_unavailable")
            try:
                row = db.execute("SELECT data FROM policy WHERE singleton=1").fetchone()
                self.limits = CallLimits.model_validate_json(row[0])
                db.execute("SELECT id,owner,status FROM invocations LIMIT 1")
                db.execute("SELECT id,status FROM requests LIMIT 1")
            except (sqlite3.Error, TypeError, ValueError):
                raise BudgetDenied("budget_history_unavailable") from None

    @classmethod
    def initialize(cls, path: Path, limits: CallLimits):
        limits = CallLimits.model_validate(limits.model_dump())
        path = Path(path)
        # O_EXCL forbids overwriting/resetting an existing or incomplete batch.
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
        with sqlite3.connect(path) as db:
            db.executescript("BEGIN IMMEDIATE;\n" + SCHEMA)
            db.execute("INSERT INTO policy VALUES(1,?)", (canonical(limits),))
            db.execute("PRAGMA user_version=1")
        return cls(path)

    @contextmanager
    def connect(self, *, write=False):
        try:
            db = sqlite3.connect(self.path.resolve().as_uri() + "?mode=rw", uri=True, timeout=0.1)
        except sqlite3.Error:
            raise BudgetDenied("budget_history_unavailable") from None
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA foreign_keys=ON")
            if write:
                db.execute("BEGIN IMMEDIATE")
            yield db
            if write:
                db.commit()
        finally:
            db.close()  # Uncommitted writes roll back, including process interruptions.

    @staticmethod
    def _uncertain(db):
        return db.execute("SELECT 1 FROM requests WHERE status!='complete' LIMIT 1").fetchone()

    def begin_invocation(self, invocation_id: str) -> None:
        if type(invocation_id) is not str or not 1 <= len(invocation_id) <= 100:
            raise BudgetDenied("invalid_invocation_id")
        owner = uuid4().hex
        with self.connect(write=True) as db:
            if self._uncertain(db):
                raise BudgetDenied("request_accounting_incomplete")
            if db.execute("SELECT 1 FROM invocations WHERE status='running'").fetchone():
                raise BudgetDenied("invocation_incomplete")
            if db.execute("SELECT 1 FROM invocations WHERE id=?", (invocation_id,)).fetchone():
                raise BudgetDenied("invocation_already_claimed")
            count = db.execute("SELECT count(*) FROM invocations").fetchone()[0]
            if count >= self.limits.max_invocations:
                raise BudgetDenied("invocation_limit")
            db.execute(
                "INSERT INTO invocations VALUES(?,?,'running','pending')", (invocation_id, owner)
            )
        # A lost commit acknowledgement intentionally leaves no reusable capability.
        self._owners[invocation_id] = owner

    def finish_invocation(self, invocation_id: str, outcome: str) -> None:
        if outcome not in ("completed", "stopped", "cancelled", "timeout"):
            raise BudgetDenied("invalid_invocation_outcome")
        with self.connect(write=True) as db:
            if (
                db.execute(
                    "UPDATE invocations SET status='finished',outcome=? "
                    "WHERE id=? AND owner=? AND status='running'",
                    (outcome, invocation_id, self._owners.get(invocation_id)),
                ).rowcount
                != 1
            ):
                raise BudgetDenied("invocation_not_running")

    def reserve(self, invocation_id: str, body: bytes) -> str:
        limits = self.limits
        if type(body) is not bytes or len(body) > limits.max_request_bytes:
            raise BudgetDenied("serialized_request_limit")
        tokens = len(body) * limits.input_tokens_per_byte + limits.framing_tokens
        input_rate = (
            limits.rates.write_12h_nano_usd
            if isinstance(limits.rates, LunaRates)
            else limits.rates.input_nano_usd + limits.rates.cache_write_surcharge_nano_usd
        )
        amount = tokens * input_rate + limits.max_output_tokens * limits.rates.output_nano_usd
        request_id = uuid4().hex
        with self.connect(write=True) as db:
            running = db.execute(
                "SELECT status,owner FROM invocations WHERE id=?", (invocation_id,)
            ).fetchone()
            if (
                not running
                or running[0] != "running"
                or running[1] != self._owners.get(invocation_id)
            ):
                raise BudgetDenied("invocation_not_running")
            if self._uncertain(db):
                raise BudgetDenied("request_accounting_incomplete")
            total, consumed = db.execute(
                "SELECT count(*),coalesce(sum(observed_estimate_nano),0) FROM requests"
            ).fetchone()
            count = db.execute(
                "SELECT count(*) FROM requests WHERE invocation=?", (invocation_id,)
            ).fetchone()[0]
            if total >= limits.max_requests or count >= limits.requests_per_invocation:
                raise BudgetDenied("request_limit")
            if consumed + amount > limits.estimate_ceiling_nano_usd:
                raise BudgetDenied("planning_budget_exhausted")
            db.execute(
                "INSERT INTO requests VALUES(?,?,?,?,?,?,?,'pending','awaiting_usage',NULL,"
                "NULL,?,NULL,NULL)",
                (
                    request_id,
                    invocation_id,
                    count + 1,
                    hashlib.sha256(body).hexdigest(),
                    len(body),
                    tokens,
                    amount,
                    limits.service_tier,
                ),
            )
        return request_id

    def observe(self, request_id: str, raw_usage, returned_tier, returned_model) -> bool:
        direct_luna = isinstance(self.limits.rates, LunaRates)
        usage, complete = usage_evidence(raw_usage, cache_lifetimes=direct_luna)
        # Unknown/malformed categories are retained without inventing zero usage.
        rates = self.limits.rates
        estimate = None
        identity_verified = (
            type(returned_tier) is str
            and returned_tier == self.limits.service_tier
            and type(returned_model) is str
            and returned_model == self.limits.model
        )
        price_verified = not direct_luna or (complete and usage["input_tokens"] <= 272_000)
        if complete and identity_verified and price_verified and direct_luna:
            estimate = rates.estimate(usage)
        elif complete and identity_verified and price_verified:
            estimate = (
                (usage["input_tokens"] - usage["cached_tokens"]) * rates.input_nano_usd
                + usage["cached_tokens"] * rates.cached_input_nano_usd
                + usage["cache_write_tokens"] * rates.cache_write_surcharge_nano_usd
                + usage["output_tokens"] * rates.output_nano_usd
            )
        tier = returned_tier if returned_tier in ("default", "flex", "priority", "scale") else None
        with self.connect(write=True) as db:
            row = db.execute("SELECT * FROM requests WHERE id=?", (request_id,)).fetchone()
            if not row or row["status"] != "pending":
                raise BudgetDenied("request_already_resolved_or_missing")
            owner = db.execute(
                "SELECT owner FROM invocations WHERE id=?", (row["invocation"],)
            ).fetchone()
            if owner[0] != self._owners.get(row["invocation"]):
                raise BudgetDenied("request_owner_missing")
            status = "complete" if complete else "incomplete"
            reason = "observed_usage" if complete else "usage_incomplete"
            if complete and not identity_verified:
                status, reason = "unverified", "response_identity_unverified"
            elif complete and not price_verified:
                status, reason = "unverified", "pricing_assumptions_unverified"
            elif complete and (
                estimate > row["reserved_nano"]
                or usage["input_tokens"] > row["estimated_input_tokens"]
                or usage["output_tokens"] > self.limits.max_output_tokens
            ):
                status, reason = "overrun", "planning_assumptions_exceeded"
            db.execute(
                "UPDATE requests SET status=?,reason=?,usage=?,observed_estimate_nano=?,"
                "returned_tier=?,returned_model=? WHERE id=?",
                (
                    status,
                    reason,
                    canonical(usage),
                    estimate,
                    tier,
                    self.limits.model
                    if type(returned_model) is str and returned_model == self.limits.model
                    else None,
                    request_id,
                ),
            )
        return status == "complete"

    def interrupt(self, request_id: str, reason: str) -> None:
        if reason not in ("cancelled", "timeout", "transport_error", "response_invalid"):
            raise BudgetDenied("invalid_request_outcome")
        with self.connect(write=True) as db:
            db.execute(
                "UPDATE requests SET status='incomplete',reason=? WHERE id=? AND status='pending'",
                (reason, request_id),
            )

    def report(self) -> dict:
        with self.connect() as db:
            db.execute("BEGIN")
            invocations = [
                dict(r)
                for r in db.execute("SELECT id,status,outcome FROM invocations ORDER BY rowid")
            ]
            requests = [dict(r) for r in db.execute("SELECT * FROM requests ORDER BY rowid")]
        for row in requests:
            row["usage"] = json.loads(row["usage"]) if row["usage"] is not None else None
        incomplete = any(r["status"] != "complete" for r in requests)
        return {
            "mode": "offline_planning",
            "billing_verified": False,
            "policy": self.limits.model_dump(mode="json"),
            "invocations": invocations,
            "requests": requests,
            "usage_complete": all(
                r["status"] in ("complete", "overrun", "unverified") for r in requests
            ),
            "cache_write_lifetime": "documented_30m_and_12h"
            if isinstance(self.limits.rates, LunaRates)
            else "unknown_in_pinned_usage_schema",
            "accounting_complete": not incomplete,
            "blocked": incomplete or any(i["status"] == "running" for i in invocations),
            "known_estimate_nano_usd": (
                None
                if requests and all(r["observed_estimate_nano"] is None for r in requests)
                else sum(r["observed_estimate_nano"] or 0 for r in requests)
            ),
            "estimated_total_nano_usd": None
            if incomplete
            else sum(r["observed_estimate_nano"] or 0 for r in requests),
            "reserved_estimate_nano_usd": sum(
                max(r["reserved_nano"], r["observed_estimate_nano"] or 0)
                for r in requests
                if r["status"] != "complete"
            ),
        }
