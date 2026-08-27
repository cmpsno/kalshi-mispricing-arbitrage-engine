# Evaluation: Kalshi Mispricing Arbitrage Engine

Evaluated repository: <https://github.com/isaiahcampusano/kalshi-mispricing-arbitrage-engine>

Evaluated commit: [`9774af2`](https://github.com/isaiahcampusano/kalshi-mispricing-arbitrage-engine/commit/9774af2866f5f9dcbdd7efe29e2fee21f7b3e02b) (2026-08-26)

Rubric: attached “Implementation Plan: Fee Modeling, Spread Cost, and Price Validation”

## Executive verdict

The repository is a competent demo/research foundation, but it **does not implement the three critical additions in the supplied plan**. It detects and ranks gross theoretical edges, submits the cached leg prices, and has no fee-aware or slippage-aware execution gate. It should not be described as economically realistic, even for evaluating demo results.

Strict plan coverage: **1 foundational item present, 7 required items missing or incomplete**. The existing item is the REST order-book client method; it is not wired into execution-time revalidation.

The detector does consume prices derived from live WebSocket books: `_persist_book()` updates the corresponding market row after each snapshot or delta. However, the detector-facing `Market` model carries neither the source timestamp nor the WebSocket sequence. If the feed freezes or a sequence gap occurs, the engine has no freshness or integrity gate and can continue scanning the last stored prices.

## Plan compliance

| Requirement | Status | Evidence |
|---|---|---|
| Fee/slippage/quote-age configuration | Missing | [`Settings`](https://github.com/isaiahcampusano/kalshi-mispricing-arbitrage-engine/blob/9774af2866f5f9dcbdd7efe29e2fee21f7b3e02b/src/config.py#L26-L37) has no fee, spread, or quote-age settings; `from_env()` does not parse them. |
| Opportunity fee audit fields | Missing | [`ArbitrageOpportunity`](https://github.com/isaiahcampusano/kalshi-mispricing-arbitrage-engine/blob/9774af2866f5f9dcbdd7efe29e2fee21f7b3e02b/src/models.py#L201-L210) stores only gross profit, collateral, confidence, and details. |
| Fee calculation and net-positive filtering | Missing | Opportunity creation assigns only `gross_profit_cents`; all detectors emit any positive gross edge. See [`_opportunity()`](https://github.com/isaiahcampusano/kalshi-mispricing-arbitrage-engine/blob/9774af2866f5f9dcbdd7efe29e2fee21f7b3e02b/src/arbitrage/detectors.py#L32-L51) and [`scan_all_opportunities()`](https://github.com/isaiahcampusano/kalshi-mispricing-arbitrage-engine/blob/9774af2866f5f9dcbdd7efe29e2fee21f7b3e02b/src/arbitrage/detectors.py#L247-L264). |
| Spread/slippage penalty | Missing | No spread/slippage calculation exists in source or tests. |
| Current-order-book REST method | Present as foundation | [`get_order_book()`](https://github.com/isaiahcampusano/kalshi-mispricing-arbitrage-engine/blob/9774af2866f5f9dcbdd7efe29e2fee21f7b3e02b/src/client.py#L161-L188) uses the current official endpoint and adapts YES/NO bids into a YES-side book. |
| Pre-execution price/depth revalidation | Missing | [`execute_opportunity()`](https://github.com/isaiahcampusano/kalshi-mispricing-arbitrage-engine/blob/9774af2866f5f9dcbdd7efe29e2fee21f7b3e02b/src/arbitrage/executor.py#L18-L88) converts cached legs directly to orders, then calls `place_orders()`. It does not fetch books, validate quote age, recalculate edge, or check available size. |
| Orchestration through a validation gate | Missing | The main loop sizes every detected gross opportunity and immediately invokes the executor. See [`run_engine()`](https://github.com/isaiahcampusano/kalshi-mispricing-arbitrage-engine/blob/9774af2866f5f9dcbdd7efe29e2fee21f7b3e02b/src/main.py#L63-L88). |
| Tests requested by the plan | Missing | Detector tests assert gross edge only; executor doubles do not implement or assert fresh-book reads. See [`test_detectors.py`](https://github.com/isaiahcampusano/kalshi-mispricing-arbitrage-engine/blob/9774af2866f5f9dcbdd7efe29e2fee21f7b3e02b/tests/test_detectors.py) and [`test_executor.py`](https://github.com/isaiahcampusano/kalshi-mispricing-arbitrage-engine/blob/9774af2866f5f9dcbdd7efe29e2fee21f7b3e02b/tests/test_executor.py). |

## Findings, ordered by severity

### P0 — Detector inputs lose freshness and sequence integrity

WebSocket snapshots and deltas are persisted as books, then [`_persist_book()`](https://github.com/isaiahcampusano/kalshi-mispricing-arbitrage-engine/blob/9774af2866f5f9dcbdd7efe29e2fee21f7b3e02b/src/websocket_manager.py#L223-L237) derives best YES/NO prices and updates the `markets` row used by `scan_all_opportunities()`. Therefore the data path is live under normal message delivery.

The remaining defect is that `handle_message()` ignores top-level `sid` and `seq`, so it cannot detect missed or out-of-order deltas. The detector reads `Market` rows that have no quote timestamp, and the main loop has no maximum-age check. After a feed stall, disconnect, or corrupted reconstruction, opportunities can still be emitted from the last stored state. Official order-book messages expose both `sid` and `seq`, and subscriptions can request a replacement snapshot. [Official WebSocket order-book reference](https://docs.kalshi.com/websockets/orderbook-updates)

### P0 — Fixed-point market data is collapsed to whole cents/contracts

The current API supports prices down to `$0.0001` and quantities down to `0.01` contracts. The repository converts prices to whole cents with `dollars_to_cents()` and quantities with `int(Decimal(...))`. This rounds subcent prices and truncates fractional sizes and deltas; for example, a `-0.50` contract delta becomes zero and is not applied.

Impact: reconstructed books and calculated edges can be wrong even when every WebSocket message arrives. In a strategy targeting small discrepancies, rounding individual legs to whole cents can create or erase the entire apparent profit. [Official fixed-point representation](https://docs.kalshi.com/getting_started/fixed_point_migration)

### P0 — Fees are absent from both signal generation and sizing

All three detectors report gross per-contract edge. The engine ranks on gross edge, sizes on gross collateral, and submits without calculating fees. This creates false positives and can turn apparently profitable executions into expected losses.

The five-percent collateral uplift in `risk.py` is only a sizing buffer; it does not reduce opportunity profit or filter unprofitable signals.

### P0 — Execution has no fresh-price, quote-age, or depth gate

Order prices come directly from `opp.details["legs"]`. The executor neither re-fetches books nor verifies that each price level can fill the chosen quantity. Quantity is capped by collateral alone, not by the minimum executable depth across legs.

Impact: stale or shallow quotes can produce a partially filled batch and directional exposure. The repository correctly records partial batches and emits an exposure error, but remediation is manual and occurs after risk has materialized.

### P1 — The attached fee implementation is not correct for Kalshi event contracts

The plan should not be implemented literally:

- Current event-contract taker fees use a quadratic expected-earnings formula, generally `ceil(M × 0.07 × C × P × (1-P))`, not a flat basis-point percentage of price notional. Maker fees use a separate `0.0175` coefficient and are series-dependent. The current official fee schedule also states there is no settlement fee. [Kalshi fee schedule (effective July 7, 2026)](https://kalshi.com/docs/kalshi-fee-schedule.pdf)
- The plan’s `// 10_000` floors fees. Flooring underestimates costs; it is not conservative.
- Its own 50-cent/100-bps example produces zero cents per leg under integer division, not 0.5 cents. The repository’s whole-cent money representation is too coarse for exact fee accounting because current API and fee rules use finer fixed-point values.
- Fee multipliers vary by series. The current `Event`/`Market` models do not ingest series fee metadata.

### P1 — The plan’s proposed REST route is stale

The attached plan proposes `/v2/markets/tickers/{ticker}/book`. The repository’s existing `/markets/{ticker}/orderbook` route is the correct current path, and the response contains YES bids and NO bids rather than explicit asks. [Official order-book reference](https://docs.kalshi.com/api-reference/market/get-market-orderbook)

For multi-leg validation, the current API also provides a multi-market order-book endpoint for up to 100 tickers, which reduces validation latency. [Official multiple-order-books reference](https://docs.kalshi.com/api-reference/market/get-multiple-market-orderbooks)

### P1 — A flat BPS “spread penalty” is ineffective at this precision

Top-of-book bid/ask prices already represent executable limits. The remaining risks are adverse movement, insufficient displayed size, and multi-leg race exposure. A five-basis-point penalty applied to a roughly 100-cent notional and then floored to whole cents is zero, so the supplied default adds no protection.

A better model walks book depth for the intended quantity and optionally adds a configurable tick/cents-per-leg buffer. The final order price should remain an explicit worst acceptable limit.

## Audit of the deep-dive blueprint supplied afterward

The second attachment reaches the correct high-level verdict, but its sample implementation should not be applied verbatim.

| Blueprint claim or design | Assessment | Required correction |
|---|---|---|
| WebSocket data is stored separately and never reaches detection | Incorrect | `_persist_book()` updates best prices in the market row used by detectors. The actual missing controls are sequence-gap detection, resnapshot, and maximum quote age. |
| Fee multiplier is typically `0.07` and varies by category | Conflates two values | `0.07` is the general taker coefficient. The series `fee_multiplier` is a separate multiplier; a live `GET /series/KXHIGHNY` response returned `fee_multiplier: 1`. Read current `fee_type`/`fee_multiplier` from `GET /series/{ticker}` and use `GET /series/fee_changes` for scheduled changes. [Get Series](https://docs.kalshi.com/api-reference/market/get-series), [Get Series Fee Changes](https://docs.kalshi.com/api-reference/exchange/get-series-fee-changes) |
| Fees round to the nearest `$0.01` per contract | Incorrect for the current API | Current fees use finer precision and order-level accumulation. Official documentation states prices support four decimals, quantities two decimals, fee math can require six decimals, and fee rounding depends on whether the member is direct or non-direct. [Fixed-point representation](https://docs.kalshi.com/getting_started/fixed_point_migration), [fee rounding](https://docs.kalshi.com/getting_started/fee_rounding) |
| The supplied floating-point calculator is production-ready | Incorrect | It uses binary floats, loses subpenny prices/fractional quantities, collapses fee components to whole cents, and does not implement current rounding fees/rebates. Use scaled integers or `Decimal` with explicit units and conservative pre-trade policy. |
| Maker fee is always one quarter of taker fee | Overgeneralized | Maker fees apply only to applicable fee types/series and only when an order rests before filling. The engine’s IOC arbitrage legs are taker executions; labeling some as maker without changing execution semantics underestimates cost. |
| “Spread cost” is ignored | Imprecise | Detectors already use executable asks for buys and bids for sells, so the quoted spread is already embedded in gross edge. What is missing is depth-aware VWAP, adverse-movement allowance, and multi-leg race risk. |
| Five-BPS spread penalty is adequate | Ineffective here | With whole-cent accounting, five BPS of an approximately one-dollar notional floors to zero. Use book-depth execution cost plus a tick-based buffer expressed at the market’s actual price-grid precision. |
| REST validation plus per-leg liquidity checks is sufficient | Incomplete | Validation must map Kalshi’s YES/NO bid representation correctly, validate all legs against one coherent snapshot, check sequence/freshness/depth, and acknowledge that state can still change between validation and a non-atomic batch. |
| Whole-number quantities are sufficient | Stale assumption | The current API supports fractional quantities to 0.01 contracts. The repository’s `count_to_int()` truncates them. Internal units must preserve fractional depth. |

There is an additional forward-compatibility risk absent from the blueprint: Kalshi’s order-book channel supports `use_yes_price`, and official documentation says its default will change in a future release. The repository currently relies on the legacy NO-leg price scale without setting the flag explicitly. It should opt into a documented scale and test both sides. [Official order-direction reference](https://docs.kalshi.com/getting_started/order_direction)

## What the repository does well

- Production endpoints are rejected when demo order submission is enabled.
- Orders use validated schemas, unique client IDs, IOC limits, and a current batch endpoint.
- Partial/rejected batches are persisted, and opportunities are marked executed only when every leg fully fills.
- Market/event grouping corrects several stale assumptions in the original project specification.
- In a clean virtual environment installed with `.[dev]`, the full suite passes: **46 passed**. This confirms internal consistency of the current feature set, not compliance with the new plan.

## Recommended implementation sequence

1. **Harden the market-data path first.** Preserve source timestamps and `sid`/`seq`, detect gaps/out-of-order deltas, request a replacement snapshot, and reject missing or stale books before detection.
2. **Use precise monetary units.** Represent prices and fees in fixed-point dollars/centicents or `Decimal`, clearly distinguish per-contract from total amounts, and persist gross, fee, slippage, and adjusted-net values separately.
3. **Model the actual event-contract fee schedule.** Load series fee type/multiplier, implement current taker/maker formulas and required rounding, and keep fee policy configurable/versioned because it changes over time.
4. **Make opportunities quantity-aware.** Calculate executable profit across book depth for the requested quantity. Cap quantity by the minimum available capacity across all legs.
5. **Add an execution validation gate.** Immediately before submission, batch-fetch unique order books, rebuild all legs, reapply fees and buffers, reject stale/missing/shallow/non-positive cases, and submit only the revalidated prices and quantity.
6. **Test economics and races.** Add exact rounding tests, zero/negative adjusted-edge rejection, stale timestamps, missing levels, insufficient depth, improved/worsened prices, duplicate tickers, and proof that `place_orders()` is never called after validation failure.
7. **Address residual exposure.** Decide on fill-or-kill versus IOC per leg and add an explicit automated unwind/hedge or kill-switch policy. Batch submission is not atomic even when every individual order is constrained.

## Bottom line

This is a solid demo scaffold with passing tests and sensible endpoint safety, but it remains a gross-edge research engine. Against the attached implementation plans, the verdict is **not implemented**. The first code change should harden live-book integrity and freshness; precise fee, depth, and execution validation should then be built on that trustworthy state.
