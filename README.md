# Kalshi Mispricing Arbitrage Engine

[![Tests](https://github.com/isaiahcampusano/kalshi-mispricing-arbitrage-engine/actions/workflows/tests.yml/badge.svg)](https://github.com/isaiahcampusano/kalshi-mispricing-arbitrage-engine/actions/workflows/tests.yml)

An incremental, test-first project for studying structural price inconsistencies on Kalshi. It includes the original Phase 0 authenticated request plus a local, asynchronous engine for REST ingestion, WebSocket order books and trades, SQLite persistence, structural detection, collateral sizing, dry runs, and demo order submission.

Real-money execution is not implemented. Setting `DRY_RUN=false` sends IOC limit orders only when both configured endpoints are Kalshi demo hosts; production hosts are rejected during configuration.

## Phase 0 setup

Requirements: Python 3.11 or newer and a Kalshi **demo** account with an API key.

1. Create and activate a virtual environment:

   ```shell
   python -m venv .venv
   # Windows PowerShell
   .venv\Scripts\Activate.ps1
   # macOS/Linux
   source .venv/bin/activate
   ```

2. Install the project and its test dependencies:

   ```shell
   python -m pip install -e ".[dev]"
   ```

3. Copy `.env.example` to `.env`, then set your demo API key ID and the path to your private PEM file. Keep the private key outside this repository.

4. Fetch and print open markets with an authenticated request:

   ```shell
   python -m kalshi_client --status open --limit 5
   ```

   The equivalent installed command is:

   ```shell
   kalshi-markets --status open --limit 5
   ```

The default API root is Kalshi's recommended demo endpoint, `https://external-api.demo.kalshi.co/trade-api/v2`. Production must be selected deliberately by changing `KALSHI_BASE_URL`.

## Configuration

| Variable | Required | Default |
|---|---:|---|
| `KALSHI_API_KEY_ID` | yes | — |
| `KALSHI_PRIVATE_KEY_PATH` | yes | — |
| `KALSHI_BASE_URL` | no | demo Trade API root |
| `KALSHI_REQUEST_TIMEOUT_SECONDS` | no | `10` |

Request signatures follow Kalshi's current contract: base64-encoded RSA-PSS/SHA-256 over `timestamp_ms + HTTP_METHOD + full_request_path`. Query parameters are excluded from the signed path.

## Tests

```shell
python -m pytest
```

The tests generate temporary RSA keys and use an in-memory HTTP transport. They do not need credentials or network access.

See [PROJECT_PLAN.md](PROJECT_PLAN.md) for the phased roadmap and risk constraints.

## Run the local engine

After installing the development dependencies, copy `.env.example` to `.env` and add credentials from a Kalshi **demo** account. Then run:

```shell
python -m src.cli run-engine
```

The engine will:

1. Fetch open events and markets through authenticated REST requests.
2. Store normalized integer-cent data in `kalshi_arbitrage.db`.
3. Authenticate the current Trade API v2 WebSocket handshake and subscribe to `orderbook_delta` and `trade`.
4. Reconstruct full order books from snapshots and deltas.
5. Scan every two seconds for complement, event-sum, and strike-ladder inconsistencies.
6. Log structured JSON opportunities and either simulate their legs or submit all legs as one demo batch.

Relevant settings:

| Variable | Default | Purpose |
|---|---:|---|
| `KALSHI_WS_URL` | demo WebSocket v2 URL | Streaming endpoint |
| `MAX_COLLATERAL_CENTS` | `1000000` | Maximum collateral used for sizing; transmitted demo orders are always capped at `1000` cents per opportunity |
| `PAPER_ENABLED` | `false` | `true` runs the engine in local paper-trading mode instead of submitting demo orders |
| `PAPER_INITIAL_CASH_CENTS` | `100000` | Starting paper capital ($1,000); fixed when the paper account is created |
| `PAPER_MAX_BOOK_AGE_SECONDS` | `5` | A leg's order book older than this is stale and its opportunity is rejected |
| `DRY_RUN` | `true` | `true` logs only; `false` enables demo-only batch submission |
| `KALSHI_DATABASE_PATH` | `kalshi_arbitrage.db` | Local SQLite database |
| `KALSHI_SCAN_INTERVAL_SECONDS` | `2` | Detector interval |
| `KALSHI_MARKET_LIMIT` | `1000` | Maximum markets in the initial sync |

## Streamlit dashboard

The dashboard provides auto-refreshing metrics, profit and activity charts, detector summaries, and a table of recent opportunities. It reads the engine's SQLite database in read-only mode and uses the same `KALSHI_DATABASE_PATH` setting as the engine.

Start the engine in one terminal, then start the dashboard in another terminal using the same virtual environment:

```shell
python -m src.cli run-engine
```

```shell
streamlit run dashboard.py
```

Streamlit opens the dashboard at `http://localhost:8501`. Use the sidebar to change the auto-refresh interval, history window, or row limit. If the database or its `opportunities` table does not exist yet, the dashboard displays an initialization message instead of failing.

### Demo order submission

Keep `DRY_RUN=true` until you have inspected detected opportunities and logs. To transmit orders in Kalshi's demo environment, retain the demo REST and WebSocket URLs from `.env.example`, set `DRY_RUN=false`, and start the engine normally.

Every leg is submitted as an immediate-or-cancel limit order through Kalshi's current event-order batch endpoint. Batch submission is not atomic: one leg can fill while another partially fills or is rejected. The engine records every aggregate result and its per-leg details in `execution_attempts`, and marks an opportunity executed only when every leg fills completely. Otherwise it emits an `arbitrage_execution_exposure` error for manual review; it does not automatically unwind residual demo positions.

The live path rejects opportunities above $10 (1,000 cents) of collateral regardless of `MAX_COLLATERAL_CENTS`. It also rejects production endpoints, invalid or empty legs, invalid prices, and non-positive quantities before sending a request. This demo capability is for testing execution behavior and is not financial advice.

### Paper trading (local simulation)

Paper trading is **not** the demo exchange above. It is a local simulation
that runs in the engine instead of submitting demo orders: set
`PAPER_ENABLED=true` and detected opportunities are simulated against the
observed order books, with results recorded in SQLite. Nothing is sent to
any Kalshi host in paper mode, and no real money is involved.

How orders and fills are simulated (`src/paper/simulator.py`):

- Each opportunity's legs become IOC limit orders at the detector's prices.
- A leg fills only up to the displayed size at prices at or better than its
  limit, walking the book's levels; the fill price is the volume-weighted
  average of the levels consumed. A detector signal alone is never a fill,
  and a quote without a backing book is never a fill.
- NO legs trade on the complementary price scale (NO ask = 100 − YES bid,
  NO bid = 100 − YES ask).
- Every leg's book must be no older than `PAPER_MAX_BOOK_AGE_SECONDS`
  (default 5 seconds) at order time, or the whole opportunity is rejected.
  Sequence gaps in the WebSocket feed invalidate the affected book until a
  fresh snapshot arrives.
- Unfilled quantity expires immediately (IOC). Partial fills are recorded
  per leg, so a half-filled arbitrage keeps its residual exposure visible
  instead of being averaged away.
- Positions close only by offsetting trades (`python -m src.cli
  paper-close --ticker … --side … --direction … --quantity …`). There is
  no automatic settlement yet: the outcome source for settled markets has
  not been verified, so positions in expired markets stay open and flagged.

Accounting (`PAPER_TRADING_PLAN.md`, `src/paper/report.py`):

- Initial paper capital is `PAPER_INITIAL_CASH_CENTS` (default $1,000),
  fixed when the account is created. Cash is the actual paper balance:
  buys decrease it, sells increase it.
- Equity = cash + mark-to-market value of open positions (long YES at the
  best YES bid, long NO at the best NO bid, shorts at the cost to cover).
  Reserved collateral (margin) is reported separately and never added to
  equity. Net P&L = realized P&L + unrealized P&L; ROI = net P&L ÷ initial
  capital, with a realized-only ROI alongside it.
- **Fees are not included.** No verified Kalshi fee formula is implemented
  yet, so every paper figure is gross of fees and labelled as such.
  Slippage beyond the book walk is not modelled either: fills use
  displayed prices and sizes only.
- The dashboard's **Paper Trading Performance** section shows initial
  capital, cash, reserved collateral, equity, realized/unrealized/net P&L,
  ROI, simulated orders (filled / partially filled / unfilled), capital
  deployed, the current week's return (Monday–Sunday, America/New_York),
  an equity curve, open positions, recent fills, and the ledger history.
  Detector opportunities are shown separately from trades. If no paper
  account exists, the section says so instead of showing zeros.
- Reported performance covers only the period in the paper ledger (check
  the section's period start/end and last-update time). Sample/demo
  opportunity figures elsewhere on the dashboard are not paper results and
  are never combined with them.

Known limitations: fees unverified and excluded; settlement not
implemented; whole-cent prices and whole-contract quantities (sub-cent
prices and fractional sizes are rounded/truncated by the existing engine
models); books are trusted only up to sequence-gap detection and the
5-second freshness gate; a crash between the simulator's sequential
database writes could leave a partial batch (the ledger is append-only,
so the partial state stays visible rather than silently corrected). See
`PAPER_TRADING_AUDIT.md` and `PAPER_TRADING_PLAN.md` for the audit and
the full measurement model.

```shell
python -m src.cli paper-report          # overall paper performance
python -m src.cli paper-report --week   # current week's performance
```

## Important specification corrections

The supplied strict specification is retained in [ARBITRAGE_ENGINE_SPEC.md](ARBITRAGE_ENGINE_SPEC.md), but several stale assumptions were corrected in code against Kalshi's current official API contracts:

- The demo WebSocket is `wss://external-api-ws.demo.kalshi.co/trade-api/ws/v2`, authenticated with signed headers during the handshake.
- Subscription commands use `cmd`, `params.channels`, and `market_tickers`.
- YES and NO are complementary sides of one Kalshi market, not separate `-Y` and `-N` tickers.
- Same-market arbitrage therefore uses `YES ask + NO ask < 100` or `YES bid + NO bid > 100`.
- Mutually exclusive sums are scoped to a single event; markets from different dates in a series are never added together.
- Current fixed-point dollar fields are converted once to integer cents for detector math.
