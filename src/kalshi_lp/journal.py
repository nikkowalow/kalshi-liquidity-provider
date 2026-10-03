"""Run journal: everything the bot does, on disk, for the live dashboard.

The journal is persistent: every run in the same environment and mode appends
to the same directory, so stopping and restarting the bot (say, after a code
or config change) continues one history instead of starting a new one:

    runs/prod-live/
        events.jsonl   append-only: orders, fills, quote changes, selections, logs
        metrics.jsonl  append-only: the totals every 10 seconds (the dashboard charts)
        state.json     latest full snapshot, rewritten about once a second
        scan.json      the market scanner's latest report (every rewarded market's $/day)

``state.json`` also carries the reward ledger (per-market estimated earnings)
and counters; a restarted bot reads them back and carries on from there.

They are plain files, so ``tail -f`` and ``jq`` work while the bot runs. The
dashboard doesn't read them: the bot's API (:mod:`kalshi_lp.api`) subscribes
to the journal and pushes each event and snapshot to it as they happen.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import shutil
import time
from collections.abc import Callable
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, TextIO

log = logging.getLogger(__name__)

# Live listener: called with ("event", <JSON line>), ("state", <JSON snapshot>) or
# ("scan", <JSON scanner report>).
Subscriber = Callable[[str, str], None]

# Per-run directories written by older versions: 20260929-212257-prod-live
_LEGACY_RUN = re.compile(r"^\d{8}-\d{6}-(?P<env>[a-z]+)-(?P<mode>[a-z]+)$")
_LEGACY_ARCHIVE = "_imported"
_STALE_AFTER = 60.0  # seconds without a state write before a legacy run counts as finished


def _default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    return str(value)


def _dumps(record: dict[str, Any]) -> str:
    return json.dumps(record, default=_default)


def _read_state(path: Path) -> dict[str, Any]:
    with contextlib.suppress(OSError, ValueError):
        data = json.loads(path.read_text())
        if isinstance(data, dict):
            return data
    return {}


class RunJournal:
    """Journal for one environment + mode (``runs/prod-live``), shared by every run."""

    def __init__(self, root: Path, environment: str, mode: str, name: str | None = None):
        self.run_id = name or f"{environment}-{mode}"
        self.dir = Path(root) / self.run_id
        fresh = not (self.dir / "state.json").exists()
        self.dir.mkdir(parents=True, exist_ok=True)
        if fresh and name is None:
            import_legacy_runs(Path(root), self.dir, environment, mode)
        # What earlier runs left behind: the reward ledger, counters, session count.
        self.previous: dict[str, Any] = _read_state(self.dir / "state.json")
        if self.previous and "rewards_unattributed" not in self.previous and name is None:
            # Journals imported before this field existed: recompute it from the archive.
            archived = _legacy_dirs(Path(root) / _LEGACY_ARCHIVE, environment, mode)
            self.previous["rewards_unattributed"] = _unattributed(archived)
        self.session = int(self.previous.get("session") or 0) + 1
        self._events: TextIO | None = (self.dir / "events.jsonl").open("a", buffering=1)
        self._metrics: TextIO | None = (self.dir / "metrics.jsonl").open("a", buffering=1)
        self._state_path = self.dir / "state.json"
        self.subscribers: list[Subscriber] = []

    def event(self, kind: str, **data: Any) -> None:
        if self._events is None:
            return
        line = _dumps({"ts": time.time(), "type": kind, **data})
        self._events.write(line + "\n")
        if kind == "metrics" and self._metrics is not None:
            self._metrics.write(line + "\n")
        for subscriber in self.subscribers:
            subscriber("event", line)

    def listener(self, kind: str, data: dict[str, Any]) -> None:
        """Adapter for :attr:`MarketState.listeners`."""
        self.event(kind, **data)

    def write_state(self, state: dict[str, Any]) -> None:
        text = json.dumps(state, default=_default)
        tmp = self._state_path.with_suffix(".tmp")
        tmp.write_text(text)
        os.replace(tmp, self._state_path)  # atomic: readers never see a partial file
        for subscriber in self.subscribers:
            subscriber("state", text)

    def write_scan(self, report: dict[str, Any]) -> None:
        """The market scanner's latest report (``scan.json``, replaced each scan)."""
        text = json.dumps(report, default=_default)
        tmp = self.dir / "scan.tmp"
        tmp.write_text(text)
        os.replace(tmp, self.dir / "scan.json")
        for subscriber in self.subscribers:
            subscriber("scan", text)

    def close(self) -> None:
        for f in (self._events, self._metrics):
            if f is not None:
                f.close()
        self._events = self._metrics = None


