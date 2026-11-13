"""Six non-generating probes, explicitly enrolled once; no retry/resume or key discovery."""

import asyncio
import hashlib
import json
import os
import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path
from uuid import uuid4

import httpx2
from openai import APIResponseValidationError, AsyncOpenAI, DefaultAsyncHttpxClient

from ..domain import canonical, digest
from ..runtime_guard import require_pilot_runtime
from .boundary import ARGUMENTS
from .call_budget import BudgetDenied
from .campaign import dependencies, source_witness
from .comparison import ComparisonLimits
from .comparison_protocol import COUNT_ENDPOINTS, MODELS, RATES, count_request_body, request_body
from .comparison_wire import (
    MODEL_ENDPOINTS,
    ProviderWire,
    network_transport,
    quiet_wire,
    reject_ambient_headers,
    validate_key,
)
from .contracts import CatalogResult, PurchaseSummary, QuoteResult, ServiceSummary
from .mock_responses import _unique_fields
from .runner import disable_export

COUNT_PRICING = {
    "checked": "2026-11-10",
    "openai": "https://help.openai.com/en/articles/4936856-understanding-and-counting-tokens",
    "anthropic": "https://platform.claude.com/docs/en/build-with-claude/token-counting",
    "published_count_fee_nano_usd": 0,
    "billing_verified": False,
}
SCOPES = {
    "openai": ("openai-organization", "openai-project"),
    "anthropic": ("anthropic-organization-id", "anthropic-workspace-id"),
}
REMAINING = (
    "generation_not_authorized_by_preflight",
    "count_success_does_not_establish_generation_compatibility",
    "account_billing_credits_and_full_key_scope_unverified",
    "generation_standard_global_routing_not_observed",
    "conditional_context_backstop_not_vendor_billing_guarantee",
)
DIAGNOSTIC_STAGES = (
    "setup",
    "reservation",
    "dispatch_permission",
    "transport",
    "headers_persistence",
    "http",
    "body",
    "decode",
    "json",
    "reflection",
    "response",
    "model",
    "count",
    "scope",
    "observation_persistence",
    "response_close",
    "sdk_response",
    "client_close",
    "finalization",
    "completion_persistence",
    "unknown",
)
DIAGNOSTIC_REASONS = (
    "admission_rejected",
    "transport_failed",
    "http_status_rejected",
    "encoding_rejected",
    "response_limit",
    "body_read_failed",
    "decoding_failed",
    "json_invalid",
    "credential_reflection",
    "response_contract",
    "model_mismatch",
    "count_fields",
    "count_type",
    "count_bounds",
    "count_object",
    "scope_invalid",
    "scope_presence_changed",
    "scope_value_conflict",
    "persistence_failed",
    "close_failed",
    "cancelled",
    "timeout",
    "sdk_response_rejected",
    "unknown_exception",
    "incomplete_finalization",
)


class _Rejected(BudgetDenied):
    """Local fixed codes only; never construct from provider or exception text."""

    def __init__(self, stage, reason):
        if stage not in DIAGNOSTIC_STAGES or reason not in DIAGNOSTIC_REASONS:
            raise ValueError("preflight_diagnostic_code_rejected")
        self.stage, self.reason = stage, reason
        super().__init__(reason)


