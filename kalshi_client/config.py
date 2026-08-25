"""Environment-backed configuration for the Kalshi demo client."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit


DEMO_BASE_URL = "https://external-api.demo.kalshi.co/trade-api/v2"


class ConfigError(ValueError):
    """Raised when required client configuration is missing or invalid."""


@dataclass(frozen=True, slots=True)
class Settings:
    api_key_id: str
    private_key_path: Path
    base_url: str = DEMO_BASE_URL
    timeout_seconds: float = 10.0

    def __post_init__(self) -> None:
        raw_private_key_path = str(self.private_key_path).strip()
        object.__setattr__(self, "api_key_id", self.api_key_id.strip())
        object.__setattr__(self, "private_key_path", Path(raw_private_key_path).expanduser())
        object.__setattr__(self, "base_url", self.base_url.strip().rstrip("/"))

        if not self.api_key_id:
            raise ConfigError("KALSHI_API_KEY_ID is required")
        if raw_private_key_path in {"", "."}:
            raise ConfigError("KALSHI_PRIVATE_KEY_PATH is required")
        if self.timeout_seconds <= 0:
            raise ConfigError("KALSHI_REQUEST_TIMEOUT_SECONDS must be greater than zero")

        parsed = urlsplit(self.base_url)
        if parsed.scheme != "https" or not parsed.netloc:
            raise ConfigError("KALSHI_BASE_URL must be an absolute HTTPS URL")
        if parsed.query or parsed.fragment:
            raise ConfigError("KALSHI_BASE_URL must not contain a query or fragment")
        if not parsed.path.endswith("/trade-api/v2"):
            raise ConfigError("KALSHI_BASE_URL must end with /trade-api/v2")

    @classmethod
    def from_env(cls) -> "Settings":
        """Read settings from the process environment."""

        api_key_id = os.getenv("KALSHI_API_KEY_ID", "")
        private_key_path = os.getenv("KALSHI_PRIVATE_KEY_PATH", "")
        base_url = os.getenv("KALSHI_BASE_URL", DEMO_BASE_URL)
        raw_timeout = os.getenv("KALSHI_REQUEST_TIMEOUT_SECONDS", "10")

        missing = [
            name
            for name, value in (
                ("KALSHI_API_KEY_ID", api_key_id),
                ("KALSHI_PRIVATE_KEY_PATH", private_key_path),
            )
            if not value.strip()
        ]
        if missing:
            raise ConfigError(f"Missing required configuration: {', '.join(missing)}")

        try:
            timeout_seconds = float(raw_timeout)
        except ValueError as exc:
            raise ConfigError(
                "KALSHI_REQUEST_TIMEOUT_SECONDS must be a number"
            ) from exc

        return cls(
            api_key_id=api_key_id,
            private_key_path=Path(private_key_path),
            base_url=base_url,
            timeout_seconds=timeout_seconds,
        )
