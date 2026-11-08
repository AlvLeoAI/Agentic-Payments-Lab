"""Frozen direct-OpenAI Standard Luna card; estimates, never a billing guarantee.

Verified 2026-11-07 against the pricing and prompt-caching pages below. Cache
writes are total rates for disjoint input categories, not additive input charges.
No account access, credit balance, region or future price validity is inferred.
"""

from typing import Annotated, Literal

from pydantic import Field

from ..x402_contracts import StrictModel


class LunaRates(StrictModel):
    source: Literal["openai_luna_standard_2026_11_07"] = "openai_luna_standard_2026_11_07"
    pricing_url: Literal["https://developers.openai.com/api/docs/pricing"] = (
        "https://developers.openai.com/api/docs/pricing"
    )
    usage_url: Literal["https://developers.openai.com/api/docs/guides/prompt-caching"] = (
        "https://developers.openai.com/api/docs/guides/prompt-caching"
    )
    # USD nanounits per token: $0.10 / 1M = 100 nanounits per token.
    input_nano_usd: Annotated[int, Field(strict=True, ge=100, le=100)] = 100
    cached_input_nano_usd: Annotated[int, Field(strict=True, ge=10, le=10)] = 10
    write_30m_nano_usd: Annotated[int, Field(strict=True, ge=125, le=125)] = 125
    write_12h_nano_usd: Annotated[int, Field(strict=True, ge=250, le=250)] = 250
    output_nano_usd: Annotated[int, Field(strict=True, ge=500, le=500)] = 500

    def estimate(self, usage: dict) -> int:
        ordinary = usage["input_tokens"] - usage["cached_tokens"] - usage["cache_write_tokens"]
        return (
            ordinary * self.input_nano_usd
            + usage["cached_tokens"] * self.cached_input_nano_usd
            + usage["ttl_30m_tokens"] * self.write_30m_nano_usd
            + usage["ttl_12h_tokens"] * self.write_12h_nano_usd
            + usage["output_tokens"] * self.output_nano_usd
        )
