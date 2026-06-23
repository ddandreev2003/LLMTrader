"""QuantAgent LangGraph using text-only pattern/trend (no vision model)."""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph


def build_text_trading_graph(agent_llm, decision_llm, toolkit):
    """Full classic TA pipeline — no LLM (optional via QUANT_USE_LLM=true + LLM nodes)."""
    from agent_state import IndicatorAgentState
    from quant_agent.classic_analysts import create_classic_pattern_agent, create_classic_trend_agent
    from quant_agent.classic_decision import create_classic_decision_agent
    from quant_agent.classic_indicators import create_classic_indicator_agent

    use_llm = __import__("os").environ.get("QUANT_USE_LLM", "").strip().lower() in ("1", "true", "yes")
    if use_llm:
        from decision_agent import create_final_trade_decider
        from indicator_agent import create_indicator_agent

        indicator = create_indicator_agent(decision_llm, toolkit)
        decision = create_final_trade_decider(decision_llm)
    else:
        indicator = create_classic_indicator_agent()
        decision = create_classic_decision_agent()

    graph = StateGraph(IndicatorAgentState)

    nodes = {
        "Indicator Agent": indicator,
        "Pattern Agent": create_classic_pattern_agent(),
        "Trend Agent": create_classic_trend_agent(),
        "Decision Maker": decision,
    }
    for name, node in nodes.items():
        graph.add_node(name, node)

    graph.add_edge(START, "Indicator Agent")
    graph.add_edge("Indicator Agent", "Pattern Agent")
    graph.add_edge("Pattern Agent", "Trend Agent")
    graph.add_edge("Trend Agent", "Decision Maker")
    graph.add_edge("Decision Maker", END)
    return graph.compile()
