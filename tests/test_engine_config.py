from __future__ import annotations

from pathlib import Path

import pytest

from kalshi_client.config import ConfigError
from src.config import DEMO_WS_URL, Settings


def test_engine_settings_default_to_demo_and_dry_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("KALSHI_API_KEY_ID", "demo-key")
    monkeypatch.setenv("KALSHI_PRIVATE_KEY_PATH", "private.pem")
    for name in (
        "KALSHI_WS_URL",
        "MAX_COLLATERAL_CENTS",
        "DRY_RUN",
        "KALSHI_DATABASE_PATH",
        "KALSHI_SCAN_INTERVAL_SECONDS",
        "KALSHI_MARKET_LIMIT",
    ):
        monkeypatch.delenv(name, raising=False)

    settings = Settings.from_env()

    assert settings.ws_url == DEMO_WS_URL
    assert settings.dry_run is True
    assert settings.max_collateral_cents == 1_000_000
    assert settings.database_path == Path("kalshi_arbitrage.db")


def test_engine_settings_reject_invalid_boolean(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("KALSHI_API_KEY_ID", "demo-key")
    monkeypatch.setenv("KALSHI_PRIVATE_KEY_PATH", "private.pem")
    monkeypatch.setenv("DRY_RUN", "sometimes")

    with pytest.raises(ConfigError, match="DRY_RUN"):
        Settings.from_env()
