# Paper Trading Plan — cmpsno/kalshi-mispricing-arbitrage-engine

Phase 2 of `COPILOT_INSTRUCTIONS.md`. Phase 1 audit: `PAPER_TRADING_AUDIT.md`
(verdict: no paper trading exists today; no return can be calculated).
This document defines the measurement model only. **No implementation code
was written in this phase** — only these two docs were added to the repo
working copy, uncommitted, for the owner to sync.

Status: plan only. Nothing here is live, and no figure in this document is a
reported return. All dollar amounts in examples are illustrations of the
formulas, not results.

**Update 2026-11-05 — Phase 5 implemented.** Dashboard section "Paper
Trading Performance" added (`dashboard.py`, data via
`src/dashboard_data.py:load_paper_summary()`): initial capital, cash,
reserved collateral, equity, realized/unrealized/net P&L, ROI (net and
realized-only), simulated orders (filled/unfilled), capital deployed,
detector opportunities shown separately, current-week return
(Mon–Sun America/New_York), equity curve, open positions, recent fills,
ledger history, last-update time. Stale MTM withholds equity with a
warning; no paper account shows an explanatory empty state, never zeros.
Sample opportunity figures are never mixed into paper results. README
gained the "Paper trading (local simulation)" section plus `PAPER_*`
config rows. Verified with Streamlit `AppTest` (section renders, metrics
correct) and the loader tests. Tests: 75 passed, 1 pre-existing failure
(`test_client.py::test_get_markets_rejects_invalid_limit`). All five
phases are now done; the remaining work is gated follow-ups (fee
modelling, settlement) and live paper-mode operation.

**Accounting correction made during Phase 3 (read this).** The first draft
of §2 defined `Equity = Cash + Reserved + Unrealized P&L` with cash as
"uncommitted only". That identity is correct for long positions but wrong
for shorts: a short sale credits proceeds to cash while the position's
entry value is negative, so adding reserved collateral double-counts the
proceeds. The implemented model, now §2 below, is the standard one:
**cash is the actual cash balance** (buys decrease it, sells increase
it), **equity = cash + mark-to-market value of open positions**, and
reserved/margin is reported separately and never added to equity. Long-only
numbers in the worked example (§2.4) are unchanged; short-leg accounting
now verifies exactly (see `tests/test_paper_simulator.py::
test_sell_legs_use_margin_and_short_mtm`).

## 0. Design constraints (carried from Phase 1)

- No real-money execution. No production-host order submission. The existing
  demo-host safeguards (`src/config.py:Settings.__post_init__`, demo hosts
  only when `DRY_RUN=false`) are not loosened.
- A separate **local** simulator + ledger. The demo-host submission path
  (`src/arbitrage/executor.py:execute_opportunity()` live branch →
  `src/client.py:place_orders()`) is not modified or expanded.
- Never call detector potential profit "realized profit". Never call a
  demo-host order a local paper simulation. Never imply a quote guarantees
  a fill. Never report ROI without a defined initial-capital denominator.
- Do not invent fills, prices, fees, balances, or performance figures.

## 1. What each thing is (definitions)

