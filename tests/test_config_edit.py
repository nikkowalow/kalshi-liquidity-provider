from pathlib import Path

import pytest

from kalshi_lp import config_edit
from kalshi_lp.config import ConfigError

CONFIG = """\
environment: demo
dry_run: true

selection:
  max_markets: 10 # how many markets
  max_fills_per_day: null # off
  exclude_tickers: []
  exclude_series: [
      # jumpy
      KXQUAKE, # lost $7
      KXRAIN,
    ]

risk:
  max_capital: 250 # the budget
"""


@pytest.fixture
def config(tmp_path) -> Path:
    path = tmp_path / "prod.yaml"
    path.write_text(CONFIG)
    return path


def test_only_changed_lines_change_and_comments_stay(config: Path) -> None:
    changed = config_edit.apply(
        config,
        {"risk.max_capital": 300, "selection.max_markets": 10, "selection.max_fills_per_day": 5},
    )
    assert changed == ["risk.max_capital", "selection.max_fills_per_day"]  # 10 was already 10
    text = config.read_text()
    assert "  max_capital: 300 # the budget\n" in text
    assert "  max_fills_per_day: 5 # off\n" in text
    assert text.replace("300", "250").replace(": 5 #", ": null #") == CONFIG
    assert config.with_suffix(".yaml.bak").read_text() == CONFIG  # the previous version


def test_lists_keep_the_lines_and_comments_of_items_that_stay(config: Path) -> None:
    config_edit.apply(
        config,
        {
            "selection.exclude_series": ["KXQUAKE", "KXNEW"],
            "selection.exclude_tickers": ["ABC-1"],
        },
    )
    text = config.read_text()
    assert "      # jumpy\n      KXQUAKE, # lost $7\n      KXNEW,\n    ]\n" in text
    assert "KXRAIN" not in text
    assert "  exclude_tickers: [ABC-1]\n" in text
    assert config_edit.values(config)["selection"]["exclude_series"] == ["KXQUAKE", "KXNEW"]


def test_settings_the_file_leaves_out_are_added_to_their_section(config: Path) -> None:
    config_edit.apply(config, {"risk.unwind_stop": 0.04, "scanner.size": 12})
    values = config_edit.values(config)
    assert values["risk"]["unwind_stop"] == 0.04
    assert values["scanner"]["size"] == 12
    assert "  max_capital: 250 # the budget\n  unwind_stop: 0.04\n" in config.read_text()


def test_invalid_or_locked_settings_are_refused_and_nothing_is_written(config: Path) -> None:
    with pytest.raises(ConfigError):
        config_edit.apply(config, {"risk.max_capital": -5})
    with pytest.raises(ConfigError, match="dry_run"):
        config_edit.apply(config, {"dry_run": False})
    with pytest.raises(ConfigError, match="api"):
        config_edit.apply(config, {"api.port": 1})
    assert config.read_text() == CONFIG


def test_unchanged_decimals_are_not_rewritten(config: Path) -> None:
    assert config_edit.apply(config, {"risk.max_capital": 250.0}) == []
    assert config.read_text() == CONFIG


def test_schema_describes_every_editable_section() -> None:
    schema = config_edit.schema()
    assert schema["order"] == list(config_edit.EDITABLE_SECTIONS)
    risk = schema["sections"]["risk"]["properties"]
    assert "description" in risk["max_capital"] and "exit_mode" in risk
