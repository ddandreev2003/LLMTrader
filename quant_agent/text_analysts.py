"""Pattern/trend graph nodes — classic TA only (re-export for compatibility)."""

from quant_agent.classic_analysts import (
    analyze_patterns,
    analyze_trend,
    create_classic_pattern_agent,
    create_classic_trend_agent,
)

create_text_pattern_agent = create_classic_pattern_agent
create_text_trend_agent = create_classic_trend_agent

__all__ = [
    "analyze_patterns",
    "analyze_trend",
    "create_classic_pattern_agent",
    "create_classic_trend_agent",
    "create_text_pattern_agent",
    "create_text_trend_agent",
]
