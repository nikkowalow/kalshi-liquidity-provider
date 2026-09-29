"""Command-line interface.

klp check     verify credentials and connectivity
klp markets   show which markets the selector would quote, and why
klp rewards   estimate $/day from live incentive programs at several order sizes
klp status    show positions and the bot's resting orders
klp run       run the bot (dry-run unless --live)
klp cancel    cancel the bot's resting orders (--all: every order on the account)
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys
from collections.abc import Callable, Coroutine
from decimal import Decimal
from typing import Any

from kalshi_lp import __version__
from kalshi_lp.config import ConfigError, Environment, Settings, load_settings
from kalshi_lp.engine.bot import LiquidityBot
from kalshi_lp.engine.executor import OrderExecutor
from kalshi_lp.engine.reconciler import Plan
from kalshi_lp.exchange.auth import KeyLoadError, Signer
from kalshi_lp.exchange.client import KalshiClient
from kalshi_lp.exchange.errors import KalshiError
from kalshi_lp.exchange.rate_limit import RateLimiter
from kalshi_lp.log import setup_logging
from kalshi_lp.strategy.estimator import RewardEstimator
from kalshi_lp.strategy.quoting import QuoteEngine
from kalshi_lp.strategy.selection import MarketSelector

log = logging.getLogger("kalshi_lp")

PUBLIC_READ_TOKENS_PER_SEC = 60  # ~6 requests/sec


def build_signer(settings: Settings, *, required: bool = True) -> Signer | None:
    """The request signer, or None for public-data commands when no key is configured."""
    if not required and not settings.has_credentials():
        return None
    creds = settings.credentials()
    try:
        return Signer.from_file(creds.key_id, creds.private_key_path)
    except KeyLoadError as exc:
        if required:
            raise ConfigError(str(exc)) from exc
        log.warning("%s; continuing without authentication", exc)
        return None


def build_client(settings: Settings, signer: Signer | None = None) -> KalshiClient:
    return KalshiClient(
        settings.api_url,
        signer if signer is not None else build_signer(settings),
        rate_limiter=RateLimiter(
            settings.rate_limits.read_per_sec, settings.rate_limits.write_per_sec
        ),
        subaccount=settings.subaccount,
    )


def build_selector(settings: Settings, client: KalshiClient) -> MarketSelector:
    engine = QuoteEngine(settings.quoting, settings.risk.max_position_per_market)
    return MarketSelector(client, settings.selection, settings.quoting, engine)


def public_client(settings: Settings) -> KalshiClient:
    """Client for commands that only read public market data (no API key needed)."""
    signer = build_signer(settings, required=False)
    if signer is None:
        log.info("no %s API key configured; using public endpoints", settings.environment.value)
    # Unauthenticated requests get a lower rate limit than a Basic key.
    read_rate = settings.rate_limits.read_per_sec if signer else PUBLIC_READ_TOKENS_PER_SEC
    return KalshiClient(
        settings.api_url,
        signer,
        rate_limiter=RateLimiter(read_rate, settings.rate_limits.write_per_sec),
    )


# ---------------------------------------------------------------- commands


async def cmd_check(settings: Settings, _: argparse.Namespace) -> int:
    async with build_client(settings) as client:
        status = await client.get_exchange_status()
        balance = await client.get_balance()
        positions = await client.get_positions()
        orders = await client.get_resting_orders()
    open_positions = [p for p in positions.values() if p.position != 0]
    print(f"environment     {settings.environment.value}  ({settings.api_url})")
    print(f"exchange        active={status.exchange_active} trading={status.trading_active}")
    print(f"balance         ${balance.balance:,.2f}")
    print(f"positions       {len(open_positions)} open")
    print(f"resting orders  {len(orders)}")
    print("OK: credentials and connectivity verified")
    return 0


async def cmd_markets(settings: Settings, _: argparse.Namespace) -> int:
    async with public_client(settings) as client:
        candidates = await build_selector(settings, client).select()
    if not candidates:
        print("No markets passed the selection filters.")
        return 1
    print(f"{'TICKER':<44} {'BID':>6} {'ASK':>6} {'VOL24H':>10} {'PROGRAM/DAY':>12} {'EST/DAY':>9}")
    for c in candidates:
        m = c.market
        print(
            f"{m.ticker:<44} {m.yes_bid or '-':>6} {m.yes_ask or '-':>6} "
            f"{m.volume_24h:>10.0f} {c.reward.reward_per_day:>12.2f} {c.est_daily_reward:>9.2f}"
        )
    return 0


async def cmd_status(settings: Settings, _: argparse.Namespace) -> int:
    async with build_client(settings) as client:
        balance = await client.get_balance()
        positions = await client.get_positions()
        orders = await client.get_resting_orders()
    prefix = f"{settings.client_order_prefix}-"
    print(f"balance ${balance.balance:,.2f}\n")
    print("POSITIONS")
    for p in positions.values():
        if p.position != 0:
            print(
                f"  {p.ticker:<44} {p.position:>8.2f}  cost ${p.market_exposure:,.2f}  "
                f"realized ${p.realized_pnl:,.2f}"
            )
    print("\nBOT ORDERS")
    for o in orders:
        if o.client_order_id.startswith(prefix):
            print(f"  {o.ticker:<44} {o.side.value:<4} {o.remaining:>8.2f} @ {o.yes_price}")
    return 0


async def cmd_rewards(settings: Settings, args: argparse.Namespace) -> int:
    sizes = [Decimal(s) for s in args.sizes.split(",")]
    async with public_client(settings) as client:
        estimator = RewardEstimator(client, build_selector(settings, client), settings.quoting)
        results = await estimator.estimate(sizes, samples=args.samples, interval=args.interval)
    if not results:
        print(f"No active liquidity programs in {settings.environment.value}.")
        if settings.environment is Environment.DEMO:
            print("Demo rarely has programs; try: klp rewards -c config/prod.yaml")
        return 1

    shown = results[: args.top]
    size_cols = "".join(f"{f'@{s.normalize():f}':>10}" for s in sizes)
    print(
        f"\n{'TICKER':<46}{'PROGRAM':>9}{'TARGET':>8}{'BOOK':>12}{'QUOTE':>12}"
        f"{'SHARE':>7}{size_cols}"
    )
    subheader = f"{'':<46}{'$/day':>9}{'':>8}{'bid/ask':>12}{'bid/ask':>12}{'':>7}"
    print(f"{subheader}  est. $/day at each order size")
    for r in shown:
        first = r.sizes[sizes[0]]
        book = f"{r.market.yes_bid or '-'}/{r.market.yes_ask or '-'}" if r.market.spread else "-"
        quote = f"{first.bid or '-'}/{first.ask or '-'}" if first.bid or first.ask else "-"
        cells = "".join(f"{r.sizes[s].daily:>10.2f}" for s in sizes)
        print(
            f"{r.ticker[:45]:<46}{r.reward.reward_per_day:>9.2f}"
            f"{r.reward.target_size.normalize():>8f}{_short(book):>12}{_short(quote):>12}"
            f"{first.share:>7.1%}{cells}"
        )
    totals = "".join(f"{sum((r.sizes[s].daily for r in shown), Decimal(0)):>10.2f}" for s in sizes)
    print(f"{f'TOTAL (top {len(shown)} of {len(results)})':<106}{totals}")
    print(
        "\nEstimates: quotes the bot would post, at the back of each queue, averaged over "
        f"{args.samples} book samples.\nThey ignore competitors reacting and losses from fills. "
        "Rewards need both sides quoted,\nso each order size means that many contracts on BOTH "
        "the bid and the ask."
    )
    return 0


def _short(pair: str) -> str:
    """0.4800/0.5200 -> .48/.52"""
    return (
        "/".join(p.rstrip("0").lstrip("0") or "0" for p in pair.split("/")) if "/" in pair else pair
    )


async def cmd_cancel(settings: Settings, args: argparse.Namespace) -> int:
    async with build_client(settings) as client:
        if args.all:
            await client.cancel_all_orders()
            print("Cancelled every resting order on the account.")
        else:
            executor = OrderExecutor(client, prefix=settings.client_order_prefix, dry_run=False)
            orders = [o for o in await client.get_resting_orders() if executor.is_ours(o)]
            await executor.execute(Plan(cancels=orders))
            print(f"Cancelled {len(orders)} bot orders.")
    return 0


async def cmd_run(settings: Settings, args: argparse.Namespace) -> int:
    signer = build_signer(settings)
    async with build_client(settings, signer) as client:
        bot = LiquidityBot(settings, client, signer=signer)
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, bot.stop)
        if args.duration:
            loop.call_later(args.duration, bot.stop)
        await bot.run()
        if bot.risk.halted:
            log.critical("bot halted: %s", bot.risk.halt_reason)
            return 2
    return 0


COMMANDS: dict[str, Callable[[Settings, argparse.Namespace], Coroutine[Any, Any, int]]] = {
    "check": cmd_check,
    "markets": cmd_markets,
    "rewards": cmd_rewards,
    "status": cmd_status,
    "run": cmd_run,
    "cancel": cmd_cancel,
}


# ------------------------------------------------------------------ parsing


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="klp", description="Kalshi Liquidity Incentive Program market-making bot."
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "-c", "--config", default="config/demo.yaml", help="YAML config (default: %(default)s)"
    )
    common.add_argument("-v", "--verbose", action="store_true", help="debug logging")

    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check", parents=[common], help="verify credentials and connectivity")
    sub.add_parser("markets", parents=[common], help="preview market selection")
    rewards = sub.add_parser(
        "rewards", parents=[common], help="estimate $/day from live incentive programs"
    )
    rewards.add_argument(
        "--sizes", default="10,50,100,250", help="order sizes to compare (default: %(default)s)"
    )
    rewards.add_argument(
        "--top", type=int, default=25, help="markets to show (default: %(default)s)"
    )
    rewards.add_argument("--samples", type=int, default=3, help="book samples to average")
    rewards.add_argument("--interval", type=float, default=2.0, help="seconds between samples")
    sub.add_parser("status", parents=[common], help="show positions and bot orders")

    run = sub.add_parser("run", parents=[common], help="run the bot")
    run.add_argument("--live", action="store_true", help="send real orders (overrides dry_run)")
    run.add_argument(
        "--confirm-prod",
        action="store_true",
        help="required to trade live in the production (real money) environment",
    )
    run.add_argument("--duration", type=float, default=None, help="stop after N seconds")

    cancel = sub.add_parser("cancel", parents=[common], help="cancel the bot's resting orders")
    cancel.add_argument(
        "--all", action="store_true", help="cancel EVERY resting order on the account"
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    overrides: dict[str, Any] = {}
    if getattr(args, "live", False):
        overrides["dry_run"] = False
    try:
        settings = load_settings(args.config, overrides, dotenv=True)
    except (ConfigError, ValueError) as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 1

    if (
        args.command == "run"
        and settings.environment is Environment.PROD
        and not settings.dry_run
        and not args.confirm_prod
    ):
        print(
            "Refusing to trade real money without --confirm-prod. "
            "Run in demo first, then pass --live --confirm-prod.",
            file=sys.stderr,
        )
        return 1

    level = "DEBUG" if args.verbose else settings.logging.level
    setup_logging(level, settings.logging.file, settings.logging.json_format)
    try:
        return asyncio.run(COMMANDS[args.command](settings, args))
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 1
    except KalshiError as exc:
        log.error("%s", exc)
        return 1
    except KeyboardInterrupt:
        return 130
