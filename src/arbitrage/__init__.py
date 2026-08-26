"""Structural arbitrage detection, sizing, and demo-safe execution."""

from .detectors import (
    detect_same_market_cross,
    detect_series_sum_deviation,
    detect_strike_monotonicity_violations,
    scan_all_opportunities,
)
from .risk import calculate_max_quantity

__all__ = [
    "calculate_max_quantity",
    "detect_same_market_cross",
    "detect_series_sum_deviation",
    "detect_strike_monotonicity_violations",
    "scan_all_opportunities",
]