def probe_plan():
    initial = [
        {
            "kind": "user",
            "text": "Synthetic matched comparison protocol preflight; no action requested.",
        }
    ]
    history = list(initial)
    pending = PurchaseSummary(
        purchase_id="synthetic-preflight-purchase",
        payment_state="not_started",
        delivery_state="not_requested",
        amount_atomic=None,
        simulated_receipt_id=None,
        operator_reconciliation_required=False,
    )
    catalog = CatalogResult(
        services=[
            ServiceSummary(service_id="report-standard", quality="standard", amount_atomic=10000)
        ],
        recommended_service_id="report-standard",
        reason="eligible",
        available_atomic=10000,
        per_purchase_limit_atomic=10000,
        required_quality="standard",
    )
    quote = QuoteResult(
        quote_id="synthetic-preflight-quote", service_id="report-standard", amount_atomic=10000
    )
    purchased = PurchaseSummary(
        purchase_id=pending.purchase_id,
        payment_state="settled",
        delivery_state="delivered",
        amount_atomic=10000,
        simulated_receipt_id="sim-synthetic-preflight-receipt",
        operator_reconciliation_required=False,
    )
    for number, (name, arguments, result) in enumerate(
        (
            (
                "get_purchase_status",
                {},
                pending,
            ),
            ("list_services", {}, catalog),
            (
                "get_quote",
                {"service_id": "report-standard"},
                quote,
            ),
            (
                "purchase",
                {"quote_id": quote.quote_id},
                purchased,
            ),
        )
    ):
        call_id = f"synthetic-{number}"
        history.extend(
            [
                {
                    "kind": "tool_call",
                    "name": name,
                    "call_id": call_id,
                    "arguments": canonical(
                        ARGUMENTS[name].model_validate(arguments).model_dump(mode="json")
                    ),
                },
                {
                    "kind": "tool_result",
                    "call_id": call_id,
                    "content": canonical(result.model_dump(mode="json")),
                },
            ]
        )
    plan = [
        {
            "provider": p,
            "kind": "model",
            "method": "GET",
            "endpoint": MODEL_ENDPOINTS[p],
            "body": None,
            "generation": None,
        }
        for p in MODELS
    ]
    for transcript in (initial, history):
        for provider in MODELS:
            generation = request_body(provider, transcript, ComparisonLimits())
            plan.append(
                {
                    "provider": provider,
                    "kind": "count",
                    "method": "POST",
                    "endpoint": COUNT_ENDPOINTS[provider],
                    "body": count_request_body(provider, generation),
                    "generation": generation,
                }
            )
    return [{"slot": i, **step} for i, step in enumerate(plan)]


SCHEMA = """
CREATE TABLE policy(data TEXT NOT NULL);
CREATE TABLE state(singleton INTEGER PRIMARY KEY CHECK(singleton=1), status TEXT NOT NULL,
 owner TEXT, next_slot INTEGER NOT NULL, reason TEXT NOT NULL);
CREATE TABLE completion(singleton INTEGER PRIMARY KEY CHECK(singleton=1),
 confirmed INTEGER NOT NULL CHECK(confirmed IN (0,1)));
CREATE TRIGGER completion_no_delete BEFORE DELETE ON completion
 BEGIN SELECT RAISE(ABORT,'retain completion boundary'); END;
CREATE TRIGGER completion_final BEFORE UPDATE ON completion WHEN OLD.confirmed=1
 BEGIN SELECT RAISE(ABORT,'retain completion boundary'); END;
CREATE TABLE attempts(id TEXT PRIMARY KEY, slot INTEGER NOT NULL UNIQUE, provider TEXT NOT NULL,
 kind TEXT NOT NULL CHECK(kind IN ('model','count')), endpoint TEXT NOT NULL, method TEXT NOT NULL,
 payload_hash TEXT NOT NULL, payload_bytes INTEGER NOT NULL, generation_hash TEXT,
 status TEXT NOT NULL, http_status INTEGER, result TEXT, error_code TEXT);
CREATE TRIGGER policy_immutable_update BEFORE UPDATE ON policy
 BEGIN SELECT RAISE(ABORT,'immutable policy'); END;
CREATE TRIGGER policy_immutable_delete BEFORE DELETE ON policy
 BEGIN SELECT RAISE(ABORT,'retain policy'); END;
CREATE TRIGGER attempts_retain BEFORE DELETE ON attempts
 BEGIN SELECT RAISE(ABORT,'retain attempts'); END;
CREATE TRIGGER attempts_final BEFORE UPDATE ON attempts WHEN OLD.status='complete'
 BEGIN SELECT RAISE(ABORT,'retain result'); END;
CREATE TRIGGER state_retain BEFORE DELETE ON state BEGIN SELECT RAISE(ABORT,'retain owner'); END;
CREATE TRIGGER state_no_reset BEFORE UPDATE ON state WHEN OLD.status!='prepared' AND
 (NEW.owner IS NOT OLD.owner OR NEW.next_slot<OLD.next_slot OR NEW.status='prepared' OR
 (OLD.status IN ('finished','stopped') AND NEW.status!=OLD.status))
 BEGIN SELECT RAISE(ABORT,'retain preflight fence'); END;
"""
SCHEMA += (
    "CREATE TABLE diagnostic(singleton INTEGER PRIMARY KEY CHECK(singleton=1),"
    " stage TEXT NOT NULL CHECK(stage IN " + str(DIAGNOSTIC_STAGES) + "),"
    " reason TEXT NOT NULL CHECK(reason IN " + str(DIAGNOSTIC_REASONS) + "),"
    " slot INTEGER CHECK(slot BETWEEN 0 AND 5),"
    " attempt_status TEXT CHECK(attempt_status IN ('reserved','dispatch_authorized',"
    "'headers_received','complete','unknown')),"
    " http_status INTEGER CHECK(http_status BETWEEN 100 AND 599));"
    "CREATE TRIGGER diagnostic_no_update BEFORE UPDATE ON diagnostic"
    " BEGIN SELECT RAISE(ABORT,'retain first failure'); END;"
    "CREATE TRIGGER diagnostic_no_delete BEFORE DELETE ON diagnostic"
    " BEGIN SELECT RAISE(ABORT,'retain first failure'); END;"
)


