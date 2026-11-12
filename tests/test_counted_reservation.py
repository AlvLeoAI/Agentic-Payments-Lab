"""Step2 formula examples: placeholder prices, never verified account costs."""

import pytest

from agentic_payments_lab.buyer_agent.call_budget import BudgetDenied
from agentic_payments_lab.buyer_agent.comparison_protocol import (
    RESERVATION_POLICY,
    reservation_estimate,
)


@pytest.mark.parametrize(
    "provider,price,output,context",
    [("openai", 500, 750, 1050000), ("anthropic", 1000, 2500, 1000000)],
)
def test_counted_and_uncounted_rule(provider, price, output, context):
    assert reservation_estimate(provider, 10, overhead_margin=20) == 30 * price + 512 * output
    assert (
        reservation_estimate(provider, None, overhead_margin=20) == context * price + 512 * output
    )
    assert reservation_estimate(provider, 0, overhead_margin=0, max_output=1) == output
    assert reservation_estimate(provider, 10, overhead_margin=20) < reservation_estimate(
        provider, None
    )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"count": True},
        {"count": -1},
        {"count": 1.5},
        {"count": 1050001},
        {"count": 1050000},
        {"count": 10, "overhead_margin": -1},
        {"count": 10, "overhead_margin": True},
        {"count": 10, "max_output": 513},
        {"count": 10, "max_output": 0},
        {"count": 10, "max_output": True},
    ],
)
def test_invalid_bounds(kwargs):
    with pytest.raises(BudgetDenied):
        reservation_estimate("openai", **kwargs)


def test_output_is_one_total_bucket_including_reasoning():
    assert RESERVATION_POLICY["max_output_tokens_including_reasoning"] == 512
    assert "PLACEHOLDER" in RESERVATION_POLICY["price_status"]
    assert reservation_estimate("openai", 0, overhead_margin=0) == 512 * 750


def test_observed_settlement_uses_same_placeholder_prices_not_legacy_estimate():
    from agentic_payments_lab.buyer_agent.comparison_protocol import prospective_observed_estimate

    usage = {"complete": True, "prompt_tokens": 10, "output_tokens": 5, "estimated_nano_usd": 1}
    assert prospective_observed_estimate("openai", usage) == 8750
    assert prospective_observed_estimate("anthropic", usage) == 22500
    usage["complete"] = False
    assert prospective_observed_estimate("openai", usage) is None