| Term | Definition | Source of truth |
|---|---|---|
| Detector opportunity | A signal from `src/arbitrage/detectors.py` (`same_market_cross`, `mutually_exclusive_sum`, `strike_monotonicity`). Carries `gross_profit_cents` (theoretical edge per contract, before fees) and `required_collateral_cents`. It is **not** a trade. | `opportunities` table, `src/storage.py:save_opportunity()` |
| Simulated order | An order submitted to the **local paper simulator**, never to Kalshi. Records timestamp, market/contract ticker, side (yes/no), direction (buy/sell), limit price, requested quantity, and the detector/opportunity that generated it. | New: `paper_orders` (§5) |
| Simulated fill | Quantity actually filled under the execution model in §4. A fill exists only if the observed order book supported it at that moment. Unfilled quantity is not a trade. | New: `paper_fills` (§5) |
| Open position | A filled position not yet closed by an offsetting trade or settled. Stored per leg — never netted across legs, so multi-leg residual exposure stays visible. | New: `paper_positions` (§5) |
| Cash | Actual paper cash balance. A buy decreases it by the fill cost; a sell increases it by the fill proceeds; settlement pays out / collects $1.00 per contract. | `paper_accounts` + ledger (§5) |
| Reserved collateral | Margin / capital committed, tracked separately and **not added to equity**: `cost_basis` for a long, `quantity × 100 − cost_basis` for a short (the cash that must stay aside to cover a short's $1.00 payout obligation). Buying power for a new order is `cash − short margin`; a long's cost already left cash, so it needs no additional margin. | Ledger events (§3) |
| Realized P&L | P&L on closed/settled positions, net of fees **when fees are known**. Fees are currently unknown (see §2), so v1 realized P&L is labelled **gross of fees**. | Computed from ledger, never stored as a bare number without its basis |
| Unrealized P&L | Mark-to-market estimate for open positions: current exit value minus entry value (§3). Valuation method must be disclosed with every figure. | Computed from latest books (§3) |
| Equity | `Cash + mark-to-market value of open positions`. (Equivalently: cash + reserved cost basis + unrealized only for longs; the general formula is cash + MTM.) | Computed, not stored as an input |
| Net P&L | `Realized P&L + Unrealized P&L`. Every report states which of the two it includes. | Computed |
| ROI | `Net P&L / Initial capital`, as a percentage. A realized-only ROI (`Realized P&L / Initial capital`) is reported alongside it, never instead of it without labelling. | Computed |
| Capital deployed | Currently reserved collateral (§3). Reported separately from P&L; collateral is never subtracted twice. | Computed |
| Fill rate | Two numbers, always both: (a) filled quantity / requested quantity, (b) fully-or-partially-filled orders / submitted orders. | Computed from `paper_orders` + `paper_fills` |
| Win rate | Profitable closed positions / closed positions. Denominator is closed positions only — never opportunities, never open positions. | Computed from ledger |

### Contract and settlement conventions — verified from the repo vs. uncertain

**Verified from the repo's own implementation** (detectors + sizing, all in
integer cents; a binary contract pays out $1.00 = 100¢ to the winning side):

- Same-market cross (`src/arbitrage/detectors.py:detect_same_market_cross`):
  buy YES ask + NO ask for `< 100¢` locks `100 − (yes_ask + no_ask)`; sell YES
  bid + NO bid for `> 100¢` locks `(yes_bid + no_bid) − 100`.
- Mutually-exclusive sum (`detect_series_sum_deviation`): buy all YES asks
  for `< 100¢` locks `100 − sum(asks)` (exactly one outcome pays $1);
  sell all YES bids for `> 100¢` locks `sum(bids) − 100`.
- Strike spread (`detect_strike_monotonicity_violations`): buy lower-strike
  YES ask, sell higher-strike YES bid; edge = `higher.yes_bid −
  lower.yes_ask`; collateral = `lower.yes_ask + (100 − higher.yes_bid)`.
  The `(100 − price)` term is the reserve for a **sold** YES leg: selling YES
  at price P obligates up to $1 if YES wins, so `100 − P` per contract must
  be reserved. Symmetrically, buying costs the price; selling costs
  `100 − price` in reserved collateral. This is the collateral rule for the
  whole model (§3).
- `OrderRequest.to_api_payload()` (`src/models.py`) confirms YES/NO are two
  sides of one market (buy YES / sell NO map to the bid side of the book,
  and vice versa), and that orders are IOC limits.

**Uncertain — I cannot verify these from the current repository/data, and
the plan does not assume them:**

- **Fees.** `REPOSITORY_EVALUATION.md` reports (not implements) that current
  event-contract taker fees use a quadratic expected-earnings formula,
  generally `ceil(M × 0.07 × C × P × (1−P))`, with a separate series
  `fee_multiplier`, separate maker rules, and no settlement fee, and that
  fee math needs finer-than-cent precision. That is a claim in a repo doc,
  not code, and the fee schedule is dated (effective July 7, 2026). **V1
  therefore reports all P&L gross of fees, with `fee_cents = NULL` and an
  explicit "fees not included" label.** No flat bps fee, no invented fee.
  Implementing fees is a separate, gated step: re-verify the current fee
  schedule and series `fee_type`/`fee_multiplier` (`GET /series/{ticker}`),
  implement it in `Decimal`, and only then relabel results net of fees.
- **Settlement outcome source.** The repo never implements settlement: no
  code reads a winning outcome, closes a position at expiry, or books
  settlement P&L (`src/client.py:get_events()` accepts a `settled` status
  filter, but nothing consumes it). The $1-payout rule above is verified
  from detector math; *where the simulator learns which side won* is not.
  **V1 supports closing positions only by offsetting trades.** Settlement
  is added only after verifying the outcome source against current API docs
  (candidate: polling market/event status — to be verified, not assumed).
  Until then, positions in expired markets are reported as open with an
  "unsettled — outcome source unverified" flag, never auto-settled.