class Preflight:
    def __init__(self, path):
        self.path, self._owner = Path(path).resolve(), None
        self._stage, self._first_failure, self._diagnostic_unavailable = "setup", None, False
        self._active_slot, self._observed_http_status = None, None
        with self.connect() as db:
            if db.execute("PRAGMA user_version").fetchone()[0] != 2:
                raise BudgetDenied("preflight_schema_rejected")
            self.policy = json.loads(db.execute("SELECT data FROM policy").fetchone()[0])
            if db.execute("SELECT count(*) FROM completion WHERE singleton=1").fetchone()[0] != 1:
                raise BudgetDenied("preflight_schema_rejected")
        if (
            canonical(self.policy["plan"]) != canonical(probe_plan())
            or self.policy["pricing"] != COUNT_PRICING
        ):
            raise BudgetDenied("preflight_policy_rejected")
        self.policy_hash = digest(self.policy)

    @contextmanager
    def connect(self, write=False):
        with closing(
            sqlite3.connect(
                self.path.as_uri() + ("?mode=rw" if write else "?mode=ro"), uri=True, timeout=0.1
            )
        ) as db:
            db.row_factory = sqlite3.Row
            db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield db
            if write:
                db.commit()

    def verify(self):
        if (
            digest(self.policy) != self.policy_hash
            or self.policy["root"] != str(self.path.parent)
            or self.policy["source"] != source_witness()
            or self.policy["runtime"] != require_pilot_runtime()
            or self.policy["dependencies"] != dependencies()
            or self.policy["rates"] != RATES
        ):
            raise BudgetDenied("preflight_source_binding")

    def activate(self, approved_policy_hash, reviewed_source_hash):
        self.verify()
        if approved_policy_hash != self.policy_hash or reviewed_source_hash != digest(
            self.policy["source"]
        ):
            raise BudgetDenied("preflight_review_binding")
        owner = uuid4().hex
        with self.connect(True) as db:
            if (
                db.execute(
                    "UPDATE state SET status='running',owner=?,reason='active' "
                    "WHERE status='prepared' AND owner IS NULL",
                    (owner,),
                ).rowcount
                != 1
            ):
                raise BudgetDenied("preflight_already_claimed")
        self._owner = owner

    def _owned(self, db):
        state = db.execute("SELECT * FROM state").fetchone()
        if self._owner is None or state["owner"] != self._owner or state["status"] != "running":
            raise BudgetDenied("preflight_not_owned")
        return state

    def reserve(self, provider, request):
        self.verify()
        body = request.content
        if len(body) > 32768:
            raise BudgetDenied("preflight_request_limit")
        with self.connect(True) as db:
            state = self._owned(db)
            self._active_slot = state["next_slot"]
            if state["next_slot"] >= len(self.policy["plan"]):
                raise BudgetDenied("preflight_limit")
            step = self.policy["plan"][state["next_slot"]]
            if provider != step["provider"] or (request.method, str(request.url)) != (
                step["method"],
                step["endpoint"],
            ):
                raise BudgetDenied("preflight_request_binding")
            if step["body"] is None:
                if body:
                    raise BudgetDenied("preflight_request_binding")
            else:
                try:
                    if canonical(json.loads(body, object_pairs_hook=_unique_fields)) != canonical(
                        step["body"]
                    ):
                        raise ValueError
                except (ValueError, TypeError):
                    raise BudgetDenied("preflight_request_binding") from None
            claim = uuid4().hex
            db.execute(
                "INSERT INTO attempts VALUES(?,?,?,?,?,?,?,?,?,'reserved',NULL,NULL,NULL)",
                (
                    claim,
                    step["slot"],
                    provider,
                    step["kind"],
                    step["endpoint"],
                    step["method"],
                    hashlib.sha256(body).hexdigest(),
                    len(body),
                    digest(step["generation"]) if step["generation"] else None,
                ),
            )
        return claim

    def permit(self, claim):
        with self.connect(True) as db:
            self._owned(db)
            if (
                db.execute(
                    "UPDATE attempts SET status='dispatch_authorized' "
                    "WHERE id=? AND status='reserved'",
                    (claim,),
                ).rowcount
                != 1
            ):
                raise BudgetDenied("preflight_duplicate_dispatch")

    def claim_wire(self, provider, request):
        self._stage = "reservation"
        try:
            claim = self.reserve(provider, request)
            self._stage = "dispatch_permission"
            self.permit(claim)
        except sqlite3.Error:
            self.fail(self._stage, "persistence_failed")
            raise
        except BudgetDenied:
            self.fail(self._stage, "admission_rejected")
            raise
        self._stage = "transport"
        return claim

    def wire_status(self, claim, status):
        self._stage = "headers_persistence"
        self._observed_http_status = (
            status if type(status) is int and 100 <= status <= 599 else None
        )
        with self.connect(True) as db:
            self._owned(db)
            if (
                db.execute(
                    "UPDATE attempts SET status='headers_received',http_status=? "
                    "WHERE id=? AND status='dispatch_authorized'",
                    (status, claim),
                ).rowcount
                != 1
            ):
                raise BudgetDenied("preflight_response_not_owned")

    def wire_failure(self, claim, code):
        reason = (
            "cancelled"
            if code == "cancelled"
            else "close_failed"
            if code == "close_failed"
            else "persistence_failed"
            if self._stage == "headers_persistence"
            else "transport_failed"
        )
        self.fail(self._stage, reason)

    def observe(self, claim, payload, headers):
        self._stage = "observation_persistence"
        with self.connect(True) as db:
            state = self._owned(db)
            row = db.execute(
                "SELECT * FROM attempts WHERE id=? AND slot=? AND status='headers_received'",
                (claim, state["next_slot"]),
            ).fetchone()
            if row is None or row["http_status"] != 200 or type(payload) is not dict:
                raise _Rejected("response", "response_contract")
            provider = row["provider"]
            if row["kind"] == "model":
                kind = "object" if provider == "openai" else "type"
                if payload.get("id") != MODELS[provider] or payload.get(kind) != "model":
                    raise _Rejected("model", "model_mismatch")
                result = {
                    "returned_model_id": payload["id"],
                    "generation_compatibility_verified": False,
                }
            else:
                fields = {"input_tokens", "object"} if provider == "openai" else {"input_tokens"}
                count = payload.get("input_tokens")
                if set(payload) != fields:
                    raise _Rejected("count", "count_fields")
                if type(count) is not int:
                    raise _Rejected("count", "count_type")
                if not 0 <= count <= (1050000 if provider == "openai" else 1000000):
                    raise _Rejected("count", "count_bounds")
                if provider == "openai" and payload.get("object") != "response.input_tokens":
                    raise _Rejected("count", "count_object")
                result = {
                    "input_tokens": count,
                    "count_semantics": "effective_input"
                    if provider == "openai"
                    else "estimate_without_cache_logic",
                }
            scope = {}
            for name in SCOPES[provider]:
                value = headers.get(name)
                if value is not None:
                    if not 1 <= len(value) <= 256 or not value.isascii():
                        raise _Rejected("scope", "scope_invalid")
                    scope[name] = hashlib.sha256(value.encode()).hexdigest()
            previous = [
                (r["endpoint"], json.loads(r["result"])["reported_scope_hashes"])
                for r in db.execute(
                    "SELECT endpoint,result FROM attempts WHERE provider=? AND status='complete'",
                    (provider,),
                )
            ]
            # Values must agree across the provider, even across endpoint families.
            # Missing headers are unknown: different endpoints need not expose the
            # same metadata. Repeated probes of one endpoint must remain stable.
            if any(
                any(old[k] != scope[k] for k in old.keys() & scope.keys()) for _, old in previous
            ):
                raise _Rejected("scope", "scope_value_conflict")
            if any(
                old.keys() != scope.keys()
                for endpoint, old in previous
                if endpoint == row["endpoint"]
            ):
                raise _Rejected("scope", "scope_presence_changed")
            result.update(
                reported_scope_hashes=scope,
                billing_identity_verified=False,
                full_key_scope_verified=False,
            )
            db.execute(
                "UPDATE attempts SET status='complete',result=? WHERE id=?",
                (canonical(result), claim),
            )
            next_slot = state["next_slot"] + 1
            db.execute("UPDATE state SET next_slot=?,reason='responses_observed'", (next_slot,))

    def finish(self):
        with self.connect(True) as db:
            state = self._owned(db)
            if (
                state["next_slot"] != 6
                or db.execute("SELECT count(*) FROM attempts WHERE status='complete'").fetchone()[0]
                != 6
            ):
                raise _Rejected("finalization", "incomplete_finalization")
            db.execute("UPDATE state SET status='finished',reason='bounded_probes_complete'")

    def stop(self, code="stopped"):
        if code not in ("stopped", "cancelled", "transport_failed", "close_failed"):
            code = "stopped"
        with self.connect(True) as db:
            db.execute(
                "UPDATE state SET status='stopped',reason=? WHERE status='running' AND owner=?",
                (code, self._owner),
            )

    def confirm_completion(self):
        """Last durable boundary: all responses/close operations and finish returned.

        It does not claim that later reporting or post-commit cleanup cannot fail.
        A finalization error before this call leaves completion unconfirmed forever.
        """
        with self.connect(True) as db:
            state = db.execute("SELECT * FROM state").fetchone()
            if (
                self._owner is None
                or state["owner"] != self._owner
                or state["status"] != "finished"
                or state["next_slot"] != 6
                or self._first_failure is not None
                or db.execute("SELECT count(*) FROM diagnostic").fetchone()[0]
                or db.execute("SELECT count(*) FROM attempts WHERE status='complete'").fetchone()[0]
                != 6
            ):
                raise BudgetDenied("preflight_completion_not_confirmable")
            if (
                db.execute(
                    "UPDATE completion SET confirmed=1 WHERE singleton=1 AND confirmed=0"
                ).rowcount
                != 1
            ):
                raise BudgetDenied("preflight_completion_already_confirmed")

    def record_failure(self, stage, reason):
        """At most one immutable first failure; no raw values or sensitive IDs."""
        if stage not in DIAGNOSTIC_STAGES or reason not in DIAGNOSTIC_REASONS:
            raise ValueError("preflight_diagnostic_code_rejected")
        with self.connect(True) as db:
            state = db.execute("SELECT * FROM state").fetchone()
            if self._owner is None or state["owner"] != self._owner:
                raise BudgetDenied("preflight_not_owned")
            row = db.execute(
                "SELECT slot,status,http_status FROM attempts WHERE slot=?", (self._active_slot,)
            ).fetchone()
            status = row["status"] if row else None
            if status is not None and status not in (
                "reserved",
                "dispatch_authorized",
                "headers_received",
                "complete",
            ):
                status = "unknown"
            http_status = self._observed_http_status
            if http_status is None and row:
                http_status = row["http_status"]
            if type(http_status) is not int or not 100 <= http_status <= 599:
                http_status = None
            slot = self._active_slot
            if type(slot) is not int or not 0 <= slot < 6:
                slot = None
            db.execute(
                "INSERT INTO diagnostic VALUES(1,?,?,?,?,?) ON CONFLICT(singleton) DO NOTHING",
                (stage, reason, slot, status, http_status),
            )

    def fail(self, stage, reason):
        # Remember locally BEFORE best-effort persistence; secondary cleanup never
        # overwrites the first cause, observations or pending-attempt uncertainty.
        if stage not in DIAGNOSTIC_STAGES or reason not in DIAGNOSTIC_REASONS:
            stage, reason = "unknown", "unknown_exception"
        if self._first_failure is None:
            self._first_failure = (stage, reason)
        try:
            self.record_failure(*self._first_failure)
        except Exception:
            self._diagnostic_unavailable = True
        try:
            self.stop(
                "cancelled"
                if reason == "cancelled"
                else "close_failed"
                if reason == "close_failed"
                else "stopped"
            )
        except Exception:
            pass  # Owner and pending records still fence restart; no cleanup guarantee.

    def report(self):
        with self.connect() as db:
            state = dict(db.execute("SELECT * FROM state").fetchone())
            attempts = [dict(row) for row in db.execute("SELECT * FROM attempts ORDER BY slot")]
            failure = db.execute(
                "SELECT stage,reason,slot,attempt_status,http_status FROM diagnostic"
            ).fetchone()
            diagnostic = dict(failure) if failure else None
            confirmed = (
                db.execute("SELECT confirmed FROM completion WHERE singleton=1").fetchone()[0] == 1
            )
        state.pop("owner")
        return {
            "scope": "non_generating_preflight",
            "mode": self.policy["mode"],
            "policy_hash": self.policy_hash,
            "source_hash": digest(self.policy["source"]),
            "state": state,
            "completion_confirmed": confirmed,
            "completion_boundary": (
                "validated_responses_and_client_closes_before_finalization_returned"
            ),
            "post_completion_reporting_verified": False,
            "run_complete": confirmed
            and state["status"] == "finished"
            and diagnostic is None
            and self._first_failure is None,
            "diagnostic": diagnostic,
            "diagnostic_status": "recorded"
            if diagnostic
            else "unavailable_or_not_recorded"
            if self._first_failure is not None or state["status"] in ("running", "stopped")
            else "no_failure_recorded"
            if confirmed
            else "unavailable_or_not_recorded"
            if state["status"] != "prepared"
            else "not_attempted",
            "diagnostic_write_failure_observed_in_this_process": self._diagnostic_unavailable,
            "intended_maximum": {
                "model_reads_per_provider": 1,
                "counts_per_provider": 2,
                "total": 6,
            },
            "attempts": [
                {**r, "result": json.loads(r["result"]) if r["result"] else None} for r in attempts
            ],
            "reserved_attempts": len(attempts),
            "observed_http_responses": sum(r["http_status"] is not None for r in attempts),
            "verified_responses": sum(r["status"] == "complete" for r in attempts),
            "unknown_outcomes": sum(r["status"] != "complete" for r in attempts),
            "generation_attempts": sum(r["kind"] == "generation" for r in attempts),
            "generation_ready": False,
            "remaining_assumptions": list(REMAINING),
            "count_pricing": COUNT_PRICING,
            "observed_billing_cost": None,
            "financial_databases_created": False,
        }


