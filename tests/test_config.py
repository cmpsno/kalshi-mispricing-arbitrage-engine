from __future__ import annotations

from pathlib import Path

import pytest

from kalshi_client.config import DEMO_BASE_URL, ConfigError, Settings


def test_settings_from_env_defaults_to_demo(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KALSHI_API_KEY_ID", " demo-key ")
    monkeypatch.setenv("KALSHI_PRIVATE_KEY_PATH", "private.pem")
    monkeypatch.delenv("KALSHI_BASE_URL", raising=False)
    monkeypatch.delenv("KALSHI_REQUEST_TIMEOUT_SECONDS", raising=False)

    settings = Settings.from_env()

    assert settings.api_key_id == "demo-key"
    assert settings.private_key_path == Path("private.pem")
    assert settings.base_url == DEMO_BASE_URL
    assert settings.timeout_seconds == 10.0


def test_settings_reports_all_missing_required_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("KALSHI_API_KEY_ID", raising=False)
    monkeypatch.delenv("KALSHI_PRIVATE_KEY_PATH", raising=False)

    with pytest.raises(ConfigError) as exc_info:
        Settings.from_env()

    assert "KALSHI_API_KEY_ID" in str(exc_info.value)
    assert "KALSHI_PRIVATE_KEY_PATH" in str(exc_info.value)


def test_settings_rejects_empty_private_key_path() -> None:
    with pytest.raises(ConfigError, match="KALSHI_PRIVATE_KEY_PATH"):
        Settings("key", Path(""))


@pytest.mark.parametrize(
    "base_url",
    [
        "http://external-api.demo.kalshi.co/trade-api/v2",
        "https://external-api.demo.kalshi.co/not-the-api",
        "https://external-api.demo.kalshi.co/trade-api/v2?x=1",
    ],
)
def test_settings_rejects_unsafe_or_malformed_base_url(base_url: str) -> None:
    with pytest.raises(ConfigError):
        Settings("key", Path("private.pem"), base_url=base_url)
