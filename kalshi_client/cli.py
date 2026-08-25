"""Command-line entry point for the Phase 0 authenticated markets request."""

from __future__ import annotations

import argparse
import json
import sys

import httpx
from dotenv import load_dotenv

from .auth import PrivateKeyError
from .client import KalshiAPIError, KalshiClient
from .config import ConfigError, Settings


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Fetch a page of Kalshi markets with an authenticated request."
    )
    parser.add_argument(
        "--limit", type=int, default=5, help="markets to return (1-1000)"
    )
    parser.add_argument(
        "--status",
        choices=("unopened", "open", "closed", "settled"),
        help="optional market status filter",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    load_dotenv()

    try:
        settings = Settings.from_env()
        with KalshiClient.from_settings(settings) as client:
            payload = client.get_markets(limit=args.limit, status=args.status)
    except (
        ConfigError,
        PrivateKeyError,
        KalshiAPIError,
        httpx.HTTPError,
        ValueError,
    ) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0
