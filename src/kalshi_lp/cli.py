"""Command-line interface.

klp check     verify credentials and connectivity
klp markets   show which markets the selector would quote, and why
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
from typing import Any

from kalshi_lp import __version__
from kalshi_lp.config import ConfigError, Environment, Settings, load_settings
from kalshi_lp.engine.bot import LiquidityBot
from kalshi_lp.exchange.auth import Signer
from kalshi_lp.exchange.client import KalshiClient
from kalshi_lp.exchange.errors import KalshiError
from kalshi_lp.exchange.rate_limit import RateLimiter
from kalshi_lp.log import setup_logging

log = logging.getLogger("kalshi_lp")


def build_client(settings: Settings) -> KalshiClient:
    creds = settings.credentials()
    return KalshiClient(
        settings.api_url,
        Signer.from_file(creds.key_id, creds.private_key_path),
        rate_limiter=RateLimiter(
            settings.rate_limits.read_per_sec, settings.rate_limits.write_per_sec
        ),
        subaccount=settings.subaccount,
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
    async with build_client(settings) as client:
        bot = LiquidityBot(settings, client)
        candidates = await bot.selector.select()
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


async def cmd_cancel(settings: Settings, args: argparse.Namespace) -> int:
    async with build_client(settings) as client:
        if args.all:
            await client.cancel_all_orders()
            print("Cancelled every resting order on the account.")
        else:
            bot = LiquidityBot(settings, client)
            bot.executor.dry_run = False
            await bot.cancel_all_ours()
            print("Cancelled the bot's resting orders.")
    return 0


async def cmd_run(settings: Settings, args: argparse.Namespace) -> int:
    async with build_client(settings) as client:
        bot = LiquidityBot(settings, client)
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, bot.stop)
        await bot.run(max_cycles=args.cycles)
        if bot.risk.halted:
            log.critical("bot halted: %s", bot.risk.halt_reason)
            return 2
    return 0


COMMANDS: dict[str, Callable[[Settings, argparse.Namespace], Coroutine[Any, Any, int]]] = {
    "check": cmd_check,
    "markets": cmd_markets,
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
    sub.add_parser("status", parents=[common], help="show positions and bot orders")

    run = sub.add_parser("run", parents=[common], help="run the bot")
    run.add_argument("--live", action="store_true", help="send real orders (overrides dry_run)")
    run.add_argument(
        "--confirm-prod",
        action="store_true",
        help="required to trade live in the production (real money) environment",
    )
    run.add_argument("--cycles", type=int, default=None, help="stop after N cycles")

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
