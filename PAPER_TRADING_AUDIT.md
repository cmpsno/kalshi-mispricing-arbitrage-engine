# Paper Trading Audit — cmpsno/kalshi-mispricing-arbitrage-engine

Audit date: 2026-11-05 (EST). Clone inspected read-only at commit `85fe900`
(`cmpsno/main`). No code was modified, no branch sync/reset/push was performed.

## Branch state (verified)

- `cmpsno/main` HEAD: `85fe900` "Restyle dashboard to Kalshi brand, add demo dataset"
- Upstream `isaiahcampusano/main` HEAD: `9d9e12e` "Add project handoff guide"
- `git rev-list --left-right --count HEAD...upstream/main`: **3 ahead, 0 behind** — preserved.
- The 3 commits ahead:
  - `ba02f5f` Update pyproject.toml (adds blank line + `[tool.poetry] package-mode = false`)
  - `8de1a8b` Restyle dashboard to Kalshi brand, add demo dataset (`dashboard.py` restyle + committed `kalshi_arbitrage.db`, 69,632 bytes)
  - `85fe900` Restyle dashboard to Kalshi brand, add demo dataset (`.streamlit/config.toml`, 7 lines)
- Working tree clean. Audit was done on a throwaway clone in `/tmp`; nothing was written to the repo.

## Short answer

**No — the repo does not support genuine paper trading today, and no defensible
hypothetical return / ROI can be calculated from it.**

What it does have: live-market ingestion (REST + WebSocket), three structural
detectors, SQLite persistence of *opportunities*, a demo-host order path, and a
dashboard that sums theoretical gross edge. What it does not have: a paper
account, cash/capital, a position ledger, fills simulated locally against
liquidity, mark-to-market, settlement, fees, or any realized/unrealized P&L or
account ROI calculation.

The dashboard's ~239 opportunities / ~$9.69 figure is **sample data, not
trading results** (see §8). It must not be quoted as a return.

## 1. Separate paper-trading portfolio/account, or only demo-host orders?

**Only demo-host orders. No paper account exists.**

- No file, class, table, config key, or env var for a paper account, portfolio,
  cash balance, or starting capital. `grep -ri "paper|balance|capital|equity|
  realized|unrealized"` over `src/`, `dashboard.py`, `*.md` returns no account
  code — only the dashboard's per-opportunity `roi_percent` (see §6) and fee
  strings passed through from API responses.
- `src/config.py` — `Settings` (`from_env()`): `api_key_id`, `private_key_path`,
  `base_url` (default `DEMO_BASE_URL` = `https://external-api.demo.kalshi.co/trade-api/v2`),
  `ws_url` (default demo `wss://external-api-ws.demo.kalshi.co/trade-api/ws/v2`),
  `max_collateral_cents` (default 1,000,000), `dry_run` (default `True`),
  `database_path`, `scan_interval_seconds` (2.0), `market_limit`. No
  `PAPER_*` / initial-capital setting.
- `src/config.py:__post_init__` — the only safety gate: `DRY_RUN=false` is
  rejected unless both hosts are the Kalshi demo hosts. Production hosts are
  rejected; real-money execution is not implemented anywhere.
- `src/main.py:run_engine()` — the loop is: `initial_rest_sync()` → WebSocket
  subscribe → every 2 s `scan_all_opportunities(db)` → `calculate_max_quantity()`
  → `execute_opportunity(..., dry_run=settings.dry_run, client=client, db=db)`.
  There is no paper-account step in the loop.

## 2. Are detected opportunities persisted? Are submitted orders persisted?

**Opportunities: yes. Submitted orders: only as one aggregate JSON blob, and
only on the live (non-dry-run) path.**

- `src/storage.py:SCHEMA` — tables: `events`, `markets`, `order_books`,
  `trades`, `opportunities`, `execution_attempts`. No `orders`, `fills`,
  `positions`, `accounts`, `ledger`, or `balances` table.
- Opportunities: `Database.save_opportunity()` (`src/storage.py`, `INSERT OR
  IGNORE INTO opportunities ... executed=0`) called for every detection by
  `scan_all_opportunities()` in `src/arbitrage/detectors.py`. Fields:
  `id, detected_at, algorithm, markets_involved, action, gross_profit_cents,
  required_collateral_cents, confidence_score, details_json, executed`.
  `get_recent_opportunities()` reads them back. `details_json` carries the
  execution legs (`legs: [{ticker, side, price}]`).
