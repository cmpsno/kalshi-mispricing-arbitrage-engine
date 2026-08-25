# Kalshi Arbitrage Engine - Strict Implementation Specification

## 1. Objective
Implement Phases 1–6 of the Kalshi Arbitrage Engine on top of the existing Phase 0 (authenticated REST client). 
The final output must be a running Python process that:
1. Fetches all active event/market data via REST.
2. Subscribes to real-time order books and trades via WebSocket.
3. Runs three distinct arbitrage detection algorithms in real-time.
4. Calculates risk-adjusted position sizes and logs executable opportunities (with simulated or real order placement).

## 2. Tech Stack & Constraints
- **Python**: 3.11+
- **HTTP Client**: Extend existing `httpx` usage. Use `httpx.AsyncClient` for non-blocking requests.
- **WebSocket**: Use `websockets` library (`>=12.0`).
- **Data Validation**: Use `pydantic` (`>=2.0`) for all internal models.
- **Database**: Use `aiosqlite` (async SQLite) with raw parameterized SQL. Avoid ORM to keep it lightweight.
- **Logging**: Use structured logging via `structlog` (or standard `logging` with JSON formatter).
- **Testing**: Extend existing `pytest` suite with async tests using `pytest-asyncio`.

**Update `pyproject.toml` dependencies**:
```toml
dependencies = [
    "cryptography>=42,<47",
    "httpx>=0.27,<1",
    "python-dotenv>=1,<2",
    "pydantic>=2.5,<3",
    "websockets>=12.0,<13",
    "aiosqlite>=0.19,<1",
    "structlog>=24.1,<25",
]
[project.optional-dependencies]
dev = ["pytest>=8,<10", "pytest-asyncio>=0.23,<1"]
```

## 3. Folder & File Structure
Strictly implement the following files. Create them as specified.

```
src/
├── __init__.py
├── config.py          # Extend Settings with new env vars (WS_URL, MAX_COLLATERAL, etc.)
├── auth.py            # Unchanged
├── client.py          # EXTEND KalshiClient with async methods
├── models.py          # NEW: Pydantic models for Event, Market, OrderBook, Trade, Opportunity
├── storage.py         # NEW: Database class using aiosqlite
├── websocket_manager.py # NEW: Async WebSocket handler
├── arbitrage/
│   ├── __init__.py
│   ├── detectors.py   # NEW: Core arbitrage algorithms
│   ├── risk.py        # NEW: Position sizing and collateral checks
│   └── executor.py    # NEW: Order placement logic
├── main.py            # NEW: Async main orchestration loop
└── cli.py             # EXTEND with "run-engine" command
```

## 4. Data Models (`src/models.py`)
Define exact Pydantic models. **Field names must match Kalshi API JSON keys exactly**.

```python
from pydantic import BaseModel, Field
from datetime import datetime
from typing import Optional, List, Literal, Dict
from decimal import Decimal

class Event(BaseModel):
    event_ticker: str
    series_ticker: str
    title: str
    subtitle: str
    status: str  # "active", "closed", etc.
    close_time: datetime
    markets: Optional[List[str]] = []  # list of market tickers

class Market(BaseModel):
    ticker: str
    event_ticker: str
    series_ticker: str
    title: str
    subtitle: str  # Contains "Yes" or "No" for binary markets
    status: str
    yes_bid: Decimal = Decimal("0.00")
    yes_ask: Decimal = Decimal("0.00")
    no_bid: Decimal = Decimal("0.00")
    no_ask: Decimal = Decimal("0.00")
    last_price: Optional[Decimal] = None
    strike_price: Optional[Decimal] = None  # For numeric markets, parse from ticker if needed

class OrderBookLevel(BaseModel):
    price: Decimal
    size: int

class OrderBook(BaseModel):
    ticker: str
    timestamp: datetime
    bids: List[OrderBookLevel]
    asks: List[OrderBookLevel]

class Trade(BaseModel):
    ticker: str
    trade_id: str
    price: Decimal
    size: int
    side: Literal["buy", "sell"]
    timestamp: datetime

class ArbitrageOpportunity(BaseModel):
    id: str  # UUID generated
    detected_at: datetime
    algorithm: Literal["same_market_cross", "mutually_exclusive_sum", "strike_monotonicity"]
    markets_involved: List[str]  # tickers
    action: Literal["buy_yes_sell_no", "sell_yes_buy_no", "buy_all_outcomes", "sell_all_outcomes", "buy_spread", "sell_spread"]
    gross_profit_cents: int  # Profit in absolute cents (micros)
    required_collateral_cents: int
    confidence_score: float  # 0.0 to 1.0, based on spread width vs latency
    details: Dict  # JSON dict for debugging (e.g., { "yes_ask": 52, "no_bid": 49 })
```

