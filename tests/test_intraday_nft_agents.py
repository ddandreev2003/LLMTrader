"""NFT intraday agent factory tests."""

from pathlib import Path

import pytest

from agents.coordinator_agent import create_intraday_agents_from_config
from core.event_bus import EventBus


ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def bus():
    return EventBus()


def test_nft_config_creates_four_portfolio_agents(bus):
    agents, coordinator = create_intraday_agents_from_config(
        bus,
        ROOT / "config/strategies_intraday_nft.yaml",
        portfolio_cfg={"portfolio": {"initial_cash_rub": 1_000_000}},
    )
    assert coordinator is None
    assert len(agents) == 4
    for ag in agents:
        assert ag.universe == ["T", "SBER"]
        assert ag.signal_mode is True
        assert getattr(ag, "display_name", ag.agent_id)


def test_nft_display_names(bus):
    agents, _ = create_intraday_agents_from_config(
        bus,
        ROOT / "config/strategies_intraday_nft.yaml",
    )
    names = {ag.agent_id: ag.display_name for ag in agents}
    assert names["crypto_punks"] == "CryptoPunks"
    assert names["bored_ape"] == "Bored Ape Yacht Club"
    assert names["azuki"] == "Azuki"
    assert names["pudgy_penguins"] == "Pudgy Penguins"


def test_tls_config_still_has_coordinator(bus):
    agents, coordinator = create_intraday_agents_from_config(
        bus,
        ROOT / "config/strategies_intraday_tls.yaml",
    )
    assert len(agents) == 2
    assert coordinator is not None
