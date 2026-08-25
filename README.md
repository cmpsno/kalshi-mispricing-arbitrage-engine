# Kalshi Mispricing Arbitrage Engine

[![Tests](https://github.com/isaiahcampusano/kalshi-mispricing-arbitrage-engine/actions/workflows/tests.yml/badge.svg)](https://github.com/isaiahcampusano/kalshi-mispricing-arbitrage-engine/actions/workflows/tests.yml)

An incremental, test-first project for studying structural price inconsistencies on Kalshi. It includes the original Phase 0 authenticated request plus a local, asynchronous engine for REST ingestion, WebSocket order books and trades, SQLite persistence, structural detection, collateral sizing, and simulated execution.

No live-money execution is implemented. Order payload construction remains simulation-only even if `DRY_RUN=false`.

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
6. Log structured JSON opportunities and simulated order legs.

Relevant settings:

| Variable | Default | Purpose |
|---|---:|---|
| `KALSHI_WS_URL` | demo WebSocket v2 URL | Streaming endpoint |
| `MAX_COLLATERAL_CENTS` | `1000000` | Maximum simulated collateral |
| `DRY_RUN` | `true` | Log only; no execution attempt |
| `KALSHI_DATABASE_PATH` | `kalshi_arbitrage.db` | Local SQLite database |
| `KALSHI_SCAN_INTERVAL_SECONDS` | `2` | Detector interval |
| `KALSHI_MARKET_LIMIT` | `1000` | Maximum markets in the initial sync |

## Important specification corrections

The supplied strict specification is retained in [ARBITRAGE_ENGINE_SPEC.md](ARBITRAGE_ENGINE_SPEC.md), but several stale assumptions were corrected in code against Kalshi's current official API contracts:

- The demo WebSocket is `wss://external-api-ws.demo.kalshi.co/trade-api/ws/v2`, authenticated with signed headers during the handshake.
- Subscription commands use `cmd`, `params.channels`, and `market_tickers`.
- YES and NO are complementary sides of one Kalshi market, not separate `-Y` and `-N` tickers.
- Same-market arbitrage therefore uses `YES ask + NO ask < 100` or `YES bid + NO bid > 100`.
- Mutually exclusive sums are scoped to a single event; markets from different dates in a series are never added together.
- Current fixed-point dollar fields are converted once to integer cents for detector math.
