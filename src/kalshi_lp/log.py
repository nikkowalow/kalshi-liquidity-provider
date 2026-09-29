from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            entry["exc"] = self.formatException(record.exc_info)
        return json.dumps(entry)


def setup_logging(level: str = "INFO", file: Path | None = None, json_format: bool = False) -> None:
    formatter: logging.Formatter = (
        _JsonFormatter()
        if json_format
        else logging.Formatter("%(asctime)s %(levelname)-7s %(name)-24s %(message)s")
    )
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    if file is not None:
        file.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(file))
    for handler in handlers:
        handler.setFormatter(formatter)
    logging.basicConfig(level=level.upper(), handlers=handlers, force=True)
    logging.getLogger("httpx").setLevel(logging.WARNING)