- Orders: there is no per-order row. `Database.save_execution_attempt()`
  (`src/storage.py`) inserts one row into `execution_attempts` with
  `(opportunity_id, attempted_at, status, results_json)` where `status` is one
  of `{"filled", "partial", "rejected"}` (validated) and `results_json` is the
  serialized list of `OrderExecutionResult` dicts. That is the only order
  record, and it is written **only** by the live path (see §9).

## 3. Are acks, fills, partial fills, cancellations, rejections persisted per leg?

**Partially — per-leg fill data exists only inside the aggregate JSON blob, on
the live path only. No cancellations table, no order lifecycle.**

- `src/client.py:place_orders()` posts to `/portfolio/events/orders/batched`
  and parses each leg: `fill_count`/`remaining_count` →
  `OrderExecutionResult` (`src/models.py`) with `client_order_id, order_id,
  ticker, requested_quantity, filled_quantity, remaining_quantity,
  average_fill_price, fees` (raw `average_fee_paid` string from Kalshi),
  `status` (`filled` if fully filled, `rejected` if an `error` object is
  present, else `partial`), `error_code, error_message`.
- `src/arbitrage/executor.py:execute_opportunity()` aggregates legs:
  `filled` only if every leg is `filled`; `rejected` only if every leg is
  `rejected`; otherwise `partial`. It persists that aggregate via
  `save_execution_attempt()` and calls `mark_opportunity_executed()` only on
  a full fill. On a partial/rejected batch it logs
  `arbitrage_execution_exposure` ("manual review required") and does **not**
  unwind residual exposure.
- Gaps: no order-acknowledgement timestamp, no cancellation records, no
  per-leg table (querying one leg means parsing `results_json`), no
  settlement/close records, and — critically — the **dry-run path persists
  nothing at all** (see §9).

## 4. Cash balance, starting capital, collateral reservation/release, position ledger?

**None exist.**

- No balance/capital/ledger code or schema. The only collateral concepts:
  - `ArbitrageOpportunity.required_collateral_cents` — a per-opportunity,
    per-contract theoretical figure computed by the detectors
    (`src/arbitrage/detectors.py`, e.g. same-market cross: `ask_total`;
    strike spread: `lower.yes_ask + (100 - higher.yes_bid)`).
  - `src/arbitrage/risk.py:calculate_max_quantity()` — sizes by
    `max_collateral_cents // ceil(required_collateral_cents * 1.05)`.
    `MAX_LIVE_COLLATERAL_CENTS = 1_000` ($10) caps live demo submissions.
  - `Settings.max_collateral_cents` (default $10,000) is a sizing cap, not an
    account balance — it is never debited, reserved, or released.
- Nothing tracks cash in/out, reserves collateral when an order is placed,
  releases it on fill/reject/settle, or records an open position. A position
  opened by a filled demo leg has no local representation after
  `execution_attempts` is written.

## 5. Mark-to-market process for open positions?

**No.** There is no position to mark, no valuation function, and no
mark-to-market code. Market prices are persisted (`markets` table,
`yes_bid/yes_ask/no_bid/no_ask/last_price`, updated by REST sync and by
`WebSocketManager._persist_book()`), but nothing values an open position
against them.

## 6. Realized P&L, unrealized P&L, fees, equity, ROI calculated?

**No account-level P&L, equity, or ROI is calculated anywhere. Two
look-alikes exist and must not be confused with them:**

- (a) **Per-opportunity theoretical ROI** — `src/dashboard_data.py:
  load_opportunities()` computes `roi_percent = gross_profit_cents /
  required_collateral_cents * 100` per opportunity, averaged by detector in
  `summarize_opportunities()`. This is gross edge ÷ per-trade collateral for
  one contract, before fees, assuming every leg fills at the quoted price.
  It is not account ROI: there is no capital denominator, no fills, no fees.
- (b) **Gross profit** — `gross_profit_cents` is the detector's theoretical
  edge (e.g. `100 - (yes_ask + no_ask)` for a same-market cross), per
  contract, before fees. `REPOSITORY_EVALUATION.md` (evaluated commit
  `9774af2`) already flags this: detectors emit any positive *gross* edge,
  `Settings` has no fee/spread/quote-age settings, and the fee schedule is
  not implemented (Kalshi taker fees are a quadratic expected-earnings
  formula, not a flat bps rate; whole-cent representation is too coarse).
