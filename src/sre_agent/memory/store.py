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
CREATE TABLE IF NOT EXISTS followups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    trace_id TEXT,
    incident TEXT,
    item TEXT,
    done INTEGER DEFAULT 0
);
"""

_SUCCESS_STATES = {"FIXED", "IMPROVED", "MITIGATED", "NO_ACTION_NEEDED"}


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
        # The "fix" is a repair patch, an on-call mitigation, or an efficiency recommendation.
        patch = state.proposed_patch
        mit = state.mitigation
        rec = state.recommendation
        if mit is not None:
            fix_summary, tgt_kind, tgt_name = mit.summary, mit.target_kind, mit.target_name
            fix_body = mit.action.value  # pattern keyed on the mitigation action
        elif rec is not None:
            fix_summary, tgt_kind, tgt_name = rec.summary, rec.target_kind, rec.target_name
            fix_body = rec.action.value
        elif patch is not None:
            fix_summary, tgt_kind, tgt_name = patch.summary, patch.target_kind, patch.target_name
            fix_body = patch.kubectl_patch
        else:
            fix_summary = tgt_kind = tgt_name = fix_body = None

        # Key incidents/patterns by efficiency_issue for optimize runs, else by incident.
        incident_val = (state.efficiency_issue.value if state.efficiency_issue
                        else state.incident.value)
        self._conn.execute(
            "INSERT INTO incidents (ts, trace_id, scenario, incident, root_cause, "
            "fix_summary, target_kind, target_name, patch, terminal_state, success, "
            "tool_calls, elapsed) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (ts, state.trace_id, state.scenario, incident_val,
             state.hypothesis.root_cause if state.hypothesis else None,
             fix_summary, tgt_kind, tgt_name, fix_body,
             term, success, state.tool_call_count, state.elapsed_seconds),
        )
        # Update the Fix Pattern Library only when a fix/mitigation was actually applied.
        applied = any(a.applied for a in state.applied_actions)
        if fix_body and tgt_kind and applied and term in {"FIXED", "IMPROVED", "MITIGATED",
                                                          "ROLLED_BACK"}:
            worked = 1 if term in {"FIXED", "IMPROVED", "MITIGATED"} else 0
            self._upsert_pattern(incident_val, tgt_kind, fix_body, worked, ts)
        self._conn.commit()

    def record_followups(self, trace_id: str, incident: str, items: list[str]) -> None:
        ts = datetime.now(UTC).isoformat()
        self._conn.executemany(
            "INSERT INTO followups (ts, trace_id, incident, item) VALUES (?,?,?,?)",
            [(ts, trace_id, incident, item) for item in items],
        )
        self._conn.commit()

    def open_followups(self, limit: int = 50) -> list[dict]:
        rows = self._conn.execute(
            "SELECT ts, incident, item FROM followups WHERE done = 0 ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

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
