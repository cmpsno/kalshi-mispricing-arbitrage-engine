"""Local paper trading for the Kalshi mispricing engine.

Paper trading is a purely local simulation: it consumes the same
order-book observations as the detectors, simulates fills only when
displayed book depth supports them, and persists an append-only ledger
in SQLite. It never calls the Kalshi API and never submits an order to
any host.
"""

from .config import PaperSettings
from .service import PaperService
from .simulator import simulate_close, simulate_opportunity

__all__ = [
    "PaperService",
    "PaperSettings",
    "simulate_close",
    "simulate_opportunity",
]
