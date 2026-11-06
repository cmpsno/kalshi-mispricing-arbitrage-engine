"""Read-only queries used by the Streamlit dashboard."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd

from .models import OrderBook, OrderBookLevel
from .paper.models import PaperAccountState, PaperPosition
from .paper.report import overall_report, week_bounds, weekly_report

OPPORTUNITY_COLUMNS = [
    "id",
    "detected_at",
    "algorithm",
    "markets_involved",
    "action",
    "gross_profit_cents",
    "required_collateral_cents",
    "confidence_score",
    "executed",
]


class DashboardDatabaseError(RuntimeError):
    """Raised when the dashboard database cannot be read yet."""


def _connect(database_path: str | Path) -> sqlite3.Connection:
    path = Path(database_path).expanduser().resolve()
    if not path.is_file():
        raise DashboardDatabaseError(
            f"Database not found at {path}. Run the engine once to create it."
        )
    try:
        connection = sqlite3.connect(
            f"file:{path.as_posix()}?mode=ro", uri=True, timeout=5
        )
        connection.execute("PRAGMA query_only=ON")
        return connection
    except sqlite3.Error as exc:
        raise DashboardDatabaseError(f"Could not open database at {path}: {exc}") from exc


def load_opportunities(
    database_path: str | Path,
    *,
    limit: int = 1_000,
    hours: int = 24,
    now: datetime | None = None,
) -> pd.DataFrame:
    """Return recent opportunities ordered newest first."""

    if limit < 1:
        raise ValueError("limit must be at least 1")
    if hours < 1:
        raise ValueError("hours must be at least 1")

    current_time = now or datetime.now(UTC)
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=UTC)
    cutoff_ms = int((current_time - timedelta(hours=hours)).timestamp() * 1_000)

    query = """
        SELECT id, detected_at, algorithm, markets_involved, action,
               gross_profit_cents, required_collateral_cents,
               confidence_score, executed
        FROM opportunities
        WHERE detected_at >= ?
        ORDER BY detected_at DESC
        LIMIT ?
    """
    try:
        with _connect(database_path) as connection:
            frame = pd.read_sql_query(query, connection, params=(cutoff_ms, limit))
    except (sqlite3.Error, pd.errors.DatabaseError) as exc:
        raise DashboardDatabaseError(
            "The opportunities table is unavailable. Run the engine once to initialize it."
        ) from exc

    if frame.empty:
        return pd.DataFrame(columns=OPPORTUNITY_COLUMNS + ["roi_percent"])

    frame["detected_at"] = pd.to_datetime(
        frame["detected_at"], unit="ms", utc=True
    )
    collateral = frame["required_collateral_cents"].replace(0, pd.NA)
    frame["roi_percent"] = frame["gross_profit_cents"] / collateral * 100
    frame["executed"] = frame["executed"].astype(bool)
    return frame


def summarize_opportunities(frame: pd.DataFrame) -> pd.DataFrame:
    """Aggregate counts and profit statistics by detector algorithm."""

    columns = [
        "algorithm",
        "count",
        "avg_profit_cents",
        "max_profit_cents",
        "avg_roi_percent",
    ]
    if frame.empty:
        return pd.DataFrame(columns=columns)

    return (
        frame.groupby("algorithm", as_index=False)
        .agg(
            count=("id", "count"),
            avg_profit_cents=("gross_profit_cents", "mean"),
            max_profit_cents=("gross_profit_cents", "max"),
            avg_roi_percent=("roi_percent", "mean"),
        )
        .sort_values("count", ascending=False)
        .reset_index(drop=True)
    )


# ----------------------------------------------------------------------------
# Paper trading (Phase 5): read-only summary of the local paper ledger.
# Paper figures come only from paper tables; detector opportunities are
# reported separately and never mixed into paper results.
# ----------------------------------------------------------------------------


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone()
    return row is not None


def _rows(connection: sqlite3.Connection, query: str, params: tuple = ()) -> list[dict]:
    cursor = connection.execute(query, params)
    columns = [column[0] for column in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def _latest_snapshot(snapshots: list[dict], ts_ms: int) -> dict | None:
    candidates = [row for row in snapshots if row["ts"] <= ts_ms]
    if not candidates:
        return None
    return max(candidates, key=lambda row: (row["ts"], row["id"]))


def load_paper_summary(
    database_path: str | Path,
    *,
    now: datetime | None = None,
    max_book_age_seconds: float = 5.0,
) -> dict | None:
    """Return the paper-trading summary, or ``None`` if no paper account
    exists yet in this database.

    The summary holds the overall report (``overall``), the current-week
    report (``weekly``), the equity curve (``equity_curve``), recent
    fills (``fills``), recent ledger events (``ledger``), and the last
    ledger/snapshot update (``last_update_ms``). Equity and unrealized
    P&L are ``None`` when any open position lacks a fresh order book.
    """

    current_time = now or datetime.now(UTC)
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=UTC)
    now_ms = int(current_time.timestamp() * 1_000)

    with _connect(database_path) as connection:
        connection.row_factory = sqlite3.Row
        if not _table_exists(connection, "paper_accounts"):
            return None
        accounts = _rows(
            connection,
            "SELECT * FROM paper_accounts ORDER BY id LIMIT 1",
        )
        if not accounts:
            return None
        account = accounts[0]

        ledger = _rows(
            connection, "SELECT * FROM paper_ledger_events ORDER BY id"
        )
        position_rows = _rows(
            connection,
            "SELECT * FROM paper_positions WHERE closed_at IS NULL ORDER BY id",
        )
        orders = _rows(connection, "SELECT * FROM paper_orders ORDER BY created_at, id")
        fills = _rows(connection, "SELECT * FROM paper_fills ORDER BY id")
        snapshots = _rows(
            connection, "SELECT * FROM paper_equity_snapshots ORDER BY ts, id"
        )
        book_rows = _rows(
            connection,
            """
            SELECT * FROM order_books
            WHERE (ticker, timestamp) IN (
                SELECT ticker, MAX(timestamp) FROM order_books GROUP BY ticker
            )
            """,
        )
        opportunities = (
            _rows(connection, "SELECT COUNT(*) AS count FROM opportunities")[0]["count"]
            if _table_exists(connection, "opportunities")
            else 0
        )
        fill_rows = _rows(
            connection,
            """
            SELECT f.filled_at, o.ticker, o.side, o.direction,
                   f.filled_quantity, f.fill_price_cents, f.total_cents
            FROM paper_fills AS f
            JOIN paper_orders AS o ON o.id = f.order_id
            ORDER BY f.filled_at DESC, f.id DESC LIMIT 100
            """,
        )
        ledger_rows = _rows(
            connection,
            """
            SELECT ts, event_type, ticker, cash_delta_cents,
                   reserved_delta_cents, realized_pnl_cents
            FROM paper_ledger_events
            ORDER BY id DESC LIMIT 100
            """,
        )

    positions: dict[tuple[str, str, str], PaperPosition] = {}
    for row in position_rows:
        position = PaperPosition(
            ticker=row["ticker"],
            side=row["side"],
            direction=row["direction"],
            quantity=row["quantity"],
            cost_basis_cents=row["cost_basis_cents"],
            opened_at_ms=row["opened_at"],
            db_id=row["id"],
        )
        positions[position.key] = position
    state = PaperAccountState(
        initial_cash_cents=account["initial_cash_cents"],
        cash_cents=sum(event["cash_delta_cents"] for event in ledger),
        realized_pnl_cents=sum(event["realized_pnl_cents"] for event in ledger),
        positions=positions,
    )

    books: dict[str, OrderBook] = {}
    for row in book_rows:
        books[row["ticker"]] = OrderBook(
            ticker=row["ticker"],
            timestamp=datetime.fromtimestamp(row["timestamp"] / 1_000, tz=UTC),
            bids=[
                OrderBookLevel(price=price, size=size)
                for price, size in json.loads(row["bids_json"])
            ],
            asks=[
                OrderBookLevel(price=price, size=size)
                for price, size in json.loads(row["asks_json"])
            ],
        )

    overall = overall_report(
        state,
        books,
        now_ms,
        max_book_age_seconds,
        orders=orders,
        fills=fills,
        opportunities_count=int(opportunities),
    )

    start_ms, end_ms = week_bounds(now_ms)
    weekly = weekly_report(
        start_ms=start_ms,
        end_ms=end_ms,
        initial_cash_cents=state.initial_cash_cents,
        snapshot_at_start=_latest_snapshot(snapshots, start_ms),
        snapshot_at_end=_latest_snapshot(snapshots, end_ms),
        had_events_before_start=any(event["ts"] < start_ms for event in ledger),
        ledger_events_in_range=[
            event for event in ledger if start_ms <= event["ts"] < end_ms
        ],
        orders_in_range=[
            order for order in orders if start_ms <= order["created_at"] < end_ms
        ],
        fills_in_range=[
            fill for fill in fills if start_ms <= fill["filled_at"] < end_ms
        ],
        open_positions=list(positions.values()),
        state_at_end=state,
    )

    equity_curve = pd.DataFrame(
        snapshots,
        columns=["ts", "equity_cents", "cash_cents", "unrealized_pnl_cents"],
    )
    if not equity_curve.empty:
        equity_curve["detected_at"] = pd.to_datetime(
            equity_curve["ts"], unit="ms", utc=True
        )
    fills_frame = pd.DataFrame(
        fill_rows,
        columns=[
            "filled_at",
            "ticker",
            "side",
            "direction",
            "filled_quantity",
            "fill_price_cents",
            "total_cents",
        ],
    )
    if not fills_frame.empty:
        fills_frame["filled_at"] = pd.to_datetime(
            fills_frame["filled_at"], unit="ms", utc=True
        )
    ledger_frame = pd.DataFrame(
        ledger_rows,
        columns=[
            "ts",
            "event_type",
            "ticker",
            "cash_delta_cents",
            "reserved_delta_cents",
            "realized_pnl_cents",
        ],
    )
    if not ledger_frame.empty:
        ledger_frame["ts"] = pd.to_datetime(ledger_frame["ts"], unit="ms", utc=True)

    last_update_ms = None
    candidates = [event["ts"] for event in ledger] + [
        row["ts"] for row in snapshots
    ]
    if candidates:
        last_update_ms = max(candidates)

    return {
        "overall": overall,
        "weekly": weekly,
        "equity_curve": equity_curve,
        "fills": fills_frame,
        "ledger": ledger_frame,
        "last_update_ms": last_update_ms,
    }
