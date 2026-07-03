"""CLI for the SRE agent (typer + rich).

    sre-agent run --scenario bad-readiness-probe --mode dry-run
    sre-agent report --latest
"""

from __future__ import annotations

import time
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel

from sre_agent.config import Mode, load_settings
from sre_agent.graph import build_graph
from sre_agent.llm import LLM
from sre_agent.observability import RunLogger, new_trace_id
from sre_agent.reports import build_report
from sre_agent.state import AgentState
from sre_agent.tools import Tools

app = typer.Typer(add_completion=False, help="Local-first SRE agentic repair loop.")
console = Console()

_TERMINAL_STYLE = {
    "FIXED": "bold green", "IMPROVED": "green", "NO_ACTION_NEEDED": "cyan",
    "NEEDS_HUMAN": "yellow", "FAILED_SAFELY": "magenta", "ROLLED_BACK": "red",
}


@app.command()
def run(
    scenario: str = typer.Option(None, help="Scenario key (for labeling/report only)."),
    mode: str = typer.Option(None, help="dry-run | suggest-only | apply-local-lab"),
) -> None:
    """Run the SRE repair loop once against the sre-lab cluster state."""
    settings = load_settings()
    if mode:
        settings.mode = Mode(mode)

    trace_id = new_trace_id(scenario)
    logger = RunLogger(settings.runs_dir, trace_id, settings.log_level)
    tools = Tools(settings, logger)
    llm = LLM(settings)

    graph = build_graph(settings, tools, llm, logger)
    init = AgentState(
        trace_id=trace_id,
        goal="Restore the sre-lab workload to a healthy, validated state with the "
             "smallest safe change.",
        mode=settings.mode.value,
        scenario=scenario,
    )

    console.print(Panel.fit(
        f"[bold]SRE Repair Loop[/bold]\nscenario=[cyan]{scenario or '(ad-hoc)'}[/cyan]  "
        f"mode=[cyan]{settings.mode.value}[/cyan]  model=[cyan]{settings.model}[/cyan]",
        border_style="blue"))

    start = time.time()
    final_dict = graph.invoke(init, config={"recursion_limit": 50})
    elapsed = time.time() - start
    final = AgentState.model_validate(final_dict)
    final.elapsed_seconds = elapsed
    final.tool_call_count = tools.calls

    # planned-action block is the key dry-run artifact
    if final.planned_action_block:
        console.print(Panel(final.planned_action_block, title="Planned action",
                            border_style="yellow"))

    report_md = build_report(final, settings)
    logger.write_text("report.md", report_md)
    terminal = final.terminal_state.value if final.terminal_state else None
    logger.write_json("meta.json", {
        "trace_id": trace_id, "scenario": scenario, "mode": settings.mode.value,
        "model": settings.model, "terminal_state": terminal,
        "eval_score": final.eval_score, "tool_calls": tools.calls,
        "elapsed_seconds": round(elapsed, 2), "incident": final.incident.value,
    })
    # copy report into the repo-level reports/ dir too
    reports_dir = Path("reports")
    reports_dir.mkdir(exist_ok=True)
    (reports_dir / f"{trace_id}.md").write_text(report_md)

    ts = final.terminal_state.value if final.terminal_state else "?"
    style = _TERMINAL_STYLE.get(ts, "white")
    console.print(Panel.fit(
        f"Terminal state: [{style}]{ts}[/{style}]\n"
        f"Diagnosis: {final.hypothesis.root_cause if final.hypothesis else '(none)'}\n"
        f"Score: {final.eval_score}   Tool calls: {tools.calls}   Elapsed: {elapsed:.1f}s\n"
        f"Report: runs/{trace_id}/report.md",
        title="Result", border_style=style))


@app.command()
def report(
    latest: bool = typer.Option(False, "--latest", help="Show the most recent run report."),
) -> None:
    """Print the most recent run report."""
    settings = load_settings()
    runs = sorted(Path(settings.runs_dir).glob("*/report.md"), key=lambda p: p.stat().st_mtime)
    if not runs:
        console.print("[yellow]No runs found. Run `sre-agent run` first.[/yellow]")
        raise typer.Exit(1)
    console.print(runs[-1].read_text())


if __name__ == "__main__":
    app()
