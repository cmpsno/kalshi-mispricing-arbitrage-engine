"""Validated opportunity execution for dry-run and Kalshi demo trading."""

from __future__ import annotations

import uuid
from typing import Any

import structlog

from ..client import KalshiClient
from ..models import ArbitrageOpportunity, OrderRequest
from ..storage import Database
from .risk import MAX_LIVE_COLLATERAL_CENTS

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
        raise ValueError("quantity must be at least 1")

    legs = opp.details.get("legs", [])
    if not isinstance(legs, list) or not legs:
        raise ValueError("opportunity must contain at least one execution leg")
    collateral = opp.required_collateral_cents * quantity
    if not dry_run and collateral > MAX_LIVE_COLLATERAL_CENTS:
        raise ValueError(
            f"live opportunity collateral exceeds {MAX_LIVE_COLLATERAL_CENTS} cents"
        )

    directions = {
        "buy_yes": ("buy", "yes"),
        "buy_no": ("buy", "no"),
        "sell_yes": ("sell", "yes"),
        "sell_no": ("sell", "no"),
    }
    orders: list[OrderRequest] = []
    for leg in legs:
        if not isinstance(leg, dict):
            raise TypeError("each execution leg must be an object")
        try:
            action, side = directions[str(leg["side"])]
            ticker = str(leg["ticker"]).strip()
            price = int(leg["price"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("execution leg has an invalid ticker, side, or price") from exc
        orders.append(
            OrderRequest(
                ticker=ticker,
                action=action,
                side=side,
                price=price,
                quantity=quantity,
                client_order_id=str(uuid.uuid4()),
            )
        )

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
        return [
            {
                "simulated": True,
                "dry_run": True,
                "status": "dry_run",
                "leg": leg,
            }
            for leg in legs
        ]
    if client is None:
        raise ValueError("client is required when dry_run is false")

    typed_results = await client.place_orders(orders)
    serialized_results = [result.model_dump(mode="json") for result in typed_results]
    fully_filled = bool(typed_results) and all(
        result.status == "filled" for result in typed_results
    )
    aggregate_status = (
        "filled"
        if fully_filled
        else (
            "rejected"
            if typed_results
            and all(result.status == "rejected" for result in typed_results)
            else "partial"
        )
    )
    if db is not None:
        await db.save_execution_attempt(opp.id, aggregate_status, serialized_results)
    if fully_filled and db is not None:
        await db.mark_opportunity_executed(opp.id)
    elif not fully_filled:
        await log.aerror(
            "arbitrage_execution_exposure",
            opportunity_id=opp.id,
            message="One or more IOC legs were partially filled or rejected; manual review required",
            results=serialized_results,
        )
    return serialized_results
