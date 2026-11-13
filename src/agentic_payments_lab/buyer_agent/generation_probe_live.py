"""Prepared one-shot live adapter. Import/preparation never discovers keys or sends HTTP.

Execution requires separately approved frozen review, transient explicit keys and
an exclusive shared authority. All development/testing uses synthetic wire doubles.
"""

import asyncio
import hashlib
import json
import re
import sqlite3
from pathlib import Path
from uuid import uuid4

from ..domain import canonical, digest
from ..runtime_guard import require_pilot_runtime
from .campaign import dependencies, source_witness
from .comparison_protocol import COUNT_ENDPOINTS, ENDPOINTS, PROVIDERS, RATES, request_body
from .comparison_runner import MockExchange, NativeMockModel
from .comparison_wire import ProviderWire, network_transport, quiet_wire, validate_key
from .generation_probe import LIMITS, SCHEMA, TRANSCRIPT, Probe, _inventory, _reject

REPORTED_POLICY = {
    "price_status": "user_reported_not_independently_account_verified",
    "input_nano": {"openai": 100, "anthropic": 100},
    "output_nano": {"openai": 500, "anthropic": 500},
    "overhead_margin_tokens": 1024,
    "margin_is_guaranteed_bound": False,
    "max_output_tokens_including_reasoning": 512,
    "short_input_threshold": {"openai": 272_000, "anthropic": 100_000},
    "cache_prices": None,
    "cache_policy": "no_cache_configuration_require_zero_observed_cache_categories",
    "cache_nonzero_exposure": "unknown_price_stop_no_settlement",
    "mode": "standard_uncached_text_only_no_reasoning_or_thinking",
}
ACCOUNT_REPORTS = {
    "provenance": "user_reported_not_independently_verified",
    "openai": {"project_limit_usd": "10", "credits_usd": "119.99", "auto_reload": False},
    "anthropic": {"workspace_limit_usd": "10", "promo_credits_usd": "99.99", "auto_reload": False},
    "provider_enforcement_or_hard_cap_verified": False,
}
WIRE_SCHEMA = """
CREATE TABLE probe_wire(id TEXT PRIMARY KEY,record_id TEXT UNIQUE NOT NULL,
 slot INTEGER NOT NULL,provider TEXT NOT NULL,kind TEXT NOT NULL,
 endpoint TEXT NOT NULL,body_hash TEXT NOT NULL,status INTEGER,failure TEXT,
 UNIQUE(slot,kind));
CREATE TRIGGER probe_wire_no_delete BEFORE DELETE ON probe_wire
 BEGIN SELECT RAISE(ABORT,'retain wire'); END;
CREATE TRIGGER probe_wire_no_rewrite BEFORE UPDATE ON probe_wire WHEN
 NEW.id!=OLD.id OR NEW.record_id!=OLD.record_id OR NEW.slot!=OLD.slot OR
 NEW.provider!=OLD.provider OR NEW.kind!=OLD.kind OR NEW.endpoint!=OLD.endpoint OR
 NEW.body_hash!=OLD.body_hash OR
 (OLD.status IS NOT NULL AND NEW.status IS NOT OLD.status) OR
 (OLD.failure IS NOT NULL AND NEW.failure IS NOT OLD.failure)
 BEGIN SELECT RAISE(ABORT,'retain wire'); END;
"""
REVIEW_FIELDS = {
    "generation_authorization",
    "reviewed_source_hash",
    "reviewed_generation_hashes",
    "shared_authorization_id",
    "prior_inventory_hash",
    "prior_inventory_complete_attested",
    "reported_rates_accepted",
    "margin_assumption_accepted",
    "uncached_mode_risk_accepted",
    "preflight_lineage_reviewed",
    "account_reports",
    "strict_quality_gate_accepted",
}


def payload_hashes():
    return {p: digest(request_body(p, TRANSCRIPT, LIMITS)) for p in PROVIDERS}


def validate_review(review, authorization_id, inventory):
    """Operator attestations, not authentication/billing facts or permission by code."""
    if type(review) is not dict or set(review) != REVIEW_FIELDS:
        _reject()
    if (
        review["generation_authorization"] != "approve_one_count_and_one_generation_per_provider"
        or review["shared_authorization_id"] != authorization_id
        or review["prior_inventory_hash"] != digest(inventory)
        or review["reviewed_source_hash"] != digest(source_witness())
        or review["reviewed_generation_hashes"] != payload_hashes()
        or review["account_reports"] != ACCOUNT_REPORTS
        or any(
            review[k] is not True
            for k in (
                "prior_inventory_complete_attested",
                "reported_rates_accepted",
                "margin_assumption_accepted",
                "uncached_mode_risk_accepted",
                "preflight_lineage_reviewed",
                "strict_quality_gate_accepted",
            )
        )
    ):
        _reject()


