"""SQLite-backed local memory (specs/008 + product spec 'Memory').

Stores past incidents, root causes, fixes, validation outcomes and reusable lessons —
never secrets. Also maintains a Fix Pattern Library: per (incident, target) it tracks
which structured patch worked and how often, so future runs can recall a proven fix.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from sre_agent.state import AgentState

_SCHEMA = """
CREATE TABLE IF NOT EXISTS incidents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    trace_id TEXT,
    scenario TEXT,
    incident TEXT,
    root_cause TEXT,
    fix_summary TEXT,
    target_kind TEXT,
    target_name TEXT,
    patch TEXT,
    terminal_state TEXT,
    success INTEGER,
    tool_calls INTEGER,
    elapsed REAL
);
CREATE TABLE IF NOT EXISTS fix_patterns (
    incident TEXT,
    target_kind TEXT,
    patch TEXT,
    successes INTEGER DEFAULT 0,
    failures INTEGER DEFAULT 0,
    last_ts TEXT,
    PRIMARY KEY (incident, target_kind, patch)
);
"""

_SUCCESS_STATES = {"FIXED", "IMPROVED", "NO_ACTION_NEEDED"}


class Memory:
    def __init__(self, db_path: Path | str) -> None:
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path))
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    # ---- writes ------------------------------------------------------
    def record_run(self, state: AgentState) -> None:
        ts = datetime.now(UTC).isoformat()
        term = state.terminal_state.value if state.terminal_state else None
        success = 1 if term in _SUCCESS_STATES else 0
        patch = state.proposed_patch
        self._conn.execute(
            "INSERT INTO incidents (ts, trace_id, scenario, incident, root_cause, "
            "fix_summary, target_kind, target_name, patch, terminal_state, success, "
            "tool_calls, elapsed) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (ts, state.trace_id, state.scenario, state.incident.value,
             state.hypothesis.root_cause if state.hypothesis else None,
             patch.summary if patch else None,
             patch.target_kind if patch else None,
             patch.target_name if patch else None,
             patch.kubectl_patch if patch else None,
             term, success, state.tool_call_count, state.elapsed_seconds),
        )
        # Update the Fix Pattern Library only when a fix was actually applied.
        applied = any(a.applied for a in state.applied_actions)
        if patch and applied and term in {"FIXED", "IMPROVED", "ROLLED_BACK"}:
            worked = 1 if term in {"FIXED", "IMPROVED"} else 0
            self._upsert_pattern(state.incident.value, patch.target_kind,
                                 patch.kubectl_patch, worked, ts)
        self._conn.commit()

    def _upsert_pattern(self, incident: str, kind: str, patch: str,
                        worked: int, ts: str) -> None:
        self._conn.execute(
            "INSERT INTO fix_patterns (incident, target_kind, patch, successes, failures, "
            "last_ts) VALUES (?,?,?,?,?,?) "
            "ON CONFLICT(incident, target_kind, patch) DO UPDATE SET "
            "successes = successes + ?, failures = failures + ?, last_ts = ?",
            (incident, kind, patch, worked, 1 - worked, ts, worked, 1 - worked, ts),
        )

    # ---- reads -------------------------------------------------------
    def recall_incidents(self, incident: str, limit: int = 3) -> list[dict]:
        rows = self._conn.execute(
            "SELECT ts, scenario, root_cause, fix_summary, terminal_state FROM incidents "
            "WHERE incident = ? ORDER BY id DESC LIMIT ?",
            (incident, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def best_fix_pattern(self, incident: str, target_kind: str) -> dict | None:
        row = self._conn.execute(
            "SELECT patch, successes, failures FROM fix_patterns "
            "WHERE incident = ? AND target_kind = ? AND successes > 0 "
            "ORDER BY successes DESC, failures ASC LIMIT 1",
            (incident, target_kind),
        ).fetchone()
        return dict(row) if row else None

    def run_history(self, limit: int = 20) -> list[dict]:
        rows = self._conn.execute(
            "SELECT ts, scenario, incident, terminal_state, success, tool_calls, elapsed "
            "FROM incidents ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
