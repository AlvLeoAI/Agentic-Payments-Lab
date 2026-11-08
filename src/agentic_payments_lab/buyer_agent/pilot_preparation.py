"""Guarded simulator preparation only. No live transport, activation or key loading."""

from dataclasses import dataclass
from pathlib import Path

from pydantic import TypeAdapter

from ..clock import FixedClock
from ..coordinator import BuyerSession
from ..domain import Grant, Identifier, digest
from ..runtime import Runtime
from ..runtime_guard import require_pilot_runtime
from .call_budget import BudgetDenied, CallBudget, CallLimits
from .contracts import Goal
from .mock_responses import run_mock_buyer
from .pilot_pricing import LunaRates
from .store import AgentStore


@dataclass(frozen=True)
class PreparedPilot:
    runtime: Runtime
    session: BuyerSession
    metadata: AgentStore
    goal: Goal
    budget: CallBudget


def initialize_pilot(
    directory: Path,
    *,
    goal: Goal,
    grant: Grant,
    task_id: str,
    estimate_ceiling_nano_usd: int,
    **lower_limits,
) -> PreparedPilot:
    """Explicit fresh fixtures only. Guard before ANY pilot state creation/open.

    Existing/partially initialized paths reject; no automatic repair or replacement.
    Objects supplied to the lower-level runner are trusted host capabilities.
    """
    require_pilot_runtime()
    goal = Goal.model_validate(goal.model_dump())
    grant = Grant.model_validate(grant.model_dump())
    task_id = TypeAdapter(Identifier).validate_python(task_id)
    clock = FixedClock()
    if not grant.issued_at <= clock.now() < grant.expires_at or goal.purpose != grant.purpose:
        raise BudgetDenied("pilot_grant_mismatch")
    CallLimits(
        batch_id="preflight",
        model="gpt-6-luna",
        rates=LunaRates(),
        estimate_ceiling_nano_usd=estimate_ceiling_nano_usd,
        **lower_limits,
    )
    directory = Path(directory)
    directory.mkdir(mode=0o700)  # No exist_ok: do not open/migrate retained state.
    runtime = Runtime(directory, clock=clock)
    runtime.ledger.add_grant(grant, clock.now())
    runtime.ledger.create_task(task_id, grant.grant_id, clock.now())
    metadata = AgentStore(directory / "agent.sqlite3")
    metadata.initialize(runtime.ledger, task_id, goal)
    session = runtime.buyer(task_id)
    limits = pilot_limits(
        session, goal, estimate_ceiling_nano_usd=estimate_ceiling_nano_usd, **lower_limits
    )
    budget = CallBudget.initialize(directory / "pilot-budget.sqlite3", limits)
    return PreparedPilot(runtime, session, metadata, goal, budget)


def pilot_batch_id(session, goal) -> str:
    if type(session) is not BuyerSession:
        raise BudgetDenied("pilot_simulator_required")
    return "luna-" + digest(
        {"backend": "simulator", "task": session.context.model_dump(), "goal": goal.model_dump()}
    )


def pilot_limits(session, goal, *, estimate_ceiling_nano_usd: int, **lower_limits) -> CallLimits:
    """Explicit offline planning ceiling; constructing it grants no API permission."""
    require_pilot_runtime()
    return CallLimits(
        batch_id=pilot_batch_id(session, goal),
        model="gpt-6-luna",
        rates=LunaRates(),
        estimate_ceiling_nano_usd=estimate_ceiling_nano_usd,
        **lower_limits,
    )


def pilot_preflight(session, goal, budget) -> dict:
    runtime = require_pilot_runtime()
    limits = CallLimits.model_validate(budget.limits.model_dump())
    if not isinstance(limits.rates, LunaRates):
        raise BudgetDenied("sourced_luna_rates_required")
    if limits.batch_id != pilot_batch_id(session, goal):
        raise BudgetDenied("pilot_task_binding_mismatch")
    tokens = limits.max_request_bytes + limits.framing_tokens
    output = limits.max_output_tokens * limits.rates.output_nano_usd
    requests = min(limits.max_requests, limits.max_invocations * limits.requests_per_invocation)
    return {
        "mode": "offline_pilot_preparation",
        "live_authorized": False,
        "credentials_loaded": False,
        "model_access_verified": False,
        "credit_balance_verified": False,
        "billing_cap_guaranteed": False,
        "runtime": runtime,
        "policy": limits.model_dump(mode="json"),
        "maximum_planned_requests": requests,
        "estimated_input_tokens_per_request": tokens,
        "ordinary_input_plan_nano_usd": requests * (tokens * limits.rates.input_nano_usd + output),
        "worst_write_plan_nano_usd": requests * (tokens * limits.rates.write_12h_nano_usd + output),
        "rate_revalidation_required_before_live": True,
    }


async def run_pilot_preparation(
    session, metadata, goal, budget, invocation_id, responder, *, mode="offline"
):
    if mode != "offline":
        raise BudgetDenied("pilot_live_activation_not_authorized")
    preflight = pilot_preflight(session, goal, budget)
    report = await run_mock_buyer(session, metadata, goal, budget, invocation_id, responder)
    return {
        **preflight,
        "live_calls": 0,
        "model_behavior": "in_memory_responses_fixture_not_quality_evidence",
        "agent": report.model_dump(mode="json"),
        "call_budget": budget.report(),
    }