def create_live_authority(path, *, authorization_id, prior_inventory, execution_review):
    """Fresh-only; must use the single agreed authority path, never reset/clone it.

    This function cannot verify a human approval; callers must obtain it separately.
    No actual authority is created during offline development, except test fixtures.
    """
    require_pilot_runtime()
    if (
        type(authorization_id) is not str
        or re.fullmatch(r"[A-Za-z0-9_-]{1,80}", authorization_id) is None
    ):
        _reject()
    validate_review(execution_review, authorization_id, prior_inventory)
    prior = _inventory(prior_inventory)
    unresolved = sum(r["unresolved_nano"] for r in prior_inventory if r["provider"] == "openai")
    # The minimum includes the unknown hold, not an additional charge on top of it.
    if (
        prior["openai"] < 9_858_300
        or unresolved < 1_239_000
        or any(v > 1_500_000_000 for v in prior.values())
        or sum(prior.values()) > 3_000_000_000
    ):
        _reject()
    path = Path(path).resolve()
    policy = {
        "kind": LiveProbe.KIND,
        "mode": "bounded_generation_live",
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
        "reservation_policy": REPORTED_POLICY,
        "execution_review": execution_review,
    }
    with path.open("xb"):
        pass
    with sqlite3.connect(path) as db:
        db.executescript(SCHEMA.replace("user_version=2", "user_version=3") + WIRE_SCHEMA)
        db.execute("INSERT INTO policy VALUES(1,?)", (canonical(policy),))
    return path


def prepare_live_probe(path, *, authorization_id, expected_authority_hash):
    probe = LiveProbe(path)
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
        "reservation_policy": REPORTED_POLICY,
        "execution_mode": "bounded_generation_live",
        "tool_execution": False,
        "strict_quality_gate": True,
    }
    with probe.connect(write=True) as db:
        if db.execute("SELECT 1 FROM allocation").fetchone():
            _reject()
        db.execute("INSERT INTO allocation VALUES(1,?,?)", (uuid4().hex, canonical(allocation)))
        db.execute("INSERT INTO state VALUES(1,'prepared',NULL,0,0)")
    return LiveProbe(path)