def initialize_preflight(directory, *, mode="http_mock_only"):
    if mode not in ("http_mock_only", "authenticated_preflight"):
        raise BudgetDenied("preflight_mode_rejected")
    runtime = require_pilot_runtime()
    root = Path(directory).resolve()
    root.mkdir(mode=0o700)
    policy = {
        "root": str(root),
        "mode": mode,
        "source": source_witness(),
        "runtime": runtime,
        "dependencies": dependencies(),
        "plan": probe_plan(),
        "rates": RATES,
        "pricing": COUNT_PRICING,
    }
    path = root / "preflight.sqlite3"
    os.close(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))
    with closing(sqlite3.connect(path)) as db, db:
        db.executescript("BEGIN IMMEDIATE;\n" + SCHEMA)
        db.execute("INSERT INTO policy VALUES(?)", (canonical(policy),))
        db.execute("INSERT INTO state VALUES(1,'prepared',NULL,0,'not_activated')")
        db.execute("INSERT INTO completion VALUES(1,0)")
        db.execute("PRAGMA user_version=2")
    return Preflight(path)


class PreflightExchange(httpx2.AsyncBaseTransport):
    def __init__(self, preflight, provider, key, wire):
        if preflight.policy["mode"] == "http_mock_only" and type(wire) is not httpx2.MockTransport:
            raise BudgetDenied("preflight_mock_transport_required")
        self.preflight = preflight
        self.wire = ProviderWire(provider, preflight, key, wire)

    @quiet_wire
    async def handle_async_request(self, request):
        response = None
        try:
            self.preflight._stage = "transport"
            async with asyncio.timeout(30):
                response = await self.wire.handle_async_request(request)
                self.preflight._stage = "http"
                if response.status_code != 200:
                    raise _Rejected("http", "http_status_rejected")
                if response.headers.get("content-encoding", "identity") != "identity":
                    raise _Rejected("http", "encoding_rejected")
                raw = bytearray()
                limit = 16384 if request.method == "GET" else 4096
                self.preflight._stage = "body"
                async for chunk in response.stream:
                    if asyncio.current_task().cancelling():
                        raise asyncio.CancelledError
                    if len(raw) + len(chunk) > limit:
                        raise _Rejected("body", "response_limit")
                    raw.extend(chunk)
                self.preflight._stage = "json"
                try:
                    payload = json.loads(raw, object_pairs_hook=_unique_fields)
                except UnicodeDecodeError:
                    raise _Rejected("decode", "decoding_failed") from None
                except ValueError:
                    raise _Rejected("json", "json_invalid") from None
                self.preflight._stage = "reflection"
                try:
                    self.wire.check_payload(raw, payload)
                    self.wire.check_payload(
                        b"", {k: response.headers.get(k) for k in SCOPES[self.wire.provider]}
                    )
                except BudgetDenied:
                    raise _Rejected("reflection", "credential_reflection") from None
                self.preflight._stage = "observation_persistence"
                self.preflight.observe(self.wire.claim, payload, response.headers)
                return httpx2.Response(
                    200, content=bytes(raw), headers={"Content-Type": "application/json"}
                )
        except asyncio.CancelledError:
            self.preflight.fail(self.preflight._stage, "cancelled")
            raise asyncio.CancelledError("preflight_cancelled") from None
        except _Rejected as error:
            self.preflight.fail(error.stage, error.reason)
            raise BudgetDenied("preflight_stopped") from None
        except sqlite3.Error:
            self.preflight.fail(self.preflight._stage, "persistence_failed")
            raise BudgetDenied("preflight_stopped") from None
        except httpx2.ReadError:
            self.preflight.fail(self.preflight._stage, "body_read_failed")
            raise BudgetDenied("preflight_stopped") from None
        except TimeoutError:
            self.preflight.fail(self.preflight._stage, "timeout")
            raise BudgetDenied("preflight_stopped") from None
        except Exception:
            self.preflight.fail(self.preflight._stage, "unknown_exception")
            raise BudgetDenied("preflight_stopped") from None
        finally:
            if response is not None:
                self.preflight._stage = "response_close"
                try:
                    async with asyncio.timeout(0.5):
                        await response.aclose()
                except asyncio.CancelledError:
                    self.preflight.fail("response_close", "cancelled")
                    raise asyncio.CancelledError("preflight_close_cancelled") from None
                except Exception:
                    self.preflight.fail("response_close", "close_failed")
                    raise BudgetDenied("preflight_close_failed") from None
                self.preflight._stage = "sdk_response"

    async def aclose(self):
        self.preflight._stage = "client_close"
        await self.wire.aclose()


