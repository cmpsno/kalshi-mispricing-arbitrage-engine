# Kalshi Mispricing Arbitrage Engine

[![Tests](https://github.com/isaiahcampusano/kalshi-mispricing-arbitrage-engine/actions/workflows/tests.yml/badge.svg)](https://github.com/isaiahcampusano/kalshi-mispricing-arbitrage-engine/actions/workflows/tests.yml)

An incremental, test-first project for studying structural price inconsistencies on Kalshi. The current implementation is **Phase 0 only**: a small authenticated REST client that signs and performs `GET /markets` against Kalshi's demo environment.

No order placement or live-money execution is implemented.

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
