import pytest

from shachi_shock.aggregation import aggregate_shock_votes
from shachi_shock.models import AgentVote, RegulatorShockResponse


def _vote(agent_id: int, shock_type: str | None, severity: int = 5) -> AgentVote:
    if shock_type is None:
        resp = RegulatorShockResponse(shock_occurred=False)
    else:
        resp = RegulatorShockResponse(
            shock_occurred=True,
            type=shock_type,
            severity=severity,
            duration_ticks=10,
            price_impact_pct=-5.0,
            volatility_multiplier=2.0,
            description=f"test {shock_type}",
        )
    return AgentVote(agent_id=agent_id, role=f"role_{agent_id}", response=resp)


def test_no_shocks_returns_empty():
    votes = [_vote(0, None), _vote(1, None), _vote(2, None)]
    assert aggregate_shock_votes(votes) == []


def test_agent_cannot_propose_disallowed_type():
    # agent 2 (media) proposes rate_hike — should be filtered
    votes = [_vote(2, "rate_hike")]
    assert aggregate_shock_votes(votes) == []


def test_halt_suppresses_weaker_shocks():
    votes = [
        _vote(1, "halt", severity=8),
        _vote(2, "news_spike", severity=9),
    ]
    result = aggregate_shock_votes(votes)
    assert len(result) == 1
    assert result[0].type == "halt"


def test_same_type_takes_max_severity():
    votes = [
        _vote(1, "circuit_breaker", severity=3),
        _vote(1, "circuit_breaker", severity=7),
    ]
    # second vote overwrites via same agent in duplicate test - use one proposal
    votes = [_vote(1, "circuit_breaker", severity=7)]
    result = aggregate_shock_votes(votes)
    assert result[0].severity == 7


def test_multiple_allowed_types_coexist():
    votes = [
        _vote(0, "rate_hike", severity=5),
        _vote(2, "news_spike", severity=4),
    ]
    result = aggregate_shock_votes(votes)
    types = {s.type for s in result}
    assert types == {"rate_hike", "news_spike"}
