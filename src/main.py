"""Async orchestration loop for ingestion, detection, sizing, and simulation."""

from __future__ import annotations

import asyncio
from contextlib import suppress

import structlog

from .arbitrage.detectors import scan_all_opportunities
from .arbitrage.executor import execute_opportunity
from .arbitrage.risk import calculate_max_quantity
from .client import KalshiClient
from .config import Settings
from .storage import Database
from .websocket_manager import WebSocketManager

log = structlog.get_logger()


def configure_logging() -> None:
    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.add_log_level,
            structlog.processors.JSONRenderer(),
        ]
    )


async def initial_rest_sync(
    db: Database, client: KalshiClient, market_limit: int
) -> list[str]:
    events = await client.get_events(status="active")
    for event in events:
        await db.upsert_event(event)

    markets = await client.get_markets(limit=market_limit)
    for market in markets:
        await db.upsert_market(market)

    await log.ainfo(
        "initial_rest_sync_complete", events=len(events), markets=len(markets)
    )
    return [market.ticker for market in markets]


async def run_engine(settings: Settings) -> None:
    configure_logging()
    async with (
        Database(settings.database_path) as db,
        KalshiClient.from_settings(settings) as client,
    ):
        tickers = await initial_rest_sync(db, client, settings.market_limit)
        ws = WebSocketManager(db, client, settings.ws_url)
        listener: asyncio.Task[None] | None = None
        try:
            if tickers:
                await ws.connect()
                await ws.subscribe_markets(tickers)
                listener = asyncio.create_task(ws.listen(), name="kalshi-websocket")

            while True:
                if listener is not None and listener.done():
                    await listener
                opportunities = await scan_all_opportunities(db)
                opportunities.sort(
                    key=lambda item: (
                        item.confidence_score,
                        item.gross_profit_cents,
                    ),
                    reverse=True,
                )
                for opportunity in opportunities:
                    quantity = calculate_max_quantity(
                        opportunity, settings.max_collateral_cents
                    )
                    if quantity > 0:
                        await execute_opportunity(
                            opportunity,
                            quantity,
                            dry_run=settings.dry_run,
                            client=client,
                            db=db,
                        )
                await asyncio.sleep(settings.scan_interval_seconds)
        finally:
            if listener is not None:
                listener.cancel()
                with suppress(asyncio.CancelledError):
                    await listener
            await ws.close()


async def main() -> None:
    await run_engine(Settings.from_env())