class NullJournal(RunJournal):
    """Journal that records nothing (tests, one-off commands)."""

    def __init__(self) -> None:
        self.run_id = "none"
        self.previous = {}
        self.session = 1
        self._events = self._metrics = None
        self.subscribers = []

    def write_state(self, state: dict[str, Any]) -> None:
        pass

    def write_scan(self, report: dict[str, Any]) -> None:
        pass


def import_legacy_runs(root: Path, target: Path, environment: str, mode: str) -> int:
    """Fold the old one-directory-per-run journals into ``target``, oldest first.

    Events and metrics are concatenated (the rewards total in each metrics
    line is made cumulative across runs), per-market reward estimates are
    summed into the ledger, and the old directories are moved to
    ``runs/_imported``. Returns the number of runs imported.
    """
    now = time.time()
    legacy = []
    for path in _legacy_dirs(root, environment, mode):
        state_file = path / "state.json"
        running = _read_state(state_file).get("status") == "running"
        if running and now - state_file.stat().st_mtime < _STALE_AFTER:
            log.warning("not importing %s: it looks like it is still running", path.name)
            continue
        legacy.append(path)
    if not legacy:
        return 0

    ledger: dict[str, dict[str, Any]] = {}
    carried = 0.0  # rewards earned by the runs already imported
    fills = requotes = 0
    first_started: float | None = None
    with (
        (target / "events.jsonl").open("a") as events,
        (target / "metrics.jsonl").open("a") as metrics,
    ):
        for path in legacy:
            state = _read_state(path / "state.json")
            with contextlib.suppress(OSError):
                for line in (path / "events.jsonl").read_text().splitlines():
                    try:
                        record = json.loads(line)
                    except ValueError:
                        continue
                    if record.get("type") == "fill":
                        fills += 1
                    if record.get("type") == "metrics":
                        record["rewards_earned"] = carried + (record.get("rewards_earned") or 0)
                        metrics.write(_dumps(record) + "\n")
                    events.write(_dumps(record) + "\n")
            for row in state.get("markets", []):
                entry = ledger.setdefault(
                    row["ticker"],
                    {"title": "", "earned": 0.0, "snapshots": 0, "scored": 0, "score_sum": 0.0},
                )
                snapshots = int(row.get("snapshots") or 0)
                entry["title"] = row.get("title") or entry["title"]
                entry["earned"] += row.get("earned") or 0
                entry["snapshots"] += snapshots
                entry["scored"] += int(row.get("paying_snapshots") or 0)
                entry["score_sum"] += (row.get("avg_score") or 0) * snapshots
                if row.get("earned"):
                    entry["last_at"] = state.get("updated_at")
            totals = state.get("totals", {})
            carried += totals.get("rewards_earned") or 0
            requotes += int(totals.get("requotes") or 0)
            if first_started is None:
                first_started = state.get("started_at")

    last = _read_state(legacy[-1] / "state.json")
    merged = {
        **last,
        "run_id": target.name,
        "status": "ended",
        "session": len(legacy),
        "first_started_at": first_started,
        "ledger": ledger,
        "rewards_unattributed": _unattributed(legacy),
        "fills_total": fills,
        "requotes_total": requotes,
        "markets": [],
        "orders": [],
    }
    (target / "state.json").write_text(json.dumps(merged, default=_default))

    archive = root / _LEGACY_ARCHIVE
    archive.mkdir(exist_ok=True)
    for path in legacy:
        shutil.move(str(path), str(archive / path.name))
    log.info(
        "imported %d earlier %s-%s runs into %s (originals moved to %s)",
        len(legacy),
        environment,
        mode,
        target,
        archive,
    )
    return len(legacy)


def _legacy_dirs(root: Path, environment: str, mode: str) -> list[Path]:
    if not root.is_dir():
        return []
    return [
        path
        for path in sorted(root.iterdir())
        if path.is_dir()
        and (m := _LEGACY_RUN.match(path.name))
        and m["env"] == environment
        and m["mode"] == mode
    ]


def _unattributed(runs: list[Path]) -> float:
    """Rewards old runs counted in their total but not under any market.

    Older snapshots listed only the markets selected when the run ended, so the
    earnings of markets dropped mid-run survive only in the run's total.
    """
    missing = 0.0
    for path in runs:
        state = _read_state(path / "state.json")
        total = state.get("totals", {}).get("rewards_earned") or 0
        by_market = sum(row.get("earned") or 0 for row in state.get("markets", []))
        missing += max(total - by_market, 0.0)
    return missing


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
