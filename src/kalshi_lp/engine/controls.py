"""The dashboard's control buttons, as bot actions.

:mod:`kalshi_lp.api` exposes these as ``POST /api/control/<action>``. Each
returns one line for the dashboard to show, or raises :class:`ControlError`
when the request can't be carried out as asked.

    pause    cancel every bot order and quote nothing until resume
    resume   quote again
    flatten  close every position in the bot's markets now
    rescan   re-rank markets now instead of at the next scheduled scan
    budget   change risk.max_capital until the bot restarts
    stop     shut down as on Ctrl-C: cancel orders (positions stay open), exit
    config   save settings to the YAML config (see kalshi_lp.config_edit)
    restart  stop, re-read the config, start again (open positions carry over)
"""

from __future__ import annotations

import logging
from decimal import Decimal
from pathlib import Path
from typing import Any

from kalshi_lp import config_edit
from kalshi_lp.config import ConfigError
from kalshi_lp.engine.bot import LiquidityBot

log = logging.getLogger(__name__)

CENT = Decimal("0.01")


class ControlError(Exception):
    """A control request that can't be carried out (the message says why)."""


class BotControls:
    def __init__(self, bot: LiquidityBot, config_path: Path | None = None):
        self.bot = bot
        self.config_path = config_path  # the YAML the bot was started with (None: read-only)

    async def pause(self) -> str:
        if self.bot.held_since is not None:
            return "already paused"
        cancelled = await self.bot.hold()
        log.warning("paused from the dashboard: %d orders cancelled; quoting stops", cancelled)
        return f"paused: {cancelled} orders cancelled, nothing quoted until you resume"

    async def resume(self) -> str:
        if self.bot.held_since is None:
            return "not paused"
        self.bot.release()
        log.warning("resumed from the dashboard")
        return "resumed: quoting again"

    async def flatten(self) -> str:
        if self.bot.settings.dry_run:
            raise ControlError("dry run: there are no real positions to close")
        log.warning("closing all positions (asked from the dashboard)")
        found, left = await self.bot.close_positions()
        if left:
            return "could not close: " + ", ".join(f"{t} {p:+f}" for t, p in left.items())
        return f"closed {found} position(s)" if found else "no open positions"

    async def rescan(self) -> str:
        self.bot.request_rescan()
        return "re-ranking markets now; the result shows in the log and markets table"

    async def set_budget(self, max_capital: Decimal) -> str:
        if not max_capital.is_finite() or max_capital <= 0:
            raise ControlError("the budget must be more than $0")
        value = max_capital.quantize(CENT)
        balance = self.bot.state.balance
        if balance is not None:
            # A typo guard: more than cash plus what the bot has in play can't be used anyway.
            ceiling = balance + self.bot.capital_in_use()
            if value > ceiling:
                raise ControlError(
                    f"${value} is more than the account holds (${ceiling:.2f} cash and in use)"
                )
        old = self.bot.set_max_capital(value)
        before = f"${old}" if old is not None else "no limit"
        log.warning(
            "max_capital %s -> $%s (from the dashboard, until restart; to keep it, set "
            "risk.max_capital in the config)",
            before,
            value,
        )
        return f"budget {before} -> ${value} until restart (keep it: risk.max_capital in config)"

    async def stop(self) -> str:
        log.warning("stop requested from the dashboard")
        self.bot.stop()
        return (
            "stopping: cancelling orders (positions stay open). Start it again from the terminal."
        )

    def config(self) -> dict[str, Any]:
        """The editable settings: as the file has them, as the bot runs them, and their schema."""
        if self.config_path is None:
            raise ControlError("the bot wasn't started from a config file")
        return {
            "path": str(self.config_path),
            "schema": config_edit.schema(),
            "values": config_edit.values(self.config_path),
            "running": config_edit.sections(self.bot.settings),
        }

    async def set_config(self, changes: dict[str, Any]) -> str:
        if self.config_path is None:
            raise ControlError("the bot wasn't started from a config file")
        try:
            changed = config_edit.apply(self.config_path, changes)
        except ConfigError as exc:
            raise ControlError(str(exc)) from exc
        if not changed:
            return "nothing changed"
        log.warning(
            "config %s changed from the dashboard: %s", self.config_path, ", ".join(changed)
        )
        return f"saved {len(changed)} setting(s) to {self.config_path.name}; restart to apply"

    async def restart(self) -> str:
        if self.config_path is None:
            raise ControlError("the bot wasn't started from a config file")
        log.warning("restart requested from the dashboard")
        self.bot.restart_requested = True
        self.bot.stop()
        return "restarting with the saved config: back in a few seconds (open positions carry over)"
