"""Edit the YAML config from the dashboard, keeping the file as you wrote it.

Only the lines of the settings that change are touched: a new value replaces the
old one on its line and the comment after it stays. A multi-line list keeps its
item lines (and their comments) for the items still in it, drops the ones taken
out, and adds new ones at its end. A setting the file doesn't mention yet is
added at the end of its section. The result is validated as a whole before the
file is replaced, and the previous version is kept next to it (``.bak``).

Some settings can't be changed this way: the environment, live/dry-run, the
account and journal, and the dashboard's own server (a typo there would lock
the dashboard out). Edit those in the file.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml
from ruamel.yaml import YAML

from kalshi_lp.config import BUDGET_SCALED, ConfigError, Settings, budget_scaled

EDITABLE_SECTIONS = ("loop", "rate_limits", "selection", "quoting", "risk", "scanner")

_PLAIN = re.compile(r"^[A-Za-z_][A-Za-z0-9_.\-/]*$")
_RESERVED = {"null", "true", "false", "yes", "no", "on", "off", "~"}


def schema() -> dict[str, Any]:
    """JSON schema of the editable sections: types, bounds, defaults and descriptions."""
    full = Settings.model_json_schema()
    defs = full.get("$defs", {})
    sections = {}
    for name in EDITABLE_SECTIONS:
        ref = full["properties"][name].get("$ref") or full["properties"][name]["allOf"][0]["$ref"]
        sections[name] = defs[ref.rsplit("/", 1)[-1]]
    return {"sections": sections, "order": list(EDITABLE_SECTIONS)}


def values(path: Path) -> dict[str, Any]:
    """Every editable setting as the file sets it (defaults for what it leaves out)."""
    settings = Settings.model_validate(yaml.safe_load(path.read_text()) or {})
    return sections(settings)


def derived(path: Path) -> dict[str, Any]:
    """Settings the budget sets (risk.scale_with_budget), by "section.setting": their values."""
    settings = Settings.model_validate(yaml.safe_load(path.read_text()) or {})
    if not settings.risk.scale_with_budget or settings.risk.max_capital is None:
        return {}
    scaled = sections(budget_scaled(settings))
    return {key: scaled[key.split(".")[0]][key.split(".")[1]] for key in BUDGET_SCALED}


def sections(settings: Settings) -> dict[str, Any]:
    """The editable sections of ``settings`` as plain JSON values (decimals as numbers)."""
    dumped = settings.model_dump(mode="python")
    return {name: _plain(dumped[name]) for name in EDITABLE_SECTIONS}


def _plain(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_plain(v) for v in value]
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, Path):
        return str(value)
    return value


def _scalar(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        text = repr(value)
        return text[:-2] if text.endswith(".0") else text
    text = str(value)
    if _PLAIN.match(text) and text.lower() not in _RESERVED:
        return text
    return json.dumps(text)


def _inline(value: Any) -> str:
    if isinstance(value, list):
        return "[" + ", ".join(_scalar(v) for v in value) + "]"
    return _scalar(value)


def _value_end(line: str, start: int) -> int:
    """Where a plain scalar ends on its line: before the comment, or at the end."""
    comment = line.find(" #", start)
    end = comment if comment >= 0 else len(line.rstrip("\n"))
    return len(line[:end].rstrip())


def _list_end(lines: list[str], first: int, col: int) -> int:
    """The line where a flow list starting at ``lines[first][col]`` closes."""
    depth = 0
    for i in range(first, len(lines)):
        segment = (lines[i][col:] if i == first else lines[i]).split(" #", 1)[0]
        depth += segment.count("[") - segment.count("]")
        if depth <= 0:
            return i
    return len(lines) - 1


def _replace_list(lines: list[str], first: int, last: int, col: int, new: list[Any]) -> list[str]:
    """Rewrite a flow list, keeping the lines (and comments) of the items that stay."""
    if first == last:  # one line: rewrite the value in place
        line = lines[first]
        return [line[:col] + _inline(new) + line[_value_end(line, col) :]]
    wanted = [str(v) for v in new]
    kept, seen = [lines[first]], set()
    indent = " " * (col + 2)
    for line in lines[first + 1 : last]:
        item = line.split("#", 1)[0].strip().rstrip(",").strip()
        if not item:
            kept.append(line)  # a comment or blank line
            continue
        indent = line[: len(line) - len(line.lstrip())]
        if item in wanted and item not in seen:
            seen.add(item)
            kept.append(line)
    kept += [f"{indent}{_scalar(v)},\n" for v in wanted if v not in seen]
    kept.append(lines[last])
    return kept


def edit(text: str, changes: Mapping[str, Any]) -> str:
    """``text`` with ``changes`` ("section.setting" -> value) applied line by line."""
    lines = text.splitlines(keepends=True)
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"
    for key, value in changes.items():
        section, _, name = key.partition(".")
        if section not in EDITABLE_SECTIONS or not name:
            raise ConfigError(f"{key} can't be changed from the dashboard")
        doc = YAML().load("".join(lines)) or {}
        block = doc.get(section)
        if block is not None and name in block:
            _, _, line_no, col = block.lc.data[name]
            if lines[line_no][col : col + 1] == "[":
                last = _list_end(lines, line_no, col)
                new = value if isinstance(value, list) else [value]
                lines[line_no : last + 1] = _replace_list(lines, line_no, last, col, new)
            else:
                line = lines[line_no]
                if col >= len(line.rstrip("\n")):  # value on the next lines (a block list)
                    raise ConfigError(f"{key}: edit block-style values in the file")
                lines[line_no] = line[:col] + _inline(value) + line[_value_end(line, col) :]
        elif block is not None:  # add it as the section's last setting
            last = max(v[2] for v in block.lc.data.values())
            while last + 1 < len(lines) and lines[last + 1].startswith((" ", "\t")):
                last += 1
            lines.insert(last + 1, f"  {name}: {_inline(value)}\n")
        else:
            lines.append(f"\n{section}:\n  {name}: {_inline(value)}\n")
    return "".join(lines)


def apply(path: Path, changes: Mapping[str, Any]) -> list[str]:
    """Write ``changes`` to the config file if the result is valid; the keys that changed."""
    current = values(path)
    changed = {
        k: v
        for k, v in changes.items()
        if current.get(k.partition(".")[0], {}).get(k.partition(".")[2], object()) != v
    }
    if not changed:
        return []
    text = edit(path.read_text(), changed)
    try:
        Settings.model_validate(yaml.safe_load(text) or {})
    except (ValueError, yaml.YAMLError) as exc:
        raise ConfigError(str(exc)) from exc
    backup = path.with_suffix(path.suffix + ".bak")
    backup.write_text(path.read_text())
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text)
    os.replace(tmp, path)
    return sorted(changed)
