"""Configuration for local paper trading.

Paper trading is a purely local simulation: it never calls the Kalshi API
and never submits an order to any host. It is disabled by default.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

DEFAULT_INITIAL_CASH_CENTS = 100_000  # $1,000 — confirmed by the owner (Phase 2)
DEFAULT_MAX_BOOK_AGE_SECONDS = 5.0  # confirmed by the owner (Phase 2)


def _parse_bool(name: str, value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be true or false")


@dataclass(frozen=True, slots=True)
class PaperSettings:
    """Paper-trading settings.

    ``initial_cash_cents`` is the explicitly configured starting capital.
    It has a proposed default ($1,000) only because the owner confirmed
    that value; it is never inferred from market data or results.
    """

    enabled: bool = False
    initial_cash_cents: int = DEFAULT_INITIAL_CASH_CENTS
    max_book_age_seconds: float = DEFAULT_MAX_BOOK_AGE_SECONDS

    def __post_init__(self) -> None:
        if self.initial_cash_cents <= 0:
            raise ValueError(
                "PAPER_INITIAL_CASH_CENTS must be greater than zero"
            )
        if self.max_book_age_seconds <= 0:
            raise ValueError(
                "PAPER_MAX_BOOK_AGE_SECONDS must be greater than zero"
            )

    @classmethod
    def from_env(cls) -> PaperSettings:
        try:
            return cls(
                enabled=_parse_bool(
                    "PAPER_ENABLED", os.getenv("PAPER_ENABLED", "false")
                ),
                initial_cash_cents=int(
                    os.getenv(
                        "PAPER_INITIAL_CASH_CENTS",
                        str(DEFAULT_INITIAL_CASH_CENTS),
                    )
                ),
                max_book_age_seconds=float(
                    os.getenv(
                        "PAPER_MAX_BOOK_AGE_SECONDS",
                        str(DEFAULT_MAX_BOOK_AGE_SECONDS),
                    )
                ),
            )
        except ValueError as exc:
            raise ValueError(f"Invalid paper-trading configuration: {exc}") from exc
