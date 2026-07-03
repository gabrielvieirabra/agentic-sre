"""CLI for the SRE agent (typer + rich).

    sre-agent run --scenario bad-readiness-probe --mode dry-run
    sre-agent report --latest
"""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel

from sre_agent.config import Mode, load_settings
from sre_agent.loop import run_loop

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

    console.print(Panel.fit(
        f"[bold]SRE Repair Loop[/bold]\nscenario=[cyan]{scenario or '(ad-hoc)'}[/cyan]  "
        f"mode=[cyan]{settings.mode.value}[/cyan]  model=[cyan]{settings.model}[/cyan]",
        border_style="blue"))

    final = run_loop(settings, scenario)

    # planned-action block is the key dry-run artifact
    if final.planned_action_block:
        console.print(Panel(final.planned_action_block, title="Planned action",
                            border_style="yellow"))

    ts = final.terminal_state.value if final.terminal_state else "?"
    style = _TERMINAL_STYLE.get(ts, "white")
    console.print(Panel.fit(
        f"Terminal state: [{style}]{ts}[/{style}]\n"
        f"Diagnosis: {final.hypothesis.root_cause if final.hypothesis else '(none)'}\n"
        f"Score: {final.eval_score}   Tool calls: {final.tool_call_count}   "
        f"Elapsed: {final.elapsed_seconds:.1f}s\n"
        f"Report: runs/{final.trace_id}/report.md",
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