- **Price precision.** The engine converts prices to whole cents
  (`src/models.py:dollars_to_cents()`) and truncates fractional quantities
  (`count_to_int()`). Real edges in the sample data are 1–12¢, so sub-cent
  rounding can create or erase an entire edge. V1 keeps integer cents for
  compatibility with the existing models and discloses this as a limitation;
  a precision pass (Decimal / finer units, per `REPOSITORY_EVALUATION.md`
  recommendation 2) is required before claiming cent-accurate economics.

## 2. Accounting model

All money in integer cents in v1 (matching existing models); quantities in
whole contracts in v1 (matching `OrderRequest.quantity: int`); fees `NULL`
until verified (§1).

### 2.1 The identity

```
Equity = Cash + mark-to-market value of open positions
Net P&L = Realized P&L + Unrealized P&L = Equity - Initial capital
ROI = Net P&L / Initial capital
```

Mark-to-market value per leg: long YES `qty × best YES bid`, long NO
`qty × best NO bid`, short YES `−qty × best YES ask`, short NO
`−qty × best NO ask`. Unrealized P&L per leg is that value minus its
entry value (`+cost_basis` for a long, `−cost_basis` for a short, where
cost basis is the total cash paid (long) or received (short)). Reserved
collateral is margin information only: it constrains buying power, it is
never added to equity — adding it would double-count short-sale proceeds
(see the correction note at the top of this document).

### 2.2 Ledger transitions (append-only; every transition is an event, §5)

| Event | Cash | Reserved (buying-power hold / margin) | Realized P&L | Notes |
|---|---|---|---|---|
| Account opened | `+Initial capital` | 0 | 0 | One `DEPOSIT` event. Initial capital is configured, never inferred. |
| Order placed | 0 | `+reserve` | 0 | Reserve per leg: buy → `qty × limit price`; sell → `qty × (100 − limit price)`. This is a buying-power hold only — cash does not move yet. The existing 5% sizing buffer from `src/arbitrage/risk.py:calculate_max_quantity()` is a sizing cap, not a fee allowance. Order rejected for insufficient cash → no movement, `REJECTED` event. |
| Fill (full/partial) | buy: `−fill cost`; sell: `+fill proceeds` | hold released; sell adds short margin `filled × 100 − proceeds` | 0 | Filled qty opens/increases a position at the fill total; unfilled qty (IOC) expires and its hold is released. |
| Order expired / unfilled (IOC) | 0 | hold released | 0 | IOC unfilled quantity never rests. |
| Close by offsetting trade | long: `+exit proceeds`; short: `−exit cost` | short: margin released | `exit proceeds − cost basis` (long) / `cost basis − exit cost` (short), per leg | Close price is a simulated fill under §4, not the mid-price. |
| Settlement (future, gated) | long win: `+payout`; short win: `−payout` | short: margin released | `payout − cost basis` (long) / `cost basis − payout` (short) | Only after the outcome source in §1 is verified. Not in v1. |
| Mark-to-market (reporting only) | — | — | — | No ledger movement. Unrealized P&L is computed at report time from the latest books (see §2.3). |

### 2.3 Mark-to-market (unrealized P&L)

Value each open leg at its **current exit price** from the latest order book
for that ticker:

- Long YES: exit = best YES bid. Long NO: exit = best NO bid.
- Short YES: exit cost = best YES ask. Short NO: exit cost = best NO ask.
- `Unrealized P&L (leg) = qty × (exit price − fill price)` for longs;
  `qty × (fill price − exit price)` for shorts.

Rules: if a leg has no book, or its book is older than
`PAPER_MAX_BOOK_AGE_SECONDS` (§6), its unrealized P&L is reported as
**unknown/stale** and excluded from the headline equity, with the exclusion
stated. Never mark with the mid-price, never mark with the detector's cached
`Market` row (it carries no timestamp — see audit §"Other notes"), and never
carry a position at cost and call it marked.

### 2.4 Worked example (illustration of the formulas, not a result)

Proposed initial capital $1,000.00 = 100,000¢ (to be confirmed, §6).
Same-market cross: YES ask 48¢, NO ask 49¢, edge 3¢/contract, qty 10.
Collateral 970¢. Simulator submits both legs; YES fills 10 @ 48¢, NO fills
only 6 @ 49¢ (partial — the lock is broken, §4.3):

