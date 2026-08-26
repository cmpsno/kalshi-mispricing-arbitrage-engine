"""Deterministic collateral-based position sizing."""

from __future__ import annotations

import math

from ..models import ArbitrageOpportunity

MAX_LIVE_COLLATERAL_CENTS = 1_000


def calculate_max_quantity(
    opportunity: ArbitrageOpportunity,
    max_collateral_cents: int,
    *,
    live: bool = False,
) -> int:
    if live:
        max_collateral_cents = min(
            max_collateral_cents, MAX_LIVE_COLLATERAL_CENTS
        )
    if max_collateral_cents <= 0 or opportunity.required_collateral_cents <= 0:
        return 0
    buffered_per_contract = math.ceil(opportunity.required_collateral_cents * 1.05)
    return max_collateral_cents // buffered_per_contract
