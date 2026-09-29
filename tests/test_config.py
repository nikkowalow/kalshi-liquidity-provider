from pathlib import Path

import pytest

from kalshi_lp.config import API_URLS, ConfigError, Environment, load_settings

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
