"""Configuration loading and validation.

Everything that should differ between demo/live or between deployments is
read from environment variables. Nothing here is a secret default — Telegram
and API credentials are NEVER hard-coded.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


class ConfigError(Exception):
    pass


DEFAULT_API_URL = "http://140.245.226.102:8080/public/m1-live.json"


@dataclass
class Config:
    # --- external endpoints -------------------------------------------------
    api_url: str = field(default_factory=lambda: os.environ.get("REALMARKET_API_URL", DEFAULT_API_URL))
    # Telegram credentials: read exclusively via os.getenv(), never hard-coded,
    # never given a non-empty default. Absence is a valid, detectable state
    # (see require_telegram_credentials()) rather than a silent empty string
    # standing in for a real secret.
    telegram_bot_token: str = field(default_factory=lambda: os.getenv("TELEGRAM_BOT_TOKEN", ""))
    telegram_chat_id: str = field(default_factory=lambda: os.getenv("TELEGRAM_CHAT_ID", ""))

    # --- storage --------------------------------------------------------------
    db_path: str = field(default_factory=lambda: os.environ.get("PSYGRID_DB_PATH", "psygrid.sqlite3"))

    # --- loop timing ------------------------------------------------------
    scan_interval_seconds: int = field(
        default_factory=lambda: int(os.environ.get("PSYGRID_SCAN_INTERVAL_SECONDS", "20"))
    )
    tournament_interval_minutes: int = field(
        default_factory=lambda: int(os.environ.get("PSYGRID_TOURNAMENT_INTERVAL_MINUTES", "30"))
    )
    api_timeout_seconds: float = field(
        default_factory=lambda: float(os.environ.get("PSYGRID_API_TIMEOUT_SECONDS", "15"))
    )

    # --- data quality gates -------------------------------------------------
    freshness_max_seconds: float = field(
        default_factory=lambda: float(os.environ.get("PSYGRID_FRESHNESS_MAX_SECONDS", "180"))
    )
    history_min_candles: int = field(
        default_factory=lambda: int(os.environ.get("PSYGRID_HISTORY_MIN_CANDLES", "200"))
    )
    max_rolling_candles: int = field(
        default_factory=lambda: int(os.environ.get("PSYGRID_MAX_ROLLING_CANDLES", "1600"))
    )

    # --- setup / execution gates --------------------------------------------
    min_rr: float = field(default_factory=lambda: float(os.environ.get("PSYGRID_MIN_RR", "1.5")))
    max_setup_age_minutes: float = field(
        default_factory=lambda: float(os.environ.get("PSYGRID_MAX_SETUP_AGE_MINUTES", "20"))
    )
    max_travel_ratio: float = field(
        default_factory=lambda: float(os.environ.get("PSYGRID_MAX_TRAVEL_RATIO", "0.75"))
    )
    abnormal_volatility_multiple: float = field(
        default_factory=lambda: float(os.environ.get("PSYGRID_ABNORMAL_VOL_MULTIPLE", "3.5"))
    )

    # --- scoring -------------------------------------------------------------
    # NOTE: these weights are declared, explainable defaults — NOT
    # statistically calibrated probabilities. They sum to 1.0 and every
    # component is reported individually alongside the final score so the
    # result is always auditable rather than a black box.
    min_quality_score: float = field(
        default_factory=lambda: float(os.environ.get("PSYGRID_MIN_QUALITY_SCORE", "78"))
    )
    weight_structure: float = 0.20
    weight_momentum: float = 0.15
    weight_liquidity: float = 0.15
    weight_volatility: float = 0.10
    weight_setup: float = 0.15
    weight_execution: float = 0.15
    weight_historical: float = 0.05
    weight_data_quality: float = 0.05
    historical_min_sample: int = field(
        default_factory=lambda: int(os.environ.get("PSYGRID_HISTORICAL_MIN_SAMPLE", "20"))
    )
    expected_instrument_count: int = field(
        default_factory=lambda: int(os.environ.get("PSYGRID_EXPECTED_INSTRUMENTS", "10"))
    )

    def validate(self) -> list:
        """Return a list of human-readable problems; empty means OK."""
        problems = []
        if not self.api_url:
            problems.append("REALMARKET_API_URL is empty.")
        weights = [
            self.weight_structure,
            self.weight_momentum,
            self.weight_liquidity,
            self.weight_volatility,
            self.weight_setup,
            self.weight_execution,
            self.weight_historical,
            self.weight_data_quality,
        ]
        if abs(sum(weights) - 1.0) > 1e-6:
            problems.append(f"Scoring weights must sum to 1.0, got {sum(weights)}")
        if not (0 < self.min_quality_score <= 100):
            problems.append("PSYGRID_MIN_QUALITY_SCORE must be in (0, 100].")
        if self.min_rr <= 0:
            problems.append("PSYGRID_MIN_RR must be positive.")
        return problems

    def telegram_configured(self) -> bool:
        return bool(self.telegram_bot_token) and bool(self.telegram_chat_id)

    def require_telegram_credentials(self) -> None:
        """Fail fast and clearly when Telegram secrets are not wired up.

        Raises :class:`ConfigError` naming exactly which environment
        variable(s) are missing. Never includes credential values in the
        message — there is nothing to redact when the whole point is that
        the value is absent, but this also guards against accidentally
        interpolating a *stray* value from elsewhere.
        """
        missing = []
        if not self.telegram_bot_token:
            missing.append("TELEGRAM_BOT_TOKEN")
        if not self.telegram_chat_id:
            missing.append("TELEGRAM_CHAT_ID")
        if missing:
            raise ConfigError(
                "Missing required Telegram configuration: "
                + ", ".join(missing)
                + ". Set these as environment variables (locally via .env / "
                "your shell, or in CI via GitHub Actions repository secrets) "
                "before running a Telegram connectivity check."
            )


def load_config() -> Config:
    return Config()
