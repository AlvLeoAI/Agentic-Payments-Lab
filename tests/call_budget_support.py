"""Synthetic rate card and raw pinned-SDK Responses fixture, never provider pricing."""

from agentic_payments_lab.buyer_agent.call_budget import CallBudget, CallLimits, PlanningRates


def limits(**changes):
    return CallLimits(
        batch_id="offline-batch",
        model="gpt-6-luna",
        estimate_ceiling_nano_usd=10_000_000,
        rates=PlanningRates(
            input_nano_usd=3,
            cached_input_nano_usd=1,
            cache_write_surcharge_nano_usd=2,
            output_nano_usd=6,
        ),
        **changes,
    )


def initialized(path, **changes):
    return CallBudget.initialize(path / "call-budget.sqlite3", limits(**changes))


def usage():
    return {
        "input_tokens": 10,
        "output_tokens": 5,
        "total_tokens": 15,
        "input_tokens_details": {"cached_tokens": 2, "cache_write_tokens": 3},
        "output_tokens_details": {"reasoning_tokens": 1},
    }


def payload(number, output, *, raw_usage=None):
    return {
        "id": f"response-{number}",
        "object": "response",
        "created_at": 0,
        "status": "completed",
        "model": "gpt-6-luna",
        "service_tier": "default",
        "parallel_tool_calls": False,
        "output": [item.model_dump(mode="json") for item in output],
        "usage": usage() if raw_usage is None else raw_usage,
    }
