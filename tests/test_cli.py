import argparse

import pytest

from kalshi_lp import cli
from kalshi_lp.config import Settings
from tests.factories import make_market
from tests.fake_exchange import FakeExchange
from tests.test_selection import BOOK, program, sweep_trades


class _Session:
    def __init__(self, exchange: FakeExchange) -> None:
        self.exchange = exchange

    async def __aenter__(self) -> FakeExchange:
        return self.exchange

    async def __aexit__(self, *exc: object) -> None:
        pass


async def test_markets_command_shows_fill_risk(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    ex = FakeExchange(
        [make_market("CALM-1"), make_market("BUSY-1")], {"CALM-1": BOOK, "BUSY-1": BOOK}
    )
    ex.programs = [program("CALM-1", 100), program("BUSY-1", 100)]
    ex.trades["BUSY-1"] = sweep_trades("BUSY-1", n=2, volume=5000)
    monkeypatch.setattr(cli, "public_client", lambda settings: _Session(ex))
    settings = Settings.model_validate({"selection": {"mode": "incentives", "max_markets": 5}})
    assert await cli.cmd_markets(settings, argparse.Namespace()) == 0
    out = capsys.readouterr().out
    assert "FILLS/DAY" in out and "NET/DAY" in out
    rows = {line.split()[0]: line.split() for line in out.splitlines() if "-1 " in line}
    assert rows["CALM-1"][-3] == "0.0"  # no sweeps: no fills
    assert float(rows["BUSY-1"][-3]) > 0  # two sweeps through our quote


async def test_a_restart_rereads_the_config_and_runs_again(tmp_path, monkeypatch) -> None:
    config = tmp_path / "bot.yaml"
    config.write_text("risk:\n  max_capital: 100\n")
    runs: list[object] = []

    async def run_once(settings: Settings, args: argparse.Namespace) -> tuple[int, bool]:
        runs.append(settings.risk.max_capital)
        if len(runs) == 1:
            config.write_text("risk:\n  max_capital: 300\n")  # saved from the dashboard
            return 0, True  # ...then "save & restart"
        return 0, False

    monkeypatch.setattr(cli, "_run_once", run_once)
    args = argparse.Namespace(config=str(config), overrides={"dry_run": True})
    first = cli.load_settings(config)
    assert await cli.cmd_run(first, args) == 0
    assert [str(c) for c in runs] == ["100", "300"]
