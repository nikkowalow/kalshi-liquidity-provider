from decimal import Decimal
from pathlib import Path

import pytest

from kalshi_lp.config import API_URLS, ConfigError, Environment, Settings, load_settings

ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.parametrize("name", ["demo.yaml", "prod.yaml"])
def test_shipped_configs_are_valid(name: str) -> None:
    settings = load_settings(ROOT / "config" / name)
    assert settings.dry_run  # both ship in dry-run; --live is explicit


def test_demo_points_at_demo() -> None:
    settings = load_settings(ROOT / "config" / "demo.yaml")
    assert settings.environment is Environment.DEMO
    assert settings.api_url == API_URLS[Environment.DEMO]
    assert "demo" in settings.api_url


def test_overrides_and_validation(tmp_path: Path) -> None:
    cfg = tmp_path / "c.yaml"
    cfg.write_text("environment: demo\nquoting:\n  size: 3\n")
    settings = load_settings(cfg, {"dry_run": False})
    assert not settings.dry_run
    assert settings.quoting.size == 3

    cfg.write_text("quoting:\n  bogus_key: 1\n")
    with pytest.raises(ValueError):
        load_settings(cfg)


def test_credentials_are_environment_scoped(tmp_path: Path, monkeypatch) -> None:
    key = tmp_path / "demo.key"
    key.write_text("x")
    monkeypatch.setenv("KALSHI_DEMO_KEY_ID", "demo-id")
    monkeypatch.setenv("KALSHI_DEMO_PRIVATE_KEY_PATH", str(key))
    monkeypatch.delenv("KALSHI_PROD_KEY_ID", raising=False)
    monkeypatch.delenv("KALSHI_PROD_PRIVATE_KEY_PATH", raising=False)

    cfg = tmp_path / "c.yaml"
    cfg.write_text("environment: demo\n")
    assert load_settings(cfg).credentials().key_id == "demo-id"

    cfg.write_text("environment: prod\n")
    with pytest.raises(ConfigError, match="KALSHI_PROD_KEY_ID"):
        load_settings(cfg).credentials()


def test_budget_scaling_sizes_everything_from_the_budget() -> None:
    from kalshi_lp.config import budget_scaled

    base = {
        "selection": {"max_markets": 10},
        "quoting": {"capital_utilization": 0.95},
        "risk": {"max_capital": 500, "scale_with_budget": True, "max_session_loss": 3},
    }
    s = budget_scaled(Settings.model_validate(base))
    assert s.quoting.max_loss_per_fill == Decimal("23.75")  # 500 x 0.95 / (10 markets x 2)
    assert s.quoting.max_size == 238 and s.risk.max_position_per_market == 238
    assert s.risk.order_group_contracts_limit == 476
    assert s.risk.max_total_exposure == Decimal("95.00")
    assert s.risk.max_session_loss == Decimal("47.50")  # the file's 3 is ignored
    half = budget_scaled(
        Settings.model_validate({**base, "risk": {**base["risk"], "max_capital": 250}})
    )
    assert half.quoting.max_loss_per_fill == Decimal("11.87")
    off = Settings.model_validate({**base, "risk": {"max_capital": 500, "max_session_loss": 3}})
    assert budget_scaled(off) == off  # scaling off: the file's numbers stand


def test_budget_can_be_split_over_fewer_markets() -> None:
    from kalshi_lp.config import budget_scaled

    s = Settings.model_validate(
        {
            "selection": {"max_markets": 10},
            "quoting": {"capital_utilization": 0.95},
            "risk": {"max_capital": 500, "scale_with_budget": True, "budget_min_markets": 3},
        }
    )
    assert budget_scaled(s, 5).quoting.max_loss_per_fill == Decimal("47.50")  # 500 x .95 / 10
    assert budget_scaled(s, 5).risk.max_session_loss == Decimal("95.00")
    assert budget_scaled(s, 1).quoting.max_loss_per_fill == Decimal("79.16")  # floor: 3 markets
    assert budget_scaled(s, 40).quoting.max_loss_per_fill == Decimal("23.75")  # at most 10