- Fees: only the pass-through string `OrderExecutionResult.fees` from the
  Kalshi batch response (`src/client.py:place_orders()`). Fees are never
  summed, never subtracted from profit, and never modelled in detectors,
  sizing, or the dashboard ("before fees" is stated in `dashboard.py`).

## 7. Can a weekly performance report be reconstructed from persisted records?

**No.** The committed database contains no trade/fill/position/ledger rows at
all (see §8). Even a live engine database would contain only:
`opportunities` (signals with `detected_at`) + `execution_attempts`
(aggregate attempt rows with `attempted_at` + per-leg JSON). From those you
could count signals and demo attempts in a week, but you could not compute
starting/ending equity, realized or unrealized P&L, ROI, fill rate against a
capital base, or open exposure — because there is no cash, no position
lifecycle, no settlement, and no fee accounting. "What were the returns this
week?" is currently unanswerable from the repo, by design of the missing
pieces, not by a missing query.

## 8. Where do ~239 opportunities / $9.69 come from?

**Static sample data generated by a script — not live detections, not
simulated executions, not real trades.** Verified against the committed
`kalshi_arbitrage.db`:

- The committed DB contains **only one table** (`opportunities`); the
  engine's other tables (`events, markets, order_books, trades,
  execution_attempts`) do not exist in it, so it was never written by
  `src/storage.py:Database.initialize()`.
- **240 rows, $9.73 total** (`SUM(gross_profit_cents) = 973`), by detector:
  same-market cross 90 rows / 372¢, mutually-exclusive sum 84 / 320¢, strike
  monotonicity 66 / 281¢. Timestamps span 2026-09-20 00:49 UTC to 2026-09-21
  00:43 UTC.
- **Every row** has `details_json = {"sample": true, "source":
  "demo-generator"}`. The generator is `~/workspace/kalshi-dashboard/
  make_sample_db.py` (`random.seed(20260920)`, 240 rows, profit drawn from
  `[1,1,2,2,2,3,3,4,5,6,8,12]` cents, collateral 82–120¢, `executed` set to 1
  with probability 0.15).
- The `executed` flag (37 of 240 = 1) is **randomly assigned by the
  generator**; there is no `execution_attempts` table to back any of those
  37 flags. They are not evidence of 37 trades.
- The dashboard's "239 / $9.69" is the same dataset through its default
  24-hour window (`dashboard.py`, `hours_back` default 24;
  `src/dashboard_data.py:load_opportunities()` filters
  `detected_at >= now - 24h`): at the time it was observed (Sep 21), the
  oldest row (4¢, 2026-09-20 00:49 UTC) had aged out of the window —
  240 − 1 = 239 rows, 973¢ − 4¢ = 969¢ = **$9.69**. The dashboard does label
  this with a "Demo dataset" banner, and its "Potential gross profit" metric
  is defined as profit *if every opportunity were executed at the quoted
  prices, before fees*.
- Conclusion: **I cannot verify any realized return from this data, and the
  $9.69 is not a return.** It is the sum of synthetic per-contract gross
  edges, assuming 100% fill at quoted prices, zero fees, and no capital base.

## 9. What does the current demo execution path actually do?

**Two distinct paths — a local dry-run that simulates nothing, and a real
submission to Kalshi's demo host. Neither is local paper trading.**

`src/arbitrage/executor.py:execute_opportunity(opp, quantity, dry_run=True)`:

- **Dry-run (default, `DRY_RUN=true`):** builds `OrderRequest` legs from
  `opp.details["legs"]` (validates ticker/side/price), logs the opportunity,
  and returns `[{"simulated": True, "dry_run": True, "status": "dry_run",
  "leg": leg}, ...]` per leg. It performs **no fill simulation** (no
  order-book liquidity check, no price, no quantity filled, no fees), makes
  **no network call**, and writes **nothing to the database** — no order, no
  attempt, no position. The word "simulated" in the return dict is a label
  only. `src/main.py` runs this path on every detected opportunity (sized by
  `calculate_max_quantity()`), so in default mode the engine logs signals
  and returns dry-run dicts that are then discarded.
- **Live demo (`DRY_RUN=false`, demo hosts only):** checks collateral ≤
  $10 (`MAX_LIVE_COLLATERAL_CENTS`), requires a `KalshiClient`, and calls
  `client.place_orders(orders)` → `POST /portfolio/events/orders/batched`
  with IOC limit orders (`time_in_force: immediate_or_cancel`,
  `src/models.py:OrderRequest.to_api_payload()`). Batch submission is **not
  atomic**: one leg may fill while another is rejected/partial. Results are
  persisted as one aggregate `execution_attempts` row (see §2–§3); full fill
  marks the opportunity executed; otherwise an exposure error is logged for
  manual review with no automatic unwind.
- Pre-execution gaps (already noted in `REPOSITORY_EVALUATION.md`): legs are
  built from cached detector prices with no fresh order-book revalidation,
  no quote-age/depth check, and no fee gate before submission.

## Other audit notes

- **Detectors (verified, `src/arbitrage/detectors.py`):** `detect_same_market_cross`
  (YES ask + NO ask < 100, or YES bid + NO bid > 100), `detect_series_sum_deviation`
  (per-event, mutually-exclusive only, sum of YES asks < 100 / bids > 100),
  `detect_strike_monotonicity_violations` (higher-strike YES bid > lower-strike
  YES ask, pairwise adjacent strikes). All pure integer-cent, gross-edge only,
  confidence = `min(1, edge_cents / 10)`. `scan_all_opportunities()` saves each
  detection via `save_opportunity()` (INSERT OR IGNORE, so re-scans dedupe by id).
- **Ingestion (verified):** `src/client.py` (REST: `get_events`, `get_markets`,
  `get_order_book`, `get_trades`, `place_orders`; RSA-PSS auth in `src/auth.py`),
  `src/websocket_manager.py` (order-book snapshots/deltas + trades → `order_books`
  / `markets` rows). `REPOSITORY_EVALUATION.md` notes the WebSocket handler
  ignores `sid`/`seq`, so missed/out-of-order deltas are undetectable and
  detectors can scan stale prices — relevant to any future fill simulation,
  which must not trust undated quotes.
- **Tests:** 12 test files exist, including `tests/test_executor.py`
  (dry-run logging, full-fill marking, partial-batch non-marking, invalid-input
  rejection) and `tests/test_storage.py` (round-trips, scanner persistence).
  Test suite was **not executed** in this audit (no `pytest` in the system
  Python in this environment); no test covers paper accounting because none
  exists. `tests/test_dashboard_data.py` asserts the per-opportunity
  `roi_percent` formula only.
- **Docs vs code:** `README.md`, `PROJECT_PLAN.md` (Phase 6 "Paper execution"
  is a roadmap item, not implemented), `ARBITRAGE_ENGINE_SPEC.md`, `HANDOFF.md`,
  and `REPOSITORY_EVALUATION.md` are consistent with the code on the points
  above; the evaluation doc's "not implemented" verdict on fee/slippage/
  validation work still holds at `85fe900`.
- **What the 3 ahead commits changed:** dashboard styling + sample DB +
  `[tool.poetry] package-mode = false`. None touches detection, execution,
  storage, or accounting. The paper-trading gap is inherited from upstream,
  not introduced by the fork.

## Verdict for the goal

| Question | Answer |
|---|---|
| Genuine paper trading supported? | **No** |
| Separate paper account / starting capital? | No — nothing exists |
| Simulated fills against liquidity? | No — dry-run returns a label; live path submits to demo host |
| Cash / collateral ledger / positions? | No |
| Mark-to-market / settlement? | No |
| Realized / unrealized P&L, equity, account ROI? | No — only per-opportunity gross edge ÷ collateral |
| Weekly return reconstructable? | **No — I cannot verify this from the current repository/data** |
| 239 / $9.69 = return? | **No** — synthetic sample, sum of gross edges, $9.73 in full; $9.69 is the 24h-window slice |

## Recommended next step (Phase 2, not started)

Per `COPILOT_INSTRUCTIONS.md` Phase 2–3: define the accounting model first
(opportunity ≠ order ≠ fill ≠ position; cash vs reserved collateral vs equity;
ROI = net P&L ÷ explicitly configured initial capital), then propose the
smallest safe implementation — a **separate local simulator + SQLite ledger**,
isolated from the demo-host submission path, with fills gated on observed
order-book liquidity, partial fills and multi-leg exposure represented, and
fees either verified or explicitly labelled unknown/not included.

No code changes were made in this phase. Awaiting the owner's go-ahead before
writing `PAPER_TRADING_PLAN.md` or any implementation.
