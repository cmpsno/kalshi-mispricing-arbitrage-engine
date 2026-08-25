# Kalshi Mispricing Arbitrage Engine — Project Plan

## 0. Scope: two projects hide under “mispricing arbitrage”

### Structural arbitrage (build this first)

Exploit logical inconsistencies within Kalshi's own prices:

- YES ask + NO ask should not allow a locked-in profit after fees.
- YES prices for mutually exclusive, collectively exhaustive markets should sum to approximately $1.
- Strike ladders on the same underlying and settlement date must be monotonic.

This requires careful handling of order books, fees, and settlement rules rather than a forecasting model. It is the first implementation target.

### Statistical mispricing (stretch goal)

Build an independent probability estimate and trade when it differs from Kalshi's implied probability. This has directional risk and requires a genuine forecasting edge, so it comes only after the structural pipeline is proven.

## 1. Prerequisites

- A KYC-complete Kalshi account with API access
- An RSA key pair; Kalshi receives the public key and the private key is stored locally and never committed
- Kalshi's demo environment until the engine is fully tested
- Python 3.11+
- The current official `openapi.yaml` and `asyncapi.yaml` from Kalshi's documentation as API ground truth

## 2. Suggested stack

| Purpose | Tool |
|---|---|
| HTTP and signed requests | `httpx`, `cryptography` (RSA-PSS signing) |
| Streaming | `websockets` |
| Data validation | `pydantic` |
| Storage | SQLite initially, Postgres later if needed |
| Analysis | `pandas` |
| Secrets | `python-dotenv`, `.env` (gitignored) |
| Tests | `pytest` |

## 3. Phased build plan

### Phase 0 — Scaffolding

Repository structure, `.env.example`, key management, and a `kalshi_client` module that performs one authenticated `GET /markets` request and prints the response. Validate request signing and configuration before adding downstream features.

### Phase 1 — REST data ingestion

Wrap events, markets, order books, candlesticks, and trades. Keep this phase read-only and expose focused methods such as `client.get_orderbook(ticker)`.

### Phase 2 — Storage

Add schemas for markets, event groupings, order book snapshots, and trades. Start with SQLite so stored history can be replayed later.

### Phase 3 — WebSocket streaming

Consume live order book deltas and trades through Kalshi's authenticated WebSocket connection. Persist snapshots on an interval for backtesting.

### Phase 4 — Structural mispricing detection

Implement three fee-aware detectors:

1. Same-market YES/NO crossing
2. Mutually exclusive and exhaustive event-sum deviations
3. Strike-ladder monotonicity violations

Each detector must use executable bid/ask prices and account for fees before reporting an opportunity.

### Phase 5 — Backtesting and signal evaluation

Replay stored order book history. Measure signal count, edge, persistence, realistic capture rate, and net returns after fees.

### Phase 6 — Paper execution

Manage orders in the demo environment, including placement, tracking, cancellation, partial fills, rejections, stale quotes, and execution races.

### Phase 7 — Risk controls

Before any live-money work, add position limits, maximum daily loss, a kill switch, and a complete audit trail of orders and decisions.

### Phase 8 — Statistical mispricing

Add an external probability model for a specific market category and explicitly treat its signals as directional strategies with real risk.

## 4. Target repository layout

```text
kalshi-mispricing-arbitrage-engine/
├── kalshi_client/       # Phases 0–1: signed REST and WebSocket clients
├── storage/             # Phase 2: schema and database access
├── detectors/           # Phase 4: structural checks
├── backtest/            # Phase 5: replay and evaluation
├── execution/           # Phases 6–7: orders and risk limits
├── models/              # Phase 8: external probability models
├── tests/
├── .env.example
└── PROJECT_PLAN.md
```

Create later-phase directories only when implementing those phases so the repository does not contain misleading placeholders.

## 5. Development workflow

- Work one phase at a time and use the relevant official Kalshi API specification as ground truth.
- Write implementation and tests together.
- Run and review each phase before beginning the next.
- Give extra scrutiny to authentication and detection logic, where subtle mistakes can silently produce incorrect signals.
- Never paste or commit credentials or private keys.

## 6. Immediate next steps

1. Create the Kalshi account and RSA API key.
2. Use the demo environment.
3. Complete and validate Phase 0.
4. Proceed to Phase 1 only after a real authenticated demo request succeeds.

## Risk note

Structural arbitrage is not risk-free in practice. Fees, settlement rules, partial execution, quote staleness, and latency can erase an apparent edge. Treat all work through paper execution as testing, and do not use real money before the risk controls in Phase 7 are implemented and tested. This project is not financial advice.
