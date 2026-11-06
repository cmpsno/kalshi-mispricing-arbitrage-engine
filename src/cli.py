"""Command line interface for the asynchronous engine."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

import httpx
from dotenv import load_dotenv

from kalshi_client.auth import PrivateKeyError
from kalshi_client.client import KalshiAPIError
from kalshi_client.config import ConfigError

from .main import main as engine_main
from .paper.config import PaperSettings
from .paper.service import PaperService
from .storage import Database


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Kalshi structural arbitrage engine")
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser(
        "run-engine",
        help="start REST ingestion, WebSocket streaming, and dry-run scanning",
    )
    report_parser = subcommands.add_parser(
        "paper-report",
        help="print the paper-trading performance report from the ledger",
    )
    report_parser.add_argument(
        "--week",
        action="store_true",
        help="report the current week (Mon-Sun, America/New_York) instead of overall",
    )
    close_parser = subcommands.add_parser(
        "paper-close",
        help="close (part of) an open paper position by offsetting trade",
    )
    close_parser.add_argument("--ticker", required=True)
    close_parser.add_argument("--side", required=True, choices=("yes", "no"))
    close_parser.add_argument("--direction", required=True, choices=("buy", "sell"))
    close_parser.add_argument("--quantity", required=True, type=int)
    return parser


async def _paper_report(weekly: bool) -> dict:
    # Paper reporting is local: it needs only the database path, not
    # Kalshi credentials.
    database_path = os.getenv("KALSHI_DATABASE_PATH", "kalshi_arbitrage.db")
    paper_settings = PaperSettings.from_env()
    async with Database(database_path) as db:
        service = PaperService(db, paper_settings)
        if await db.get_paper_account() is None:
            return {
                "paper_account": None,
                "message": (
                    "No paper account yet. Run the engine once with "
                    "PAPER_ENABLED=true to create it."
                ),
            }
        if weekly:
            return await service.weekly_report()
        books = await service.latest_books()
        return await service.overall_report(books)


async def _paper_close(ticker: str, side: str, direction: str, quantity: int) -> dict:
    database_path = os.getenv("KALSHI_DATABASE_PATH", "kalshi_arbitrage.db")
    paper_settings = PaperSettings.from_env()
    async with Database(database_path) as db:
        service = PaperService(db, paper_settings)
        books = await service.latest_books()
        result = await service.close_position(
            ticker, side, direction, quantity, books
        )
        return {
            "status": result.status,
            "reject_reason": result.reject_reason,
            "orders": [order.as_dict() for order in result.orders],
            "fills": [fill.as_dict() for fill in result.fills],
        }


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    load_dotenv()
    if args.command == "run-engine":
        pass
    elif args.command in ("paper-report", "paper-close"):
        try:
            if args.command == "paper-report":
                report = asyncio.run(_paper_report(args.week))
            else:
                report = asyncio.run(
                    _paper_close(
                        args.ticker, args.side, args.direction, args.quantity
                    )
                )
        except ValueError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        print(json.dumps(report, indent=2, default=str))
        return 0
    else:
        return 2

    try:
        asyncio.run(engine_main())
    except KeyboardInterrupt:
        print("Engine stopped.", file=sys.stderr)
        return 130
    except (
        ConfigError,
        PrivateKeyError,
        KalshiAPIError,
        httpx.HTTPError,
        ValueError,
    ) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
