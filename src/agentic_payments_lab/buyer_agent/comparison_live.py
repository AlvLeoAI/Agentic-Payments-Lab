"""Separate explicit simulator-only activation. Preflight never calls this module's runner."""

import asyncio
import re

from ..domain import digest
from .call_budget import BudgetDenied
from .campaign import source_witness
from .comparison import initialize_comparison
from .comparison_preflight import Preflight
from .comparison_runner import MockExchange, NativeMockModel, _episode
from .comparison_wire import (
    ProviderWire,
    network_transport,
    quiet_wire,
    reject_ambient_headers,
    validate_key,
)
from .runner import disable_export

REVIEW_FIELDS = {
    "preflight_path",
    "preflight_policy_hash",
    "preflight_result_hash",
    "reviewed_source_hash",
    "generation_authorization",
    "conditional_backstop_accepted",
    "account_pricing_scope_reviewed",
}


def validate_generation_review(review):
    """Trusted operator attestations, NOT proof of permission or a billing guarantee.

    A count response cannot produce these attestations. This function does not
    authorize a generation itself; it verifies a separately approved fresh policy.
    """
    try:
        if type(review) is not dict or set(review) != REVIEW_FIELDS:
            raise ValueError
        if (
            review["generation_authorization"] != "approve_fresh_matched_simulator_generation"
            or review["conditional_backstop_accepted"] is not True
            or review["account_pricing_scope_reviewed"] is not True
        ):
            raise ValueError
        for name in ("preflight_policy_hash", "preflight_result_hash", "reviewed_source_hash"):
            if type(review[name]) is not str or re.fullmatch("[a-f0-9]{64}", review[name]) is None:
                raise ValueError
        if review["reviewed_source_hash"] != digest(source_witness()):
            raise ValueError
        preflight = Preflight(review["preflight_path"])
        preflight.verify()
        report = preflight.report()
        if (
            preflight.policy["mode"] != "authenticated_preflight"
            or report["state"]["status"] != "finished"
            or report["run_complete"] is not True
            or report["completion_confirmed"] is not True
            or report["diagnostic_status"] != "no_failure_recorded"
            or report["unknown_outcomes"] != 0
            or report["verified_responses"] != 6
            or report["generation_attempts"] != 0
            or preflight.policy_hash != review["preflight_policy_hash"]
            or digest(report) != review["preflight_result_hash"]
        ):
            raise ValueError
    except Exception:
        raise BudgetDenied("comparison_generation_review_required") from None
    return dict(review)


async def initialize_live_comparison(directory, *, generation_review, **lower_limits):
    return await initialize_comparison(
        directory,
        live_review=validate_generation_review(generation_review),
        kind="paired_simulator_live_v1",
        execution_mode="live_simulator",
        **lower_limits,
    )


class LiveExchange(MockExchange):
    def __init__(self, comparison, entry, key):
        validate_key(key)
        reject_ambient_headers()
        if comparison.limits.execution_mode != "live_simulator":
            raise BudgetDenied("comparison_live_admission_blocked")
        comparison.verify_source()
        with comparison.connect() as db:
            comparison._owned(db, entry["slot"])
        super().__init__(comparison, entry, None)
        self._wire = ProviderWire(entry["provider"], comparison, key, network_transport())

    def check_response(self, raw, payload):
        self._wire.check_payload(raw, payload)

    def check_decoded(self, payload, evidence):
        self._wire.check_decoded(payload, evidence)

    @quiet_wire
    async def handle_async_request(self, request):
        try:
            async with asyncio.timeout(self.comparison.limits.timeout_seconds):
                return await super().handle_async_request(request)
        except TimeoutError:
            self._stop()
            raise TimeoutError("comparison_request_timeout") from None


class NativeLiveModel(NativeMockModel):
    exchange_type = LiveExchange


async def run_live_comparison(comparison, *, api_keys, approved_enrollment_hash):
    """Never called by preflight. Separate reviewed enrollment and operator action required."""
    api_keys = dict(api_keys)
    if set(api_keys) != {"openai", "anthropic"}:
        raise BudgetDenied("comparison_provider_keys_required")
    for key in api_keys.values():
        validate_key(key)
    reject_ambient_headers()
    disable_export()
    if comparison.limits.execution_mode != "live_simulator":
        raise BudgetDenied("comparison_live_admission_blocked")
    comparison.activate(policy_hash=approved_enrollment_hash, mode="live_simulator")
    try:
        for entry in comparison.policy["entries"]:
            if not await _episode(
                comparison, entry, api_keys[entry["provider"]], model_factory=NativeLiveModel
            ):
                break
    except asyncio.CancelledError:
        try:
            comparison.stop()
        except Exception:
            pass
        raise asyncio.CancelledError("comparison_cancelled") from None
    except Exception:
        try:
            comparison.stop()
        except Exception:
            pass
    return comparison.report()
