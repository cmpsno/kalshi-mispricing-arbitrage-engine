"""Opportunity execution; deliberately simulation-only through Phase 6."""

from __future__ import annotations

from typing import Any

import structlog

from ..client import KalshiClient
from ..models import ArbitrageOpportunity
from ..storage import Database

log = structlog.get_logger()


async def execute_opportunity(
    opp: ArbitrageOpportunity,
    quantity: int,
    dry_run: bool = True,
    *,
    client: KalshiClient | None = None,
    db: Database | None = None,
) -> list[dict[str, Any]]:
    if quantity < 1:
        return []

    legs = opp.details.get("legs", [])
    await log.ainfo(
        "arbitrage_opportunity",
        opportunity_id=opp.id,
        algorithm=opp.algorithm,
        action=opp.action,
        quantity=quantity,
        gross_profit_cents=opp.gross_profit_cents * quantity,
        dry_run=dry_run,
        legs=legs,
    )
    if dry_run:
        return [{"simulated": True, "dry_run": True, "leg": leg} for leg in legs]
    if client is None:
        raise ValueError("client is required when dry_run is false")

    results = [
        await client.place_order(
            ticker=leg["ticker"],
            side=leg["side"],
            type="limit",
            price=int(leg["price"]),
            quantity=quantity,
        )
        for leg in legs
    ]
    if (
        results
        and all(result.get("status") == "simulated_filled" for result in results)
        and db is not None
    ):
        await db.mark_opportunity_executed(opp.id)
    return results
