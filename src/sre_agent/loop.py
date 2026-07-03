"""Reusable single-run entry points for the repair loop and the on-call loop.

Shared by the CLI and the eval runner so every path executes identically and produces
the same per-run artifacts under runs/<trace_id>/.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from sre_agent.config import Settings
from sre_agent.graph import build_graph
from sre_agent.llm import LLM
from sre_agent.memory import Memory
from sre_agent.observability import RunLogger, new_trace_id
from sre_agent.oncall import build_oncall_graph
from sre_agent.reports import build_report
from sre_agent.state import AgentState, Alert
from sre_agent.tools import Tools

_GOAL_REPAIR = ("Restore the sre-lab workload to a healthy, validated state with the "
                "smallest safe change.")
_GOAL_ONCALL = ("Triage the alert and mitigate the incident (stop the bleeding) within "
                "sre-lab, then communicate and open follow-ups.")


def _finalize(final: AgentState, settings: Settings, logger: RunLogger, tools: Tools,
              elapsed: float) -> AgentState:
    final.elapsed_seconds = round(elapsed, 2)
    final.tool_call_count = tools.calls
    report_md = build_report(final, settings)
    logger.write_text("report.md", report_md)
    terminal = final.terminal_state.value if final.terminal_state else None
    logger.write_json("meta.json", {
        "trace_id": final.trace_id, "scenario": final.scenario, "mode": settings.mode.value,
        "model": settings.model, "terminal_state": terminal,
        "eval_score": final.eval_score, "tool_calls": tools.calls,
        "elapsed_seconds": final.elapsed_seconds, "incident": final.incident.value,
    })
    reports_dir = Path("reports")
    reports_dir.mkdir(exist_ok=True)
    (reports_dir / f"{final.trace_id}.md").write_text(report_md)
    return final


def run_loop(settings: Settings, scenario: str | None) -> AgentState:
    """Run the repair loop once, write all artifacts, and return the final AgentState."""
    trace_id = new_trace_id(scenario)
    logger = RunLogger(settings.runs_dir, trace_id, settings.log_level)
    tools = Tools(settings, logger)
    memory = Memory(settings.memory_db)
    graph = build_graph(settings, tools, LLM(settings), logger, memory)

    init = AgentState(trace_id=trace_id, goal=_GOAL_REPAIR, mode=settings.mode.value,
                      scenario=scenario)
    start = time.time()
    final = AgentState.model_validate(graph.invoke(init, config={"recursion_limit": 50}))
    final = _finalize(final, settings, logger, tools, time.time() - start)
    memory.close()
    return final


def _load_alert(alert_path: str | None) -> Alert | None:
    if not alert_path:
        return None
    data = json.loads(Path(alert_path).read_text())
    data.setdefault("source", "file")
    return Alert.model_validate(data)


def run_oncall(settings: Settings, scenario: str | None,
               alert_path: str | None = None) -> AgentState:
    """Run the on-call / incident-response loop once and return the final AgentState."""
    trace_id = new_trace_id(scenario or "oncall")
    logger = RunLogger(settings.runs_dir, trace_id, settings.log_level)
    tools = Tools(settings, logger)
    memory = Memory(settings.memory_db)
    graph = build_oncall_graph(settings, tools, LLM(settings), logger, memory)

    init = AgentState(trace_id=trace_id, goal=_GOAL_ONCALL, mode=settings.mode.value,
                      scenario=scenario, alert=_load_alert(alert_path))
    start = time.time()
    final = AgentState.model_validate(graph.invoke(init, config={"recursion_limit": 50}))
    final = _finalize(final, settings, logger, tools, time.time() - start)
    memory.close()
    return final