- After fills: Cash = 100,000 − 774 = 99,226¢. Mark-to-market value at
  entry = 774¢ (the positions are worth what was paid for them).
  Equity = 99,226 + 774 = 100,000¢. ✓ (Capital deployed = 774¢, reported
  separately, not added to equity.)
- Later, YES bid 55¢, NO bid 40¢: position exit value = 10×55 + 6×40 = 790¢;
  Unrealized = 790 − 774 = +16¢. Equity = 99,226 + 790 = 100,016¢.
- If YES then wins at settlement (gated, §1): payout = 10×100 + 6×0 =
  1,000¢; Realized = 1,000 − 774 = +226¢. If NO wins instead: payout =
  0 + 6×100 = 600¢; Realized = 600 − 774 = −174¢. The same partial fill is
  +$2.26 or −$1.74 depending on the outcome — that directional exposure is
  exactly why partial fills must be represented per leg, never averaged away.

## 3. Execution model (local simulator)

The simulator consumes **the same market/order-book observations the
detectors use** — `order_books` rows written by
`WebSocketManager._persist_book()` (`src/websocket_manager.py:223`) and
`KalshiClient.get_order_book()` (`src/client.py`) — but it never calls
`place_orders()`.

1. **Snapshot coherence.** All legs of one opportunity are evaluated
   against books from a single observation moment: each leg's book must
   exist and be no older than `PAPER_MAX_BOOK_AGE_SECONDS` at order time.
   If any leg's book is missing or stale, the whole simulated order is
   rejected (`REJECTED`, reason `stale_or_missing_book`) — no partial
   simulation of the other legs.
