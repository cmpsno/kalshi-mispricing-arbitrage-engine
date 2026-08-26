"""Environment-backed settings for the async engine."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from kalshi_client.config import DEMO_BASE_URL, ConfigError

DEMO_WS_URL = "wss://external-api-ws.demo.kalshi.co/trade-api/ws/v2"
DEMO_REST_HOST = "external-api.demo.kalshi.co"
DEMO_WS_HOST = "external-api-ws.demo.kalshi.co"


def _parse_bool(name: str, value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ConfigError(f"{name} must be true or false")


@dataclass(frozen=True, slots=True)
class Settings:
    api_key_id: str
    private_key_path: Path
    base_url: str = DEMO_BASE_URL
    ws_url: str = DEMO_WS_URL
    timeout_seconds: float = 10.0
    max_collateral_cents: int = 1_000_000
    dry_run: bool = True
    database_path: Path = Path("kalshi_arbitrage.db")
    scan_interval_seconds: float = 2.0
    market_limit: int = 1_000

    def __post_init__(self) -> None:
        key_path = str(self.private_key_path).strip()
        object.__setattr__(self, "api_key_id", self.api_key_id.strip())
        object.__setattr__(self, "private_key_path", Path(key_path).expanduser())
        object.__setattr__(self, "base_url", self.base_url.strip().rstrip("/"))
        object.__setattr__(self, "ws_url", self.ws_url.strip())
        object.__setattr__(self, "database_path", Path(self.database_path).expanduser())

        if not self.api_key_id:
            raise ConfigError("KALSHI_API_KEY_ID is required")
        if key_path in {"", "."}:
            raise ConfigError("KALSHI_PRIVATE_KEY_PATH is required")
        if self.timeout_seconds <= 0:
            raise ConfigError(
                "KALSHI_REQUEST_TIMEOUT_SECONDS must be greater than zero"
            )
        if self.max_collateral_cents <= 0:
            raise ConfigError("MAX_COLLATERAL_CENTS must be greater than zero")
        if self.scan_interval_seconds <= 0:
            raise ConfigError("KALSHI_SCAN_INTERVAL_SECONDS must be greater than zero")
        if not 1 <= self.market_limit <= 1_000:
            raise ConfigError("KALSHI_MARKET_LIMIT must be between 1 and 1000")

        rest = urlsplit(self.base_url)
        if (
            rest.scheme != "https"
            or not rest.netloc
            or not rest.path.endswith("/trade-api/v2")
        ):
            raise ConfigError("KALSHI_BASE_URL must be an HTTPS Trade API v2 root")
        websocket = urlsplit(self.ws_url)
        if websocket.scheme != "wss" or not websocket.netloc:
            raise ConfigError("KALSHI_WS_URL must be an absolute wss:// URL")
        if not self.dry_run and (
            rest.hostname != DEMO_REST_HOST or websocket.hostname != DEMO_WS_HOST
        ):
            raise ConfigError(
                "DRY_RUN=false is supported only with Kalshi demo REST and WebSocket hosts"
            )

    @classmethod
    def from_env(cls) -> Settings:
        required = {
            "KALSHI_API_KEY_ID": os.getenv("KALSHI_API_KEY_ID", ""),
            "KALSHI_PRIVATE_KEY_PATH": os.getenv("KALSHI_PRIVATE_KEY_PATH", ""),
        }
        missing = [name for name, value in required.items() if not value.strip()]
        if missing:
            raise ConfigError(f"Missing required configuration: {', '.join(missing)}")

        try:
            return cls(
                api_key_id=required["KALSHI_API_KEY_ID"],
                private_key_path=Path(required["KALSHI_PRIVATE_KEY_PATH"]),
                base_url=os.getenv("KALSHI_BASE_URL", DEMO_BASE_URL),
                ws_url=os.getenv("KALSHI_WS_URL", DEMO_WS_URL),
                timeout_seconds=float(
                    os.getenv("KALSHI_REQUEST_TIMEOUT_SECONDS", "10")
                ),
                max_collateral_cents=int(os.getenv("MAX_COLLATERAL_CENTS", "1000000")),
                dry_run=_parse_bool("DRY_RUN", os.getenv("DRY_RUN", "true")),
                database_path=Path(
                    os.getenv("KALSHI_DATABASE_PATH", "kalshi_arbitrage.db")
                ),
                scan_interval_seconds=float(
                    os.getenv("KALSHI_SCAN_INTERVAL_SECONDS", "2")
                ),
                market_limit=int(os.getenv("KALSHI_MARKET_LIMIT", "1000")),
            )
        except ValueError as exc:
            if isinstance(exc, ConfigError):
                raise
            raise ConfigError(f"Invalid numeric engine configuration: {exc}") from exc
