"""Bot configuration.

Strategy and risk settings live in YAML (``config/*.yaml``). Credentials come
only from the environment (or a ``.env`` file), and demo and production use
different variable names so a demo config can never pick up production keys.
"""

from __future__ import annotations

import os
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, model_validator


class Environment(StrEnum):
    DEMO = "demo"
    PROD = "prod"


API_URLS = {
    Environment.DEMO: "https://external-api.demo.kalshi.co/trade-api/v2",
    Environment.PROD: "https://api.elections.kalshi.com/trade-api/v2",
}


class _Section(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class LoopConfig(_Section):
    interval_seconds: float = Field(2.0, gt=0, description="Time between quoting cycles.")
    reselect_interval_seconds: float = Field(900, gt=0, description="How often to re-rank markets.")
    status_check_interval_seconds: float = Field(30, gt=0)


class RateLimitConfig(_Section):
    """Token budgets per second for your Kalshi tier (Basic: 200 read / 100 write)."""

    read_per_sec: float = Field(200, gt=0)
    write_per_sec: float = Field(100, gt=0)


class SelectionConfig(_Section):
    mode: Literal["incentives", "tickers", "volume"] = Field(
        "incentives",
        description=(
            "incentives: markets with active liquidity rewards. tickers: only the listed "
            "tickers. volume: most-traded open markets (useful in demo, which may have no "
            "incentive programs)."
        ),
    )
    fallback_to_volume: bool = Field(
        True, description="In incentives mode, use volume ranking if no programs are active."
    )
    tickers: list[str] = Field(default_factory=list)
    exclude_tickers: list[str] = Field(default_factory=list)
    max_markets: int = Field(5, ge=1, le=100)
    max_per_series: int = Field(
        2, ge=1, description="Diversify: cap markets from one series (their outcomes correlate)."
    )
    min_seconds_to_close: float = Field(3600, ge=0)
    min_mid_price: Decimal = Field(Decimal("0.05"), gt=0, lt=1)
    max_mid_price: Decimal = Field(Decimal("0.95"), gt=0, lt=1)
    max_spread: Decimal = Field(
        Decimal("0.10"),
        gt=0,
        le=1,
        description="Skip markets with a wider YES spread: their mid is not a fair value.",
    )
    min_daily_reward: Decimal = Field(Decimal("0"), ge=0)
    candidate_pool: int = Field(
        25_000, ge=1, description="Max open markets scanned in volume mode (1,000 per read)."
    )


class QuotingConfig(_Section):
    placement: Literal["reward", "join", "improve"] = Field(
        "reward",
        description=(
            "reward: the deepest price that earns (close to) the maximum reward share. "
            "join: rest at the best bid. improve: one tick better than the best bid."
        ),
    )
    share_tolerance: Decimal = Field(
        Decimal("0.10"),
        ge=0,
        lt=1,
        description="reward placement: accept this much less share for a deeper, safer price.",
    )
    size: Decimal = Field(Decimal("10"), gt=0, description="Contracts per side.")
    offset_ticks: int = Field(0, ge=0, description="Extra ticks behind the placement price.")
    min_edge: Decimal = Field(
        Decimal("0.01"), ge=0, description="Minimum distance from mid for any quote (dollars)."
    )
    skew_per_contract: Decimal = Field(
        Decimal("0.001"), ge=0, description="Price shift per contract of inventory (dollars)."
    )
    max_skew: Decimal = Field(Decimal("0.05"), ge=0)
    min_price: Decimal = Field(Decimal("0.03"), gt=0, lt=1, description="Lowest bid, per leg.")
    max_price: Decimal = Field(Decimal("0.97"), gt=0, lt=1, description="Highest bid, per leg.")
    one_sided_edge: Decimal = Field(
        Decimal("0.05"), ge=0, description="Distance behind the opposite best when a side is empty."
    )
    full_credit_fraction: Decimal = Field(
        Decimal("0.2"), gt=0, le=1, description="Program rule: reference depth = target / 5."
    )
    default_target_size: Decimal = Field(
        Decimal("500"), gt=0, description="Used when a market has no incentive program."
    )
    default_discount_factor: Decimal = Field(Decimal("0.5"), gt=0, le=1)
    post_only: bool = True

    @model_validator(mode="after")
    def _check_bounds(self) -> QuotingConfig:
        if self.min_price >= self.max_price:
            raise ValueError("quoting.min_price must be below quoting.max_price")
        return self


class RiskConfig(_Section):
    max_position_per_market: Decimal = Field(Decimal("50"), gt=0, description="Contracts.")
    max_total_exposure: Decimal = Field(Decimal("100"), gt=0, description="Dollars at cost.")
    max_session_loss: Decimal = Field(
        Decimal("25"), gt=0, description="Halt and cancel everything past this loss (dollars)."
    )
    min_balance: Decimal = Field(
        Decimal("10"), ge=0, description="Stop adding risk below this cash balance (dollars)."
    )
    fill_burst_contracts: Decimal = Field(
        Decimal("30"), gt=0, description="Pause a market if this many contracts fill in the window."
    )
    fill_burst_window_seconds: float = Field(60, gt=0)
    cooldown_seconds: float = Field(120, ge=0)
    close_buffer_seconds: float = Field(
        900, ge=0, description="Stop quoting this long before a market closes."
    )
    max_consecutive_errors: int = Field(5, ge=1)
    order_group_contracts_limit: int = Field(
        50, ge=0, description="Exchange-side fill cap per rolling 15s window (0 disables)."
    )


class LoggingConfig(_Section):
    level: str = "INFO"
    file: Path | None = None
    json_format: bool = False


class Credentials(_Section):
    key_id: str
    private_key_path: Path


class Settings(_Section):
    environment: Environment = Environment.DEMO
    dry_run: bool = Field(True, description="Compute and log quotes without sending orders.")
    subaccount: int = Field(0, ge=0, le=63)
    client_order_prefix: str = Field("klp", pattern=r"^[A-Za-z0-9]{1,12}$")
    loop: LoopConfig = Field(default_factory=LoopConfig)
    rate_limits: RateLimitConfig = Field(default_factory=RateLimitConfig)
    selection: SelectionConfig = Field(default_factory=SelectionConfig)
    quoting: QuotingConfig = Field(default_factory=QuotingConfig)
    risk: RiskConfig = Field(default_factory=RiskConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)

    @property
    def api_url(self) -> str:
        return os.environ.get("KALSHI_API_URL") or API_URLS[self.environment]

    def credentials(self) -> Credentials:
        prefix = f"KALSHI_{self.environment.value.upper()}"
        key_id = os.environ.get(f"{prefix}_KEY_ID")
        key_path = os.environ.get(f"{prefix}_PRIVATE_KEY_PATH")
        if not key_id or not key_path:
            raise ConfigError(
                f"Missing credentials: set {prefix}_KEY_ID and {prefix}_PRIVATE_KEY_PATH "
                "(see .env.example)."
            )
        path = Path(key_path).expanduser()
        if not path.is_file():
            raise ConfigError(f"Private key not found at {path}")
        return Credentials(key_id=key_id, private_key_path=path)


class ConfigError(Exception):
    pass


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_settings(
    path: str | Path, overrides: dict[str, Any] | None = None, *, dotenv: bool = False
) -> Settings:
    if dotenv:
        load_dotenv()
    path = Path(path)
    if not path.is_file():
        raise ConfigError(f"Config file not found: {path}")
    raw = yaml.safe_load(path.read_text()) or {}
    if not isinstance(raw, dict):
        raise ConfigError(f"{path} must contain a YAML mapping")
    return Settings.model_validate(_deep_merge(raw, overrides or {}))
