"""Command line interface for the asynchronous engine."""

from __future__ import annotations

import argparse
import asyncio
import sys

import httpx
from dotenv import load_dotenv

from kalshi_client.auth import PrivateKeyError
from kalshi_client.client import KalshiAPIError
from kalshi_client.config import ConfigError

from .main import main as engine_main


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Kalshi structural arbitrage engine")
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser(
        "run-engine",
        help="start REST ingestion, WebSocket streaming, and dry-run scanning",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    load_dotenv()
    if args.command != "run-engine":
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