class LiveProbe(Probe):
    VERSION = 3
    KIND = "bounded_generation_probe_live_v3"
    RESERVATION_POLICY = REPORTED_POLICY

    def verify_source(self):
        super().verify_source()
        validate_review(
            self.policy["execution_review"],
            self.policy["authorization_id"],
            self.policy["prior_inventory"],
        )

    @staticmethod
    def estimate(provider, count):
        if (
            provider not in PROVIDERS
            or type(count) is not int
            or count < 0
            or count + 1024 > REPORTED_POLICY["short_input_threshold"][provider]
        ):
            _reject()
        return (count + 1024) * 100 + 512 * 500

    @staticmethod
    def observed(provider, usage):
        """Known ordinary-input/output subtotal; unknown cache prices are NOT zero."""
        native = usage.get("native", {})
        output = usage.get("output_tokens")
        subtotal = output * 500 if type(output) is int and output >= 0 else None
        ordinary = native.get("input_tokens")
        if provider == "openai":
            cache = [native.get(k) for k in ("cached_tokens", "cache_write_tokens")]
            ordinary = (
                ordinary - sum(cache)
                if type(ordinary) is int and all(type(v) is int and v >= 0 for v in cache)
                else None
            )
        if type(ordinary) is int and ordinary >= 0:
            subtotal = (subtotal or 0) + ordinary * 100
        return subtotal

    def observe(self, entry, request_id, evidence):
        evidence = json.loads(canonical(evidence))
        usage = evidence["usage"]
        native = usage.get("native", {})
        provider = entry["provider"]
        cache_fields = (
            ("cached_tokens", "cache_write_tokens")
            if provider == "openai"
            else ("cache_read_input_tokens", "cache_creation_input_tokens")
        )
        prompt, output = usage.get("prompt_tokens"), usage.get("output_tokens")
        complete = (
            usage.get("complete")
            and usage.get("regime_ok")
            and all(type(native.get(k)) is int and native[k] == 0 for k in cache_fields)
            and type(prompt) is int
            and 0 <= prompt <= REPORTED_POLICY["short_input_threshold"][provider]
            and type(output) is int
            and 0 <= output <= 512
        )
        evidence["reported_rate_cost_complete"] = bool(complete)
        evidence["reported_rate_cost_basis"] = "user_reported_ordinary_input_output_only"
        if not complete:
            evidence["control_error"] = "probe_usage_or_price_categories_unverified"
        return super().observe(entry, request_id, evidence)

    def advance(self, entry):
        with self.connect(write=True) as db:
            self._owned(db, entry)
            row = db.execute(
                "SELECT * FROM requests WHERE slot=? AND status='complete'", (entry["slot"],)
            ).fetchone()
            if row is None:
                _reject()
            evidence = json.loads(row["evidence"])
            quality = (
                evidence["schema_outcome"] == "valid_final"
                and evidence["termination"] == "completed"
            ) or (
                evidence["schema_outcome"] == "valid_tool_arguments"
                and evidence["call"] is not None
                and evidence["termination"] is None
            )
            if not quality:
                db.execute("UPDATE state SET status='stopped'")
            else:
                db.execute("UPDATE state SET next_slot=next_slot+1")
        if not quality:
            _reject()  # Accounting stays complete; quality failure forbids provider2.

    def claim_wire(self, provider, request):
        self.verify_source()
        endpoint = str(request.url)
        if request.method != "POST" or provider not in PROVIDERS:
            _reject()
        if endpoint == COUNT_ENDPOINTS[provider]:
            kind, table, field = "count", "counts", "payload_hash"
        elif endpoint == ENDPOINTS[provider]:
            kind, table, field = "generation", "requests", "body_hash"
        else:
            _reject()
        entry = {"slot": PROVIDERS.index(provider), "provider": provider}
        with self.connect(write=True) as db:
            self._owned(db, entry)
            row = db.execute(
                f"SELECT * FROM {table} WHERE slot=? AND provider=? AND status='pending'",
                (entry["slot"], provider),
            ).fetchone()
            if (
                row is None
                or hashlib.sha256(request.content).hexdigest() != row[field]
                or db.execute("SELECT count(*) FROM probe_wire").fetchone()[0] >= 4
            ):
                _reject()
            claim = uuid4().hex
            db.execute(
                "INSERT INTO probe_wire VALUES(?,?,?,?,?,?,?,NULL,NULL)",
                (claim, row["id"], entry["slot"], provider, kind, endpoint, row[field]),
            )
        return claim

    def wire_status(self, claim, status):
        with self.connect(write=True) as db:
            if (
                type(status) is not int
                or db.execute(
                    "UPDATE probe_wire SET status=? WHERE id=? AND status IS NULL", (status, claim)
                ).rowcount
                != 1
            ):
                _reject()

    def wire_failure(self, claim, code):
        if code not in {"cancelled", "transport_failed", "close_failed"}:
            _reject()
        with self.connect(write=True) as db:
            db.execute(
                "UPDATE probe_wire SET failure=? WHERE id=? AND failure IS NULL", (code, claim)
            )
            db.execute("UPDATE state SET status='stopped' WHERE status='running'")

    def report(self):
        report = super().report()
        with self.connect() as db:
            claims = [
                dict(r)
                for r in db.execute(
                    "SELECT slot,provider,kind,status,failure FROM probe_wire ORDER BY rowid"
                )
            ]
        report.update(
            scope="bounded_generation_probe",
            wire_attempts=claims,
            account_reports=ACCOUNT_REPORTS,
            account_reports_independently_verified=False,
            live_generation_ready=False,  # Reporting never authorizes another invocation.
            strict_quality_gate=True,
            missing_live_attestations=[],
            unknown_cost_categories=any(r["status"] != "complete" for r in report["generations"]),
        )
        return report


class LiveProbeExchange(MockExchange):
    def __init__(self, probe, entry, key):
        if type(probe) is not LiveProbe:
            _reject()
        validate_key(key)
        probe.verify_source()
        with probe.connect() as db:
            probe._owned(db, entry)
        super().__init__(probe, entry, None)
        self._wire = ProviderWire(entry["provider"], probe, key, network_transport())

    def check_response(self, raw, payload):
        self._wire.check_payload(raw, payload)

    def check_decoded(self, payload, evidence):
        self._wire.check_decoded(payload, evidence)

    @quiet_wire
    async def handle_async_request(self, request):
        return await super().handle_async_request(request)


class LiveProbeModel(NativeMockModel):
    exchange_type = LiveProbeExchange


async def run_live_probe(probe, *, api_keys, approved_policy_hash):
    """Explicit invocation only, no CLI/ambient key loading or automatic call sites."""
    if (
        type(probe) is not LiveProbe
        or type(api_keys) is not dict
        or set(api_keys) != set(PROVIDERS)
    ):
        _reject()
    for key in api_keys.values():
        validate_key(key)
    probe.verify_source()
    probe.activate(approved_policy_hash)
    caller = {"outcome": "incomplete", "reason": "not_finished", "report": None}
    try:
        for slot, provider in enumerate(PROVIDERS):
            entry = {"slot": slot, "provider": provider}
            model = LiveProbeModel(probe, entry, api_keys[provider])
            try:
                async with asyncio.timeout(probe.limits.timeout_seconds):
                    await model.respond(TRANSCRIPT)
            finally:
                await model.aclose()
            probe.advance(entry)  # Durable accounting first; strict gate before next provider.
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
