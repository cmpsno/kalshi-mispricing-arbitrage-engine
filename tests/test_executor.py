from __future__ import annotations

import pytest

from src.arbitrage.detectors import detect_same_market_cross
from src.arbitrage.executor import execute_opportunity
from src.models import Market, OrderExecutionResult


class RecordingClient:
    def __init__(self, statuses: list[str]) -> None:
        self.statuses = statuses
        self.orders = []

    async def place_orders(self, orders):
        self.orders = orders
        return [
            OrderExecutionResult(
                client_order_id=order.client_order_id,
                order_id=f"order-{index}",
                ticker=order.ticker,
                requested_quantity=order.quantity,
                filled_quantity=order.quantity if status == "filled" else 0,
                remaining_quantity=0 if status == "filled" else order.quantity,
                status=status,
            )
            for index, (order, status) in enumerate(
                zip(orders, self.statuses, strict=True)
            )
        ]


class RecordingDatabase:
    def __init__(self) -> None:
        self.executed = []
        self.attempts = []

    async def save_execution_attempt(self, opportunity_id, status, results) -> None:
        self.attempts.append((opportunity_id, status, results))

    async def mark_opportunity_executed(self, opportunity_id: str) -> None:
        self.executed.append(opportunity_id)


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
    assert all(result["status"] == "dry_run" for result in results)


@pytest.mark.asyncio
async def test_executor_submits_all_legs_and_marks_only_complete_fill() -> None:
    opportunity = detect_same_market_cross(
        [Market(ticker="MARKET", event_ticker="EVENT", yes_ask=45, no_ask=50)]
    )[0]
    client = RecordingClient(["filled", "filled"])
    db = RecordingDatabase()

    results = await execute_opportunity(
        opportunity, quantity=2, dry_run=False, client=client, db=db
    )

    assert [order.side for order in client.orders] == ["yes", "no"]
    assert len({order.client_order_id for order in client.orders}) == 2
    assert [result["status"] for result in results] == ["filled", "filled"]
    assert db.executed == [opportunity.id]
    assert db.attempts[0][1] == "filled"


@pytest.mark.asyncio
async def test_executor_does_not_mark_partial_batch() -> None:
    opportunity = detect_same_market_cross(
        [Market(ticker="MARKET", event_ticker="EVENT", yes_ask=45, no_ask=50)]
    )[0]
    client = RecordingClient(["filled", "partial"])
    db = RecordingDatabase()

    results = await execute_opportunity(
        opportunity, quantity=2, dry_run=False, client=client, db=db
    )

    assert [result["status"] for result in results] == ["filled", "partial"]
    assert db.executed == []
    assert db.attempts[0][1] == "partial"


@pytest.mark.asyncio
async def test_executor_rejects_invalid_input_before_network() -> None:
    opportunity = detect_same_market_cross(
        [Market(ticker="MARKET", event_ticker="EVENT", yes_ask=45, no_ask=50)]
    )[0]
    client = RecordingClient(["filled", "filled"])

    with pytest.raises(ValueError, match="quantity"):
        await execute_opportunity(opportunity, 0, dry_run=False, client=client)
    with pytest.raises(ValueError, match="collateral"):
        await execute_opportunity(opportunity, 11, dry_run=False, client=client)
    assert client.orders == []


@pytest.mark.asyncio
async def test_executor_rejects_empty_or_malformed_legs() -> None:
    opportunity = detect_same_market_cross(
        [Market(ticker="MARKET", event_ticker="EVENT", yes_ask=45, no_ask=50)]
    )[0]
    client = RecordingClient([])

    opportunity.details["legs"] = []
    with pytest.raises(ValueError, match="at least one"):
        await execute_opportunity(opportunity, 1, dry_run=False, client=client)
    opportunity.details["legs"] = [
        {"ticker": "MARKET", "side": "bad", "price": 45}
    ]
    with pytest.raises(ValueError, match="invalid"):
        await execute_opportunity(opportunity, 1, dry_run=False, client=client)