## 5. Database Layer (`src/storage.py`)
Use `aiosqlite`. Implement the following schema and methods. **Use raw SQL strings**. The DB file must be `kalshi_arbitrage.db`.

**Schema DDL**:
```sql
CREATE TABLE IF NOT EXISTS events (
    event_ticker TEXT PRIMARY KEY,
    series_ticker TEXT,
    title TEXT,
    subtitle TEXT,
    status TEXT,
    close_time INTEGER, -- unix timestamp
    updated_at INTEGER
);
CREATE TABLE IF NOT EXISTS markets (
    ticker TEXT PRIMARY KEY,
    event_ticker TEXT,
    series_ticker TEXT,
    title TEXT,
    subtitle TEXT,
    status TEXT,
    yes_bid INTEGER, -- store as integer cents
    yes_ask INTEGER,
    no_bid INTEGER,
    no_ask INTEGER,
    last_price INTEGER,
    strike_price INTEGER,
    updated_at INTEGER
);
CREATE TABLE IF NOT EXISTS order_books (
    ticker TEXT,
    timestamp INTEGER,
    bids_json TEXT, -- JSON string of list of [price, size]
    asks_json TEXT,
    PRIMARY KEY (ticker, timestamp)
);
CREATE TABLE IF NOT EXISTS trades (
    trade_id TEXT PRIMARY KEY,
    ticker TEXT,
    price INTEGER,
    size INTEGER,
    side TEXT,
    timestamp INTEGER
);
CREATE TABLE IF NOT EXISTS opportunities (
    id TEXT PRIMARY KEY,
    detected_at INTEGER,
    algorithm TEXT,
    markets_involved TEXT, -- comma separated tickers
    action TEXT,
    gross_profit_cents INTEGER,
    required_collateral_cents INTEGER,
    confidence_score REAL,
    details_json TEXT,
    executed BOOLEAN DEFAULT 0
);
```

**Class Methods Required**:
- `async def upsert_event(event: Event)`
- `async def upsert_market(market: Market)`
- `async def insert_order_book(order_book: OrderBook)`
- `async def insert_trade(trade: Trade)`
- `async def get_markets_by_event(event_ticker: str) -> List[Market]`
- `async def get_markets_by_series(series_ticker: str) -> List[Market]`
- `async def get_market(ticker: str) -> Market`
- `async def save_opportunity(opp: ArbitrageOpportunity)`
- `async def get_recent_opportunities(limit: int = 100) -> List[ArbitrageOpportunity]`

## 6. Extend REST Client (`src/client.py`)
Extend the existing `KalshiClient` with **async** methods. Add a new `_async_client` property using `httpx.AsyncClient`. Use the same authentication header generator.