async def run_mock_preflight(
    preflight, *, api_keys, approved_policy_hash, reviewed_source_hash, transports
):
    """Exact in-memory transports only, checked BEFORE ownership or authentication.

    Responders are trusted host test code; arbitrary Python callbacks are not a
    sandbox. This does not accept factories, transport subclasses or real wires.
    """
    if (
        preflight.policy["mode"] != "http_mock_only"
        or type(transports) is not dict
        or set(transports) != set(MODELS)
        or any(type(wire) is not httpx2.MockTransport for wire in transports.values())
    ):
        raise BudgetDenied("preflight_mock_transport_required")
    return await _run_preflight(
        preflight,
        api_keys=api_keys,
        approved_policy_hash=approved_policy_hash,
        reviewed_source_hash=reviewed_source_hash,
        transports=transports,
    )


async def run_authenticated_preflight(
    preflight, *, api_keys, approved_policy_hash, reviewed_source_hash
):
    """Explicit host entrypoint. No injectable factory or automatic activation."""
    if preflight.policy["mode"] != "authenticated_preflight":
        raise BudgetDenied("preflight_transport_mode_binding")
    return await _run_preflight(
        preflight,
        api_keys=api_keys,
        approved_policy_hash=approved_policy_hash,
        reviewed_source_hash=reviewed_source_hash,
        transports=None,
    )


