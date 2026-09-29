from __future__ import annotations

import pytest

from kalshi_lp.config import QuotingConfig, RiskConfig
from kalshi_lp.strategy.rewards import RewardParams
from tests.factories import D


@pytest.fixture
def quoting_cfg() -> QuotingConfig:
    return QuotingConfig(
        placement="reward",
        size=D(10),
        min_edge=D("0.005"),
        skew_per_contract=D("0.001"),
        max_skew=D("0.05"),
        min_price=D("0.03"),
        max_price=D("0.97"),
    )


@pytest.fixture
def risk_cfg() -> RiskConfig:
    return RiskConfig(
        max_position_per_market=D(50),
        max_total_exposure=D(100),
        max_session_loss=D(20),
        min_balance=D(10),
        fill_burst_contracts=D(30),
        fill_burst_window_seconds=60,
        cooldown_seconds=120,
        close_buffer_seconds=900,
        max_consecutive_errors=3,
    )


@pytest.fixture
def reward() -> RewardParams:
    return RewardParams(target_size=D(100), discount_factor=D("0.5"), reward_per_day=D(50))
