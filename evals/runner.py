"""Eval runner + Regression Guard (specs/006-evaluation-strategy.md).

For each case: reset the lab -> inject the scenario -> run the agent in apply-local-lab
-> score deterministically. Then the Regression Guard compares this run to the previous
saved history and flags any case that used to pass but now fails.

    make eval           # or: uv run python -m evals.runner
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import yaml
from rich.console import Console
from rich.table import Table

from evals.scoring import CaseResult, score_case
from sre_agent.config import Mode, load_settings
from sre_agent.loop import run_loop, run_oncall, run_optimize

REPO_ROOT = Path(__file__).resolve().parents[1]
CASES_DIR = REPO_ROOT / "evals" / "cases"
console = Console()

# seconds to wait after injecting a fault so its symptom actually manifests
SETTLE_AFTER_INJECT = 16


def _sh(*args: str) -> None:
    subprocess.run(["bash", *args], cwd=REPO_ROOT, check=False,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def load_cases() -> list[dict]:
    return [yaml.safe_load(p.read_text()) for p in sorted(CASES_DIR.glob("*.yaml"))]


def run_case(case: dict) -> CaseResult:
    settings = load_settings()
    settings.mode = Mode.APPLY_LOCAL_LAB
    scenario = case["scenario"]
    kind = case.get("kind", "repair")

    console.print(f"[bold]▶ {case['name']}[/bold]  ({kind}: reset → inject {scenario} → run)")
    _sh("scripts/reset_lab.sh")
    _sh("scripts/inject_bug.sh", scenario)
    time.sleep(SETTLE_AFTER_INJECT)

    if kind == "oncall":
        alert = str(REPO_ROOT / case["alert"]) if case.get("alert") else None
        state = run_oncall(settings, scenario, alert)
    elif kind == "optimize":
        state = run_optimize(settings, scenario, case.get("app", "web"), case.get("peak"))
    else:
        state = run_loop(settings, scenario)
    return score_case(case, state)


def _history_path() -> Path:
    return Path(load_settings().runs_dir) / "eval_history.jsonl"


def _load_prev_results() -> dict[str, bool]:
    """Return {case_name: passed} from the most recent prior history entry."""
    path = _history_path()
    if not path.exists():
        return {}
    lines = [ln for ln in path.read_text().splitlines() if ln.strip()]
    if not lines:
        return {}
    last = json.loads(lines[-1])
    return {r["name"]: r["passed"] for r in last.get("results", [])}


def _append_history(results: list[CaseResult]) -> None:
    path = _history_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "ts": datetime.now(UTC).isoformat(),
        "results": [{"name": r.name, "overall": r.overall, "passed": r.passed,
                     "terminal_state": r.terminal_state} for r in results],
    }
    with path.open("a") as fh:
        fh.write(json.dumps(entry) + "\n")


def _render(results: list[CaseResult]) -> None:
    t = Table(title="SRE Eval Results", show_lines=False)
    for col in ("case", "diag", "valid", "minimal"):
        t.add_column(col)
    t.add_column("overall", justify="right")
    t.add_column("terminal")
    t.add_column("result")
    for r in results:
        d = r.dimensions
        ok = "[green]PASS[/green]" if r.passed else "[red]FAIL[/red]"
        t.add_row(
            r.name,
            "✓" if d["correct_diagnosis"] >= 1 else "✗",
            "✓" if d["successful_validation"] >= 1 else "✗",
            "✓" if d["minimal_safe_fix"] >= 1 else "✗",
            f"{r.overall:.2f}", r.terminal_state or "?", ok,
        )
    console.print(t)


def regression_guard(prev: dict[str, bool], results: list[CaseResult]) -> list[str]:
    regressions = []
    for r in results:
        if prev.get(r.name) is True and not r.passed:
            regressions.append(r.name)
    return regressions


def main() -> int:
    cases = load_cases()
    if not cases:
        console.print("[yellow]No eval cases found in evals/cases/.[/yellow]")
        return 1

    prev = _load_prev_results()  # captured BEFORE this run for the Regression Guard
    results = [run_case(c) for c in cases]

    _render(results)
    _append_history(results)

    passed = sum(1 for r in results if r.passed)
    console.print(f"\n[bold]{passed}/{len(results)} cases passed[/bold]")

    regressions = regression_guard(prev, results)
    if not prev:
        console.print("[cyan]Regression Guard: no prior history (baseline established).[/cyan]")
    elif regressions:
        console.print(f"[red]Regression Guard: REGRESSIONS in {', '.join(regressions)}[/red]")
    else:
        console.print("[green]Regression Guard: no regressions vs previous run.[/green]")

    # leave the lab healthy
    _sh("scripts/reset_lab.sh")
    return 0 if passed == len(results) and not regressions else 1


if __name__ == "__main__":
    sys.exit(main())