async def _run_preflight(
    preflight, *, api_keys, approved_policy_hash, reviewed_source_hash, transports
):
    api_keys = dict(api_keys)
    if set(api_keys) != set(MODELS):
        raise BudgetDenied("preflight_provider_keys_required")
    for key in api_keys.values():
        validate_key(key)
    reject_ambient_headers()
    disable_export()
    preflight.activate(approved_policy_hash, reviewed_source_hash)
    http = None
    try:
        for step in preflight.policy["plan"]:
            preflight._stage = "setup"
            preflight._active_slot, preflight._observed_http_status = step["slot"], None
            provider = step["provider"]
            wire = network_transport() if transports is None else transports[provider]
            exchange = PreflightExchange(preflight, provider, api_keys[provider], wire)
            http = DefaultAsyncHttpxClient(
                transport=exchange, trust_env=False, follow_redirects=False, timeout=30
            )
            async with asyncio.timeout(30):
                preflight._stage = "sdk_response"
                if provider == "openai":
                    client = AsyncOpenAI(
                        api_key="preflight-wire-auth-is-transient",
                        admin_api_key="",
                        webhook_secret="",
                        organization="",
                        project="",
                        base_url="https://api.openai.com/v1",
                        max_retries=0,
                        timeout=30,
                        http_client=http,
                        _strict_response_validation=True,
                    )
                    if step["kind"] == "model":
                        await client.models.retrieve(MODELS[provider])
                    else:
                        await client.responses.input_tokens.count(**step["body"])
                else:
                    await http.request(
                        step["method"],
                        step["endpoint"],
                        content=canonical(step["body"]) if step["body"] else b"",
                    )
            preflight._stage = "client_close"
            await http.aclose()
            http = None
        preflight._stage = "finalization"
        preflight.finish()
        preflight._stage = "completion_persistence"
        preflight.confirm_completion()
    except asyncio.CancelledError:
        preflight.fail(preflight._stage, "cancelled")
        raise asyncio.CancelledError("preflight_cancelled") from None
    except _Rejected as error:
        preflight.fail(error.stage, error.reason)
    except sqlite3.Error:
        preflight.fail(preflight._stage, "persistence_failed")
    except TimeoutError:
        preflight.fail(preflight._stage, "timeout")
    except APIResponseValidationError:
        preflight.fail("sdk_response", "sdk_response_rejected")
    except Exception:
        preflight.fail(preflight._stage, "unknown_exception")
    finally:
        if http is not None:
            try:
                async with asyncio.timeout(0.5):
                    await http.aclose()
            except asyncio.CancelledError:
                preflight.fail("client_close", "cancelled")
                raise asyncio.CancelledError("preflight_cleanup_cancelled") from None
            except Exception:
                preflight.fail("client_close", "close_failed")
    return preflight.report()
