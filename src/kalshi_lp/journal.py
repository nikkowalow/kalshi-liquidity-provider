"""Run journal: everything a bot run does, on disk, for the live dashboard.

Each run gets its own directory under ``runs/``:

    runs/20260929-153621-prod-live/
        events.jsonl   append-only: orders, fills, quote changes, selections, logs
        state.json     latest full snapshot, rewritten about once a second

Both are plain files so anything can read them while the bot runs: the
dashboard (``dashboard/server.py``), ``tail -f``, or ``jq``.
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, TextIO


def _default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    return str(value)


class RunJournal:
    def __init__(self, root: Path, environment: str, mode: str):
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        self.run_id = f"{stamp}-{environment}-{mode}"
        self.dir = Path(root) / self.run_id
        self.dir.mkdir(parents=True, exist_ok=True)
        self._events: TextIO | None = (self.dir / "events.jsonl").open("a", buffering=1)
        self._state_path = self.dir / "state.json"

    def event(self, kind: str, **data: Any) -> None:
        if self._events is None:
            return
        record = {"ts": time.time(), "type": kind, **data}
        self._events.write(json.dumps(record, default=_default) + "\n")

    def listener(self, kind: str, data: dict[str, Any]) -> None:
        """Adapter for :attr:`MarketState.listeners`."""
        self.event(kind, **data)

    def write_state(self, state: dict[str, Any]) -> None:
        tmp = self._state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, default=_default))
        os.replace(tmp, self._state_path)  # atomic: readers never see a partial file

    def close(self) -> None:
        if self._events is not None:
            self._events.close()
            self._events = None


class NullJournal(RunJournal):
    """Journal that records nothing (tests, one-off commands)."""

    def __init__(self) -> None:
        self.run_id = "none"
        self._events = None

    def write_state(self, state: dict[str, Any]) -> None:
        pass


class JournalLogHandler(logging.Handler):
    """Mirrors the bot's log lines into the journal as ``log`` events."""

    def __init__(self, journal: RunJournal, level: int = logging.INFO):
        super().__init__(level)
        self.journal = journal

    def emit(self, record: logging.LogRecord) -> None:
        if not record.name.startswith("kalshi_lp"):
            return
        try:
            self.journal.event(
                "log", level=record.levelname, logger=record.name, msg=record.getMessage()
            )
        except Exception:
            self.handleError(record)