2. **Depth walk, not top-of-book.** A buy fills only up to displayed size
   at prices ≤ the limit, walking ask levels; a sell fills only up to
   displayed size at prices ≥ the limit, walking bid levels. Fill price is
   the volume-weighted average of the levels actually consumed. If the
   book cannot fill the full quantity at/better than the limit, the order
   partially fills for what the book supports and the remainder expires
   (IOC — the engine's orders are IOC limits, `src/models.py:
   OrderRequest.to_api_payload()`).
3. **Quantity cap.** Requested quantity is capped by (a) available cash /
   collateral via `calculate_max_quantity()` (`src/arbitrage/risk.py`),
   and (b) the minimum executable depth across all legs. Quantity is never
   capped by collateral alone.
4. **No quote = no fill.** A detector signal alone is not a fill. A price
   seen in the cached `Market` row without a backing book is not a fill.
5. **Known limitation (pre-requisite, not assumed away).** The WebSocket
   handler ignores `sid`/`seq` (`REPOSITORY_EVALUATION.md`), so a missed or
   out-of-order delta is undetectable today and books can be stale without
   the engine knowing. Phase 3 must either harden this (sequence-gap
   detection + resnapshot, evaluation recommendation 1) or record the
   book's source timestamp on every fill and label fills from unverified
   books. Do not present fills as verified against books whose integrity
   was never checked.

## 4. Minimal implementation (Phase 3 proposal — not started)

Smallest architecture fitting the current codebase. New package only;
detectors and the demo-host executor are untouched except for one wiring
point in `src/main.py`.

| # | Piece | File(s) (new unless noted) | Notes |
|---|---|---|---|
| 1 | Book-integrity prerequisite | `src/websocket_manager.py` (extend) | Preserve source timestamp + `sid`/`seq` on books; gap → resnapshot; reject stale books. Or, if deferred, record timestamp on every fill and label it. Do this before trusting any fill. |
| 2 | Paper account + config | `src/paper/config.py`, `src/config.py` (extend) | `PAPER_ENABLED` (default `false`), `PAPER_INITIAL_CASH_CENTS` (**required** when enabled — no silent default; proposed value $1,000 = 100,000¢, owner to confirm), `PAPER_MAX_BOOK_AGE_SECONDS` (proposed 5, owner to confirm). `PAPER_ENABLED=true` with a non-demo host is irrelevant (paper never touches the network) but the existing production-host rejection stays in place for the demo path. |
| 3 | Ledger + tables | `src/paper/ledger.py`, `src/storage.py` (extend `SCHEMA`) | Append-only `paper_ledger_events` + `paper_accounts`, `paper_orders`, `paper_fills`, `paper_positions` (§5). Positions are derived from fills/closes, never hand-edited. Restart/reload replays the ledger — no in-memory-only state. |
| 4 | Simulator | `src/paper/simulator.py` | Implements §3 against `OrderBook` snapshots. Pure function over (opportunity, books, account state) → orders/fills/events, so it is unit-testable without network. Never imports `KalshiClient.place_orders`; a test asserts that. |
| 5 | Wiring | `src/main.py` (one branch) | When `PAPER_ENABLED`, detected opportunities go to the simulator instead of the dry-run dict return; when disabled, behaviour is byte-identical to today. Demo-host path unchanged. |
| 6 | Reporting | `src/paper/report.py` | Equity / realized / unrealized / ROI / fill & win rates over a date range, from the ledger only. Weekly report per §7. |
| 7 | Dashboard | `dashboard.py`, `src/dashboard_data.py` (extend) | "Paper Trading Performance" section only when paper data exists; banner states paper mode, initial capital, fees-excluded label, and date range. Sample-data figures are never mixed with paper results. |
| 8 | README | `README.md` (extend) | Local paper simulation vs Kalshi demo exchange; fill model; capital + ROI definitions; fees/slippage status; liquidity + partial-fill handling; date range + sample size; limitations. |

Explicitly out of scope for Phase 3: changing detector logic, changing the
demo-host executor, fee modelling (gated, §1), settlement (gated, §1),
fractional quantities / sub-cent precision (later pass), automatic unwind
of residual exposure (report it; manual review, as today).

## 5. Proposed schema (SQLite, alongside existing tables)

```sql
CREATE TABLE IF NOT EXISTS paper_accounts (
    id INTEGER PRIMARY KEY,
    initial_cash_cents INTEGER NOT NULL CHECK (initial_cash_cents > 0),
    created_at INTEGER NOT NULL,          -- ms epoch UTC
    mode TEXT NOT NULL DEFAULT 'paper'    -- always 'paper'; never 'live'
);
CREATE TABLE IF NOT EXISTS paper_orders (
    id TEXT PRIMARY KEY,                  -- uuid
    opportunity_id TEXT,                  -- FK -> opportunities(id), nullable
    created_at INTEGER NOT NULL,
    ticker TEXT NOT NULL,
    side TEXT NOT NULL CHECK (side IN ('yes','no')),
    direction TEXT NOT NULL CHECK (direction IN ('buy','sell')),
    limit_price_cents INTEGER NOT NULL,
    requested_quantity INTEGER NOT NULL CHECK (requested_quantity > 0),
    status TEXT NOT NULL                  -- placed/filled/partial/expired/rejected
);
CREATE TABLE IF NOT EXISTS paper_fills (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id TEXT NOT NULL,               -- FK -> paper_orders(id)
    filled_at INTEGER NOT NULL,
    fill_price_cents INTEGER NOT NULL,    -- VWAP of levels consumed, cents
    filled_quantity INTEGER NOT NULL,
    book_timestamp INTEGER NOT NULL,      -- timestamp of the book this fill used
    fee_cents INTEGER,                    -- NULL until fees are verified (§1)
    FOREIGN KEY (order_id) REFERENCES paper_orders(id)
);
CREATE TABLE IF NOT EXISTS paper_positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL,
    side TEXT NOT NULL CHECK (side IN ('yes','no')),
    direction TEXT NOT NULL CHECK (direction IN ('buy','sell')),
    quantity INTEGER NOT NULL,            -- open quantity; 0 = closed, row kept
    cost_basis_cents INTEGER NOT NULL,    -- exact total paid (long) or received (short)
    opened_at INTEGER NOT NULL,
    closed_at INTEGER,                    -- NULL while open
    realized_pnl_cents INTEGER,           -- NULL while open; gross of fees in v1
    close_reason TEXT                     -- offset/settled (settled gated, §1)
);
CREATE TABLE IF NOT EXISTS paper_ledger_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts INTEGER NOT NULL,                  -- ms epoch UTC
    event_type TEXT NOT NULL,             -- DEPOSIT/ORDER_PLACED/FILL/ORDER_EXPIRED/
                                          -- REJECTED/CLOSE/SETTLE (gated)
    order_id TEXT,
    ticker TEXT,
    cash_delta_cents INTEGER NOT NULL DEFAULT 0,
    reserved_delta_cents INTEGER NOT NULL DEFAULT 0,
    realized_pnl_cents INTEGER NOT NULL DEFAULT 0,
    details_json TEXT NOT NULL DEFAULT '{}'
);
```

Invariants (enforced by tests, §8): ledger replay reproduces cash, reserved,
positions, and realized P&L exactly; `Equity = Cash + Reserved + Unrealized`
holds at every event boundary; no event deletes or updates an earlier event.

## 6. Configuration decisions needed from the owner

**Confirmed by the owner 2026-11-05 (all five):** initial capital $1,000
(100,000¢); book max age 5 seconds; week Mon–Sun America/New_York; fees
gross-of-fees in v1; close by offsetting trades only. Implemented as such
in `src/paper/config.py` (`PAPER_INITIAL_CASH_CENTS=100000`,
`PAPER_MAX_BOOK_AGE_SECONDS=5`). The original proposals are kept below
for the record.

1. **Initial paper capital** — proposed $1,000 (100,000¢). Any amount is
   fine as long as it is explicit and fixed at account creation; ROI is
   meaningless without it. Confirm the number, or supply a different one.
2. **Book max age** — proposed 5 seconds. Older than this, a leg's book is
   stale and the order is rejected.
3. **Week definition** — proposed Monday 00:00 to Sunday 23:59,
   America/New_York (owner's timezone). Confirm, or pick another boundary.
4. **Fees** — v1 reports gross of fees. Confirm that is acceptable for a
   first paper version, or defer paper trading until fees are verified.
5. **Settlement** — v1 closes only by offsetting trades. Confirm, or wait
   for the settlement-source verification in §1.

## 7. Weekly performance report (answers "What were the returns this week?")

Do not answer with a number until the ledger contains timestamped records
for the period. When it does, the report states, in this order:

1. Reporting timezone and exact week boundaries (e.g. Mon 2026-11-09 00:00
   to Sun 2026-11-15 23:59 America/New_York).
2. Starting equity (at week start, from ledger replay) and ending equity.
3. Net P&L, split into realized and unrealized; ROI = net P&L / initial
   capital, plus realized-only ROI.
4. Simulated orders submitted, filled / partially filled / unfilled, fill
   rates (both definitions, §1), detector opportunities in the same period
   (shown separately — never as trades).
5. Open exposure at week end (positions, reserved collateral, stale/unsettled
   flags).
6. Whether fees are included (v1: no — gross of fees), whether unrealized
   P&L is included in the headline ROI (yes, and the realized-only figure
   is alongside), and whether the week contains live-observation-based
   paper fills or only sample/test data.

If no sufficient records exist for the week, the report says so plainly and
does **not** substitute the dashboard's $9.69 potential-profit figure (see
audit §8: synthetic sample, sum of gross edges).

## 8. Tests (Phase 4, per COPILOT_INSTRUCTIONS.md)

Deterministic fixtures; test fixtures kept separate from live/sample data:

- Starting balance and initial equity; restart/reload from SQLite without
  losing account state (ledger replay).
- Order submission with no fill; full fill; partial fill; rejected order
  (insufficient cash; stale/missing book).
- Multi-leg opportunity where one leg fills and another does not (residual
  exposure represented; §2.4 example as a fixture).
- Collateral reservation and release (place / fill / expire / close).
- Closing a position (offsetting trade) and realized P&L; settlement path
  only after the §1 gate is cleared.
- Unrealized P&L / mark-to-market, including stale-book exclusion.
- ROI calculation (net and realized-only) and weekly date-range filtering.
- No production-host execution path from paper mode: paper simulator never
  calls `place_orders()`; `PAPER_ENABLED` with demo/production hosts does
  not submit anything.

## 9. Dashboard and README (Phase 5 — only after §8 is green)

Dashboard section titled **Paper Trading Performance**, shown only when
paper data exists: initial paper capital, current cash, reserved
collateral, current equity, realized P&L, unrealized P&L, net P&L, ROI (%),
simulated orders, filled / partially filled / unfilled orders, detector
opportunities (separate), capital deployed, period start/end, last data
update — plus an equity curve and ledger history when timestamps support
them. README section per §4 row 8. Sample figures stay labelled sample and
are never combined with paper-account results.

## 10. What this plan deliberately does not decide

- Whether the strategy is profitable — that is what paper trading is for
  measuring, not something this plan asserts.
- Fee values, settlement outcomes, or sub-cent price behaviour — all gated
  on verification (§1).
- Any position sizing beyond the existing `calculate_max_quantity()` plus
  the depth cap in §3. No new strategy, no detector changes.

## 11. Next step

Phases 1–5 are implemented (see the update notes at the top). Remaining:
the gated follow-ups (fee modelling and settlement) from §1, and live
paper-mode operation — no return can be reported until the engine has
actually run in paper mode against live observations and the ledger holds
timestamped fills.
