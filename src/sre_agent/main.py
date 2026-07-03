"""CLI for the SRE agent (typer + rich).

    sre-agent run --scenario bad-readiness-probe --mode dry-run
    sre-agent report --latest
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from sre_agent.config import Mode, load_settings
from sre_agent.loop import run_loop
from sre_agent.memory import Memory

# Chaos Scenario Generator: the controlled, local-only faults it may inject.
CHAOS_SCENARIOS = ["wrong-image-tag", "bad-readiness-probe", "service-selector-mismatch"]

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
    history: bool = typer.Option(False, "--history", help="Show run history from local memory."),
) -> None:
    """Print the most recent run report, or --history for the trend from local memory."""
    settings = load_settings()
    if history:
        mem = Memory(settings.memory_db)
        rows = mem.run_history(limit=20)
        mem.close()
        if not rows:
            console.print("[yellow]No history yet. Run the agent or `make eval` first.[/yellow]")
            raise typer.Exit(1)
        t = Table(title="Run history (local memory)")
        for col in ("when", "scenario", "incident", "terminal", "tools", "elapsed"):
            t.add_column(col)
        for r in rows:
            style = _TERMINAL_STYLE.get(r["terminal_state"] or "", "white")
            t.add_row(
                (r["ts"] or "")[:19], r["scenario"] or "-", r["incident"] or "-",
                f"[{style}]{r['terminal_state']}[/{style}]",
                str(r["tool_calls"] or 0), f"{r['elapsed'] or 0:.1f}s",
            )
        console.print(t)
        return

    runs = sorted(Path(settings.runs_dir).glob("*/report.md"), key=lambda p: p.stat().st_mtime)
    if not runs:
        console.print("[yellow]No runs found. Run `sre-agent run` first.[/yellow]")
        raise typer.Exit(1)
    console.print(runs[-1].read_text())


@app.command()
def chaos(
    scenario: str = typer.Option(None, help=f"One of {CHAOS_SCENARIOS}, or omit for random."),
    run_agent: bool = typer.Option(False, "--run", help="Also run the repair loop after inject."),
    mode: str = typer.Option("dry-run", help="Mode for --run (dry-run | apply-local-lab)."),
    seed: int = typer.Option(None, help="Deterministic pick from the scenario list (index)."),
) -> None:
    """Chaos Scenario Generator: inject a controlled, local-only fault into sre-lab."""
    if scenario is None:
        # deterministic if seed given (no wall-clock randomness), else first as default
        idx = (seed if seed is not None else 0) % len(CHAOS_SCENARIOS)
        scenario = CHAOS_SCENARIOS[idx]
    if scenario not in CHAOS_SCENARIOS:
        console.print(f"[red]Unknown scenario '{scenario}'. Choose from {CHAOS_SCENARIOS}.[/red]")
        raise typer.Exit(1)

    console.print(Panel.fit(f"[bold]Chaos[/bold]: injecting [cyan]{scenario}[/cyan] into sre-lab",
                            border_style="magenta"))
    proc = subprocess.run(["bash", "scripts/inject_bug.sh", scenario], check=False)
    if proc.returncode != 0:
        raise typer.Exit(proc.returncode)

    if run_agent:
        settings = load_settings()
        settings.mode = Mode(mode)
        final = run_loop(settings, scenario)
        ts = final.terminal_state.value if final.terminal_state else "?"
        console.print(f"Repair loop finished: [bold]{ts}[/bold] (score {final.eval_score})")


if __name__ == "__main__":
    app()
