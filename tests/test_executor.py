from __future__ import annotations

import pytest

from src.arbitrage.detectors import detect_same_market_cross
from src.arbitrage.executor import execute_opportunity
from src.models import Market


@pytest.mark.asyncio
async def test_executor_logs_dry_run_without_client() -> None:
    opportunity = detect_same_market_cross(
        [
            Market(
                ticker="MARKET",
                event_ticker="EVENT",
                yes_ask=45,
                no_ask=50,
            )
        ]
    )[0]

    results = await execute_opportunity(opportunity, quantity=2, dry_run=True)

    assert len(results) == 2
    assert all(result["dry_run"] for result in results)