Implement these specific methods:
- `async def get_events(status: str = "active") -> List[Event]`
- `async def get_markets(event_ticker: Optional[str] = None, limit: int = 1000) -> List[Market]`
- `async def get_order_book(ticker: str) -> OrderBook`
- `async def get_trades(ticker: str, limit: int = 100) -> List[Trade]`
- `async def place_order(ticker: str, side: str, type: str, price: int, quantity: int) -> dict` (Simulate for now, but format the payload to match Kalshi's /orders endpoint spec).

**Important**: Kalshi returns prices in cents (integer). Keep everything as `int` (cents) internally to avoid floating-point errors. Only convert to Decimal for display if necessary, but strictly use `int` for calculations in the arbitrage logic.

## 7. WebSocket Manager (`src/websocket_manager.py`)
Kalshi WebSocket URL: `wss://trading.kalshi.com/v1/ws`. 
Implement a singleton class `WebSocketManager`:

```python
class WebSocketManager:
    def __init__(self, db: Database, client: KalshiClient):
        self.db = db
        self.client = client
        self.connection = None
        self.subscribed_tickers = set()
        self.running = False

    async def connect(self):
        # Authenticate by sending {"event": "auth", "payload": {"api_key": ..., "signature": ...}}
        # Use the client's auth header generation logic.
        pass

    async def subscribe_orderbook(self, ticker: str):
        # Send {"event": "subscribe", "channel": "orderbook", "payload": {"ticker": ticker}}
        pass

    async def subscribe_trades(self, ticker: str):
        # Send {"event": "subscribe", "channel": "trades", "payload": {"ticker": ticker}}
        pass

    async def listen(self):
        # Infinite loop: receive messages, parse JSON, call self.db.insert_order_book or self.db.insert_trade
        pass
```
**Message handling**: When orderbook updates arrive, they are deltas. Apply deltas to the local in-memory cache of the orderbook before storing full snapshots to the DB. Maintain a dictionary `self.orderbooks: Dict[str, OrderBook]` that applies updates.

## 8. Arbitrage Detectors (`src/arbitrage/detectors.py`)
Implement three pure functions (and a main orchestrator). **All prices are in integer cents**.

**Helper utility**: Parse ticker to determine if it's a "Yes" or "No". Kalshi standard: ticker ends with `-Y` (Yes) or `-N` (No). 
- `def is_yes(ticker: str) -> bool: return ticker.endswith("-Y")`
- `def is_no(ticker: str) -> bool: return ticker.endswith("-N")`
- `def base_ticker(ticker: str) -> str: return ticker[:-2]` (e.g., "SPY123456-Y" -> "SPY123456")

### 8.1 Algorithm A: Same-Market Yes/No Crossing
**Condition**: For a given base ticker `T`, we have `T-Y` and `T-N`. 
Arbitrage exists if `Yes.ask <= No.bid` -> Buy Yes, Sell No. 
Or if `No.ask <= Yes.bid` -> Buy No, Sell Yes.
**Profit Cents** = (Bid_price - Ask_price). 
**Collateral** = Ask_price * quantity + fee buffer.
**Function**: `detect_same_market_cross(markets: List[Market]) -> List[ArbitrageOpportunity]`

### 8.2 Algorithm B: Mutually Exclusive Events (Sum-to-1)
Group markets by `series_ticker`. For a given series, all `Yes` markets for mutually exclusive outcomes should sum to 1 (100 cents). 
Check if `sum(Yes.ask for all Yes outcomes) < 100` -> Buy all Yes outcomes. Profit = 100 - sum(asks). 
Check if `sum(Yes.bid for all Yes outcomes) > 100` -> Sell all Yes outcomes. Profit = sum(bids) - 100.
**Function**: `detect_series_sum_deviation(markets: List[Market], series_ticker: str) -> List[ArbitrageOpportunity]`

### 8.3 Algorithm C: Strike Ladder Monotonicity (for numeric markets)
Identify markets belonging to the same `series_ticker` and event where the underlying is numeric (e.g., "KRONUSDT").
Parse `strike_price` from the market title or ticker (use regex `r"(\d+\.?\d*)"` or standard Kalshi patterns). 
Order markets by strike ascending. For Yes markets, prices must be non-increasing as strike increases. 
If `Yes.ask` for lower strike > `Yes.bid` for higher strike, violation exists. 
If lower strike > higher strike, violation exists. 
**Example**: K1 (strike 100) Yes Ask = 60, K2 (strike 105) Yes Bid = 65. Since K2 > K1, Yes Bid should be <= K1 Ask. 65 > 60 -> Arbitrage: Buy K1 Yes, Sell K2 Yes.
**Function**: `detect_strike_monotonicity_violations(markets: List[Market]) -> List[ArbitrageOpportunity]`

**Orchestrator**: `async def scan_all_opportunities(db: Database) -> List[ArbitrageOpportunity]` which pulls all active markets from the DB, runs the three detectors, and saves them to the DB.

## 9. Risk & Position Sizing (`src/arbitrage/risk.py`)
Implement deterministic position sizing.
- `calculate_max_quantity(opportunity: ArbitrageOpportunity, max_collateral_cents: int) -> int`: Returns the max quantity (contracts) that can be bought/sold given the available collateral. Collateral is usually `quantity * ask_price` for buying, and `quantity * bid_price` for selling (covered by collateral).
- Add a 5% buffer to required collateral to avoid liquidation.

## 10. Order Executor (`src/arbitrage/executor.py`)
- `async def execute_opportunity(opp: ArbitrageOpportunity, quantity: int, dry_run: bool = True)`: 
  - If `dry_run`, just log the order.
  - If not, use `KalshiClient.place_order()` for each leg of the arbitrage (using `limit` orders, priced at the current best bid/ask to ensure execution).
  - Mark the opportunity as `executed=True` in DB only if all legs fill successfully.

## 11. Main Loop (`src/main.py`)
Create an async main function:
```python
async def main():
    settings = Settings.from_env()
    db = Database("kalshi_arbitrage.db")
    client = KalshiClient.from_settings(settings)
    ws = WebSocketManager(db, client)

    # 1. Initial REST sync
    events = await client.get_events(status="active")
    for event in events:
        await db.upsert_event(event)
        markets = await client.get_markets(event_ticker=event.event_ticker)
        for market in markets:
            await db.upsert_market(market)
            # Subscribe to active markets
            await ws.subscribe_orderbook(market.ticker)
            await ws.subscribe_trades(market.ticker)

    # 2. Start websocket listener
    asyncio.create_task(ws.listen())

    # 3. Arbitrage scanning loop (every 2 seconds)
    while True:
        opps = await scan_all_opportunities(db)
        for opp in opps:
            # Sort by confidence / profit
            qty = calculate_max_quantity(opp, settings.max_collateral_cents)
            if qty > 0:
                await execute_opportunity(opp, qty, dry_run=settings.dry_run)
        await asyncio.sleep(2)

if __name__ == "__main__":
    asyncio.run(main())
```

## 12. CLI Extension (`src/cli.py`)
Add a new command: `python -m src.cli run-engine` that calls `main()`.
Update `__main__` to parse subcommands.

## 13. Environment Variables (`.env`)
Add these new required variables:
```
KALSHI_WS_URL=wss://trading.kalshi.com/v1/ws
MAX_COLLATERAL_CENTS=1000000  # $10,000
DRY_RUN=true
```

## 14. Testing Requirements
For each `detectors.py` function, write a `pytest-asyncio` test that passes mock lists of `Market` objects and asserts the correct `ArbitrageOpportunity` is generated. 
- Test same-market crossing with Yes Ask=55, No Bid=60 -> profit 5 cents.
- Test series sum with asks 30, 30, 30 (sum 90) -> profit 10 cents.
- Test monotonicity with K1 Ask=60, K2 Bid=65 -> violation.

## 15. Acceptance Criteria
- [ ] `python -m src.cli run-engine` starts without crashing.
- [ ] Database `kalshi_arbitrage.db` is populated with events, markets, and real-time orderbooks.
- [ ] Opportunities are logged to the console (via `structlog`) every 2 seconds if found.
- [ ] All tests in `/tests` pass.

---

**Instruction for Codex**: 
"Read this specification file completely. Execute the implementation file-by-file in the order they appear. Do not deviate from the field names, class names, or function signatures provided. Ensure all async loops handle graceful shutdown (KeyboardInterrupt)."
