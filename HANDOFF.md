# Project Handoff

Last updated: August 27, 2026

## Current status

The Kalshi arbitrage engine and Streamlit dashboard code are implemented on the
`main` branch. The dashboard implementation was pushed in commit `e75adf5`
(`Add Streamlit arbitrage dashboard`). The full test suite passed with 49 tests.

Work is intentionally paused before API configuration because Kalshi demo
credentials are not available yet.

The following local-only files do not currently exist:

- `.env`
- `kalshi_arbitrage.db`
- `.venv`

This is expected. The engine creates the SQLite database after it starts, and
the `.env` file must not be created until valid demo credentials are available.

## What was completed

- REST and WebSocket Kalshi demo clients
- SQLite persistence
- Arbitrage detectors and dry-run execution flow
- Streamlit dashboard in `dashboard.py`
- Read-only dashboard queries in `src/dashboard_data.py`
- Automatic dashboard refresh, metrics, charts, detector summaries, and tables
- Tests for dashboard queries and missing-database handling
- Dashboard dependencies and usage documentation

## Resume checklist

### 1. Update the local checkout

```powershell
git switch main
git pull origin main
```

### 2. Create and activate a virtual environment

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
```

### 3. Add Kalshi demo credentials

Obtain an API key ID and its matching private PEM key from the Kalshi demo
environment. Keep the PEM file outside this repository and use an absolute path
to it.

```powershell
Copy-Item .env.example .env
```

Edit only these placeholder values in `.env`:

```dotenv
KALSHI_API_KEY_ID=your-actual-demo-api-key-id
KALSHI_PRIVATE_KEY_PATH=C:/absolute/path/outside/repo/kalshi-demo-private-key.pem
```

Leave the demo REST and WebSocket URLs unchanged. Keep this safety setting while
validating the system:

```dotenv
DRY_RUN=true
```

Never commit `.env`, a PEM/private-key file, or the SQLite database. These are
already covered by `.gitignore`.

### 4. Verify authentication

```powershell
python -m kalshi_client --status open --limit 5
```

Do not continue until this returns demo market data without an authentication
error.

### 5. Start the engine

In the first activated terminal:

```powershell
python -m src.cli run-engine
```

Keep this terminal running. It should create `kalshi_arbitrage.db`, ingest demo
market data, and write detected opportunities to the database.

### 6. Start the dashboard

In a second terminal, activate the same virtual environment and run:

```powershell
.venv\Scripts\Activate.ps1
streamlit run dashboard.py
```

Open `http://localhost:8501`. The dashboard reads the database without modifying
it and refreshes automatically.

### 7. Run tests after any changes

```powershell
python -m pytest
```

The last known result was 49 passing tests.

## Expected behavior before credentials are added

The dashboard can be opened without credentials, but it will display a message
that the database has not been created. That is not an error. Live charts and
opportunities appear only after the authenticated engine is running and has
written data.

## Deployment note

Pushing the repository does not make the dashboard a live website. GitHub Pages
cannot run this Streamlit application because Pages hosts static files and the
dashboard requires a Python server.

For now, run the engine and dashboard locally in two terminals. A future public
deployment must provide all of the following:

- A continuously running engine process
- Securely configured Kalshi demo credentials
- Persistent database storage shared with the dashboard
- A continuously running Streamlit process

Deploying only `dashboard.py` to Streamlit Community Cloud will not provide live
data unless the hosted dashboard can access a database populated by the engine.

## Useful files

- `README.md` — installation, configuration, and operating instructions
- `.env.example` — safe configuration template
- `dashboard.py` — Streamlit user interface
- `src/dashboard_data.py` — read-only dashboard database queries
- `src/storage.py` — engine database schema and persistence
- `tests/test_dashboard_data.py` — dashboard data tests

## Immediate next action

Obtain Kalshi **demo** API credentials. Then resume at step 1 of the checklist
above and keep `DRY_RUN=true` throughout initial validation.
