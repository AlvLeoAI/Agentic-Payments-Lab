"""Responses fixtures with the documented disjoint cache-write lifetimes."""

from call_budget_support import payload

from agentic_payments_lab.buyer_agent.call_budget import CallBudget
from agentic_payments_lab.buyer_agent.pilot_preparation import pilot_limits


def usage():
    return {
        "input_tokens": 10,
        "output_tokens": 5,
        "total_tokens": 15,
        "input_tokens_details": {
            "cached_tokens": 2,
            "cache_write_tokens": 3,
            "cache_write_tokens_details": {"ttl_30m_tokens": 1, "ttl_12h_tokens": 2},
        },
        "output_tokens_details": {"reasoning_tokens": 1},
    }


def response(number, output):
    return payload(number, output, raw_usage=usage())


def initialized(path, session, goal, *, ceiling=250_000_000, **changes):
    # An offline test ceiling, not permission to spend $0.25 through a provider.
    return CallBudget.initialize(
        path / "pilot-budget.sqlite3",
        pilot_limits(session, goal, estimate_ceiling_nano_usd=ceiling, **changes),
    )
