"""Bot configuration.

Strategy and risk settings live in YAML (``config/*.yaml``). Credentials come
only from the environment (or a ``.env`` file), and demo and production use
different variable names so a demo config can never pick up production keys.
"""

from __future__ import annotations

import os
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
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
    exclude_series: list[str] = Field(
        default_factory=list,
        description=(
            "Series never to quote (the ticker prefix, e.g. KXBIGGESTQUAKE). For markets that "
            "resolve on a sudden event: an earthquake, a word being said. They jump straight "
            "through every resting order, and their trade history rarely shows it coming."
        ),
    )
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
    fill_risk_queue_factor: Decimal = Field(
        Decimal(1),
        gt=0,
        le=1,
        description=(
            "How much of the queue in front of our quote the fill replay counts on (0.5: half, "
            "so a sweep half the queue's size already reaches us). Others often pull their "
            "orders just before a sweep; 1 trusts today's queue completely."
        ),
    )
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
    max_fills_per_day: Decimal | None = Field(
        None,
        ge=0,
        description=(
            "Drop markets whose replayed trades would fill more of our contracts per day than "
            "this (0: only markets no sweep would have reached). Markets whose trade history "
            "can't be fetched are dropped too, since their fill risk is unknown. Needs "
            "fill_risk. None disables."
        ),
    )
    max_fill_cost_share: Decimal | None = Field(
        None,
        gt=0,
        description=(
            "Drop markets whose expected fill cost is more than this share of their estimated "
            "reward (0.35: fills may eat at most 35%). A safety margin on top of "
            "min_net_daily_reward, which only drops markets where fills eat it all. Needs "
            "fill_risk. None disables."
        ),
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
    min_hold_seconds: float = Field(
        900,
        ge=0,
        description=(
            "A market keeps its slot at least this long after being selected, even if others "
            "now rank higher, as long as it still passes every filter and isn't paused. Stops "
            "markets flipping in and out as the estimates wobble from scan to scan."
        ),
    )
    min_period_payout: Decimal = Field(
        Decimal("0"),
        ge=0,
        description=(
            "Skip markets whose projected payout for the current program period (earned in it so "
            "far plus the estimate for the rest of it) is below this. Kalshi pays nothing for a "
            "period under $1, so spreading thin can earn $0. Markets we already have a stake in "
            "only need payout_minimum (if lower), so a market near the line isn't dropped and "
            "re-picked as its estimate wobbles."
        ),
    )
    candidate_pool: int = Field(
        25_000, ge=1, description="Max open markets scanned in volume mode (1,000 per read)."
    )

    @model_validator(mode="after")
    def _check_fill_filter(self) -> SelectionConfig:
        for name in ("max_fills_per_day", "max_fill_cost_share"):
            if getattr(self, name) is not None and not self.fill_risk:
                raise ValueError(f"selection.{name} needs selection.fill_risk: true")
        return self


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
    max_loss_per_fill: Decimal | None = Field(
        None,
        gt=0,
        description=(
            "Cap each order so a complete fill followed by the worst possible move (the "
            "bought side going to 0) loses at most this many dollars: size <= cap / price. "
            "The only protection that works against event jumps, which blow through any cushion."
        ),
    )
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
    scale_with_budget: bool = Field(
        False,
        description=(
            "Size everything from max_capital: the per-side loss cap (quoting.max_loss_per_fill) "
            "is the budget spread over selection.max_markets markets x 2 sides, and the order "
            "size limits, the exchange fill limit, max_total_exposure and max_session_loss follow "
            "from that. Change the budget and they all move with it. Their own values in the file "
            "are then ignored."
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
    exit_mode: Literal["immediate", "passive"] = Field(
        "immediate",
        description=(
            "How flatten_on_fill closes a position. immediate: cross the book at once. "
            "passive: rest an exit order at the entry price for up to unwind_seconds (a fill "
            "usually comes from a sweep that just emptied the book, so crossing at once sells "
            "at the worst price), crossing early when the price really moves against us "
            "(unwind_stop) or when the market is about to close."
        ),
    )
    unwind_seconds: float = Field(
        900, gt=0, description="passive exits: cross the book if not out after this long."
    )
    unwind_stop: Decimal = Field(
        Decimal("0.05"),
        gt=0,
        description=(
            "passive exits: cross the book once others offer our side this far below our "
            "entry (the price we could buy it back at, not the bid a sweep just emptied)."
        ),
    )
    unwind_giveup: Decimal = Field(
        Decimal("0"),
        ge=0,
        description="passive exits: rest this far below the entry price (0 = break even).",
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


class ScannerConfig(_Section):
    """The dashboard's market scanner: read-only research, never traded on."""

    enabled: bool = Field(
        True, description="Estimate every rewarded market's $/day for the dashboard."
    )
    interval_seconds: float = Field(300, ge=30, description="How often to rescan.")
    size: Decimal = Field(
        Decimal("10"), gt=0, description="Contracts per side every estimate assumes."
    )
    top: int = Field(100, ge=1, le=1000, description="How many of the best markets to report.")
    fill_risk: bool = Field(
        True, description="Replay the top markets' recent trades for expected fill cost."
    )
    pace_seconds: float = Field(
        0.1,
        ge=0,
        description="Pause between trade-history requests, leaving the rate limit to trading.",
    )


class ApiConfig(_Section):
    enabled: bool = Field(
        True, description="Serve the dashboard and its API (HTTP + WebSocket) while the bot runs."
    )
    host: str = Field(
        "127.0.0.1",
        description="Bind address. Keep it on localhost: the API shows positions and orders.",
    )
    port: int = Field(8050, ge=0, le=65535, description="0 picks a free port.")
    static_dir: Path | None = Field(
        Path("dashboard/dist"), description="Built dashboard (npm run build) served at /."
    )
    controls: bool = Field(
        True,
        description=(
            "Allow the dashboard's buttons (pause, resume, flatten, rescan, budget, stop): "
            "POST /api/control/<action> with the token from <journal>/api-token."
        ),
    )
    password: str | None = Field(
        None,
        description=(
            "Ask for this password before serving anything (HTTP basic auth, any user name). "
            "Required to serve beyond localhost (host other than 127.0.0.1). Set it with the "
            "KLP_DASHBOARD_PASSWORD environment variable, not in the config file."
        ),
    )

    @model_validator(mode="after")
    def _check_exposure(self) -> ApiConfig:
        local = self.host in ("127.0.0.1", "localhost", "::1")
        if not local and self.enabled and self.controls and not self.password:
            raise ValueError(
                "api.host is not localhost: set KLP_DASHBOARD_PASSWORD, or anyone who opens "
                "the dashboard could press its buttons"
            )
        return self


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
    api: ApiConfig = Field(default_factory=ApiConfig)
    scanner: ScannerConfig = Field(default_factory=ScannerConfig)

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


# risk.scale_with_budget: how the budget-sized settings follow from the per-side loss cap.
BUDGET_CHEAPEST_SIDE = Decimal("0.10")  # max_size lets a side this cheap still use its whole cap
BUDGET_EXPOSURE_FILLS = 4  # max_total_exposure: this many full fills held at once
BUDGET_SESSION_LOSS_FILLS = 2  # max_session_loss: this many worst-case fills


def budget_scaled(settings: Settings) -> Settings:
    """``settings`` with the budget-sized limits derived from risk.max_capital (if enabled).

    per side = max_capital x capital_utilization / (max_markets x 2), and from that:
    max_loss_per_fill = per side; max_size = max_position_per_market = per side / 0.10;
    order_group_contracts_limit = 2 x max_size; max_total_exposure = 4 x per side;
    max_session_loss = 2 x per side.
    """
    risk, quoting = settings.risk, settings.quoting
    if not risk.scale_with_budget or risk.max_capital is None:
        return settings
    markets = Decimal(settings.selection.max_markets)
    per_side = (risk.max_capital * quoting.capital_utilization / (markets * 2)).quantize(
        Decimal("0.01"), rounding=ROUND_FLOOR
    )
    size = (per_side / BUDGET_CHEAPEST_SIDE).to_integral_value(rounding=ROUND_CEILING)
    return settings.model_copy(
        update={
            "quoting": quoting.model_copy(update={"max_loss_per_fill": per_side, "max_size": size}),
            "risk": risk.model_copy(
                update={
                    "max_position_per_market": size,
                    "order_group_contracts_limit": int(size) * 2,
                    "max_total_exposure": per_side * BUDGET_EXPOSURE_FILLS,
                    "max_session_loss": per_side * BUDGET_SESSION_LOSS_FILLS,
                }
            ),
        }
    )


BUDGET_SCALED = (
    "quoting.max_loss_per_fill",
    "quoting.max_size",
    "risk.max_position_per_market",
    "risk.order_group_contracts_limit",
    "risk.max_total_exposure",
    "risk.max_session_loss",
)


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
    return budget_scaled(
        Settings.model_validate(_deep_merge(_deep_merge(raw, _env_api()), overrides or {}))
    )


def _env_api() -> dict[str, Any]:
    """Dashboard settings from the environment (e.g. on a server): host, port, password."""
    api: dict[str, Any] = {}
    if os.environ.get("KLP_API_HOST"):
        api["host"] = os.environ["KLP_API_HOST"]
    if os.environ.get("KLP_API_PORT"):
        api["port"] = int(os.environ["KLP_API_PORT"])
    if os.environ.get("KLP_DASHBOARD_PASSWORD"):
        api["password"] = os.environ["KLP_DASHBOARD_PASSWORD"]
    return {"api": api} if api else {}
