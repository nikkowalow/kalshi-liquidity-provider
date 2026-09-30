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

WS_URLS = {
    Environment.DEMO: "wss://external-api-ws.demo.kalshi.co/trade-api/ws/v2",
    Environment.PROD: "wss://external-api-ws.kalshi.com/trade-api/ws/v2",
}


class _Section(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class LoopConfig(_Section):
    """Quoting is event-driven off the WebSocket feed; these bound how often things run."""

    requote_min_interval_seconds: float = Field(
        0.25, ge=0, description="Debounce: minimum time between requotes of the same market."
    )
    heartbeat_seconds: float = Field(
        5, gt=0, description="Requote every market at least this often, even if nothing changed."
    )
    reconcile_interval_seconds: float = Field(
        15, gt=0, description="Cross-check orders, positions and balance over REST."
    )
    queue_refresh_seconds: float = Field(
        10, gt=0, description="Refresh our orders' queue positions (for reward tracking)."
    )
    reward_report_seconds: float = Field(60, gt=0, description="Log reward estimates this often.")
    reselect_interval_seconds: float = Field(900, gt=0, description="How often to re-rank markets.")
    reselect_on_pause: bool = Field(
        True,
        description=(
            "When a market gets paused, re-rank early and give its slot to the best other "
            "market that passes the filters. If none does, the paused market keeps its slot."
        ),
    )
    min_reselect_gap_seconds: float = Field(
        60, ge=0, description="Early re-ranks (on pause) happen at most this often."
    )
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
    competition_weight: Decimal = Field(
        Decimal("0"),
        ge=0,
        le=1,
        description=(
            "Prefer uncrowded markets: low-competition markets rank x(1 + w), high ones "
            "x(1 - w), medium unchanged. Competition is how deep the Target Size reaches below "
            "the best bid (see strategy.rewards.competition): crowded books force quotes to the "
            "top, where fills happen, and their reward share flickers."
        ),
    )
    fill_risk: bool = Field(
        True,
        description=(
            "Price in the risk of being filled: replay each top candidate's recent public "
            "trades against the quotes we'd post (see strategy.fill_risk), and rank by "
            "estimated reward minus the expected cost of the fills. Markets whose fills would "
            "cost more than they pay are dropped."
        ),
    )
    fill_risk_pool: int = Field(
        25, ge=1, description="How many of the best-ranked candidates get their trades checked."
    )
    trade_lookback_hours: float = Field(24, gt=0, description="Trade history to replay.")
    sweep_window_seconds: float = Field(
        2.0,
        gt=0,
        description="Trades this close together count as one sweep (the bot can't react faster).",
    )
    max_trades_per_market: int = Field(
        5000, ge=100, description="Cap on trades fetched per market and check (API budget)."
    )
    taker_fee_rate: Decimal = Field(
        Decimal("0.07"), ge=0, description="Kalshi taker fee: rate x P x (1 - P) per contract."
    )
    adverse_move: Decimal = Field(
        Decimal("0.02"),
        ge=0,
        description="Assumed loss per filled contract from the price move that caused the fill.",
    )
    min_net_daily_reward: Decimal = Field(
        Decimal("0"), description="Drop markets whose reward minus fill cost is at or below this."
    )
    catalog_refresh_seconds: float = Field(
        600,
        ge=0,
        description=(
            "How long to reuse the list of programs and their markets between scans. Order "
            "books (and trades) are always fetched fresh, so scans can run every minute."
        ),
    )
    protect_unpaid: bool = Field(
        True,
        description=(
            "Count what leaving would forfeit: a market we've earned in but not yet up to "
            "payout_minimum (Kalshi pays nothing below it) gets that amount, spread over its "
            "remaining time, added to its $/day when compared with other markets. Only if "
            "staying would actually reach the minimum."
        ),
    )
    payout_minimum: Decimal = Field(
        Decimal("1"), ge=0, description="Kalshi's minimum payout per market and period, dollars."
    )
    incumbent_bonus: Decimal = Field(
        Decimal("0.1"),
        ge=0,
        description=(
            "Stickiness: markets we already quote, or have earned rewards in, rank as if their "
            "estimate were this much higher (0.5 = +50%). A newcomer must beat them by that "
            "margin to take their slot. Switching throws away the queue spot and the progress "
            "toward Kalshi's $1 minimum payout."
        ),
    )
    min_period_payout: Decimal = Field(
        Decimal("0"),
        ge=0,
        description=(
            "Skip markets whose projected payout over the program's remaining period is below "
            "this. Kalshi pays nothing under $1, so spreading thin can earn $0."
        ),
    )
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
    auto_size: bool = Field(
        False,
        description=(
            "Size quotes to use the capital: split risk.max_capital (less what positions tie up) "
            "evenly across the markets being quoted, at each market's current prices. "
            "Capped by max_size and risk.max_position_per_market; `size` is then ignored."
        ),
    )
    max_size: Decimal | None = Field(
        None, gt=0, description="With auto_size, never quote more than this per side."
    )
    capital_utilization: Decimal = Field(
        Decimal("0.95"),
        gt=0,
        le=1,
        description="With auto_size, the share of the budget to put to work (slack for rounding).",
    )
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
    min_cushion: Decimal = Field(
        Decimal("0"),
        ge=0,
        description=(
            "Only quote where at least this many contracts from other traders rest ahead of "
            "us (at our price or better). A sell-off must eat through them before reaching us, "
            "and the bot backs away as they disappear. 0 disables."
        ),
    )
    min_quote_life_seconds: float = Field(
        0,
        ge=0,
        description=(
            "Don't move a resting order for reward reasons until it is this old (safety moves "
            "still happen immediately). Stops churn and keeps queue position."
        ),
    )

    @model_validator(mode="after")
    def _check_bounds(self) -> QuotingConfig:
        if self.min_price >= self.max_price:
            raise ValueError("quoting.min_price must be below quoting.max_price")
        return self


class RiskConfig(_Section):
    max_capital: Decimal | None = Field(
        None,
        gt=0,
        description=(
            "Dollar cap on position cost plus cash locked in resting orders. "
            "New orders are shrunk or skipped to stay under it. None disables."
        ),
    )
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
    flatten_on_fill: bool = Field(
        False,
        description=(
            "Never hold a position: when an order fills, cancel that market's quotes and close "
            "the position at once with an immediate-or-cancel order that crosses the book. "
            "Costs the spread plus taker fees, but removes directional risk."
        ),
    )
    flatten_slippage: Decimal = Field(
        Decimal("0.02"),
        ge=0,
        description=(
            "How far through the best price an exit may go (sweeps thin top levels), in dollars."
        ),
    )
    flatten_retry_seconds: float = Field(
        2.0, gt=0, description="Minimum time between exit attempts in one market."
    )
    close_buffer_seconds: float = Field(
        900, ge=0, description="Stop quoting this long before a market closes."
    )
    max_consecutive_errors: int = Field(5, ge=1)
    max_mid_move: Decimal | None = Field(
        None,
        gt=0,
        description=(
            "Pause a market (cooldown_seconds) if its mid moves this much (dollars) within "
            "mid_move_window_seconds: fast moves mean news, and resting orders get picked off."
        ),
    )
    mid_move_window_seconds: float = Field(30, gt=0)
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
    runs_dir: Path = Field(Path("runs"), description="Run journals for the dashboard.")
    loop: LoopConfig = Field(default_factory=LoopConfig)
    rate_limits: RateLimitConfig = Field(default_factory=RateLimitConfig)
    selection: SelectionConfig = Field(default_factory=SelectionConfig)
    quoting: QuotingConfig = Field(default_factory=QuotingConfig)
    risk: RiskConfig = Field(default_factory=RiskConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)

    @property
    def api_url(self) -> str:
        return os.environ.get("KALSHI_API_URL") or API_URLS[self.environment]

    @property
    def ws_url(self) -> str:
        return os.environ.get("KALSHI_WS_URL") or WS_URLS[self.environment]

    def has_credentials(self) -> bool:
        prefix = f"KALSHI_{self.environment.value.upper()}"
        return bool(
            os.environ.get(f"{prefix}_KEY_ID") and os.environ.get(f"{prefix}_PRIVATE_KEY_PATH")
        )

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
