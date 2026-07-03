"""Reusable single-run entry point for the SRE repair loop.

Shared by the CLI (`sre-agent run`) and the eval runner so both execute the loop
identically and produce the same per-run artifacts.
"""

from __future__ import annotations

import time
from pathlib import Path

from sre_agent.config import Settings
from sre_agent.graph import build_graph
from sre_agent.llm import LLM
from sre_agent.observability import RunLogger, new_trace_id
from sre_agent.reports import build_report
from sre_agent.state import AgentState
from sre_agent.tools import Tools


def run_loop(settings: Settings, scenario: str | None) -> AgentState:
    """Run the loop once, write all artifacts, and return the final AgentState."""
    trace_id = new_trace_id(scenario)
    logger = RunLogger(settings.runs_dir, trace_id, settings.log_level)
    tools = Tools(settings, logger)
    graph = build_graph(settings, tools, LLM(settings), logger)

    init = AgentState(
        trace_id=trace_id,
        goal="Restore the sre-lab workload to a healthy, validated state with the "
             "smallest safe change.",
        mode=settings.mode.value,
        scenario=scenario,
    )

    start = time.time()
    final_dict = graph.invoke(init, config={"recursion_limit": 50})
    elapsed = time.time() - start

    final = AgentState.model_validate(final_dict)
    final.elapsed_seconds = round(elapsed, 2)
    final.tool_call_count = tools.calls

    report_md = build_report(final, settings)
    logger.write_text("report.md", report_md)
    terminal = final.terminal_state.value if final.terminal_state else None
    logger.write_json("meta.json", {
        "trace_id": trace_id, "scenario": scenario, "mode": settings.mode.value,
        "model": settings.model, "terminal_state": terminal,
        "eval_score": final.eval_score, "tool_calls": tools.calls,
        "elapsed_seconds": final.elapsed_seconds, "incident": final.incident.value,
    })
    reports_dir = Path("reports")
    reports_dir.mkdir(exist_ok=True)
    (reports_dir / f"{trace_id}.md").write_text(report_md)
    return final
