from shachi_shock.models import (
    AGENT_ALLOWED_SHOCKS,
    SHOCK_PRIORITY,
    AcceptedShock,
    AgentVote,
    RegulatorShockResponse,
)


def _to_accepted(response: RegulatorShockResponse, agent_id: int) -> AcceptedShock | None:
    if not response.shock_occurred or response.type is None:
        return None
    allowed = AGENT_ALLOWED_SHOCKS.get(agent_id, set())
    if response.type not in allowed:
        return None
    return AcceptedShock(
        type=response.type,
        severity=response.severity,
        duration_ticks=response.duration_ticks,
        price_impact_pct=response.price_impact_pct,
        volatility_multiplier=response.volatility_multiplier,
        description=response.description or response.rationale,
        proposed_by=[agent_id],
    )


def aggregate_shock_votes(votes: list[AgentVote]) -> list[AcceptedShock]:
    """Aggregate regulator proposals with priority and de-duplication."""
    proposals: list[AcceptedShock] = []
    for vote in votes:
        accepted = _to_accepted(vote.response, vote.agent_id)
        if accepted is not None:
            proposals.append(accepted)

    if not proposals:
        return []

    proposals.sort(key=lambda s: SHOCK_PRIORITY.get(s.type, 0), reverse=True)

    by_type: dict[str, AcceptedShock] = {}
    for proposal in proposals:
        existing = by_type.get(proposal.type)
        if existing is None:
            by_type[proposal.type] = proposal
            continue
        if proposal.severity > existing.severity:
            by_type[proposal.type] = proposal
        existing.proposed_by.extend(proposal.proposed_by)

    result = list(by_type.values())
    result.sort(key=lambda s: SHOCK_PRIORITY.get(s.type, 0), reverse=True)

    # halt/circuit_breaker dominate — suppress weaker concurrent shocks
    top_priority = SHOCK_PRIORITY.get(result[0].type, 0)
    if top_priority >= SHOCK_PRIORITY["circuit_breaker"]:
        return [result[0]]

    return result
