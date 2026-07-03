"""Per-run observability: run directory, structured JSONL logs, tool audit, snapshots.

See specs/008-observability.md. Everything is local files under runs/<trace_id>/.
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


class RunLogger:
    """Writes events.jsonl, tools.jsonl, snapshots and the report into runs/<trace_id>/."""

    def __init__(self, runs_dir: Path, trace_id: str, level: str = "INFO") -> None:
        self.trace_id = trace_id
        self.dir = Path(runs_dir) / trace_id
        self.dir.mkdir(parents=True, exist_ok=True)
        self.events_path = self.dir / "events.jsonl"
        self.tools_path = self.dir / "tools.jsonl"
        self.node = "-"
        self._levels = {"DEBUG": 10, "INFO": 20, "WARN": 30, "ERROR": 40}
        self._threshold = self._levels.get(level.upper(), 20)

    def set_node(self, node: str) -> None:
        self.node = node

    def _write(self, path: Path, obj: dict[str, Any]) -> None:
        with path.open("a") as fh:
            fh.write(json.dumps(obj, default=str) + "\n")

    def log(self, level: str, msg: str, **fields: Any) -> None:
        if self._levels.get(level.upper(), 20) < self._threshold:
            return
        self._write(
            self.events_path,
            {"ts": utc_now_iso(), "trace_id": self.trace_id, "node": self.node,
             "level": level.upper(), "msg": msg, "fields": fields},
        )

    def info(self, msg: str, **f: Any) -> None:
        self.log("INFO", msg, **f)

    def warn(self, msg: str, **f: Any) -> None:
        self.log("WARN", msg, **f)

    def error(self, msg: str, **f: Any) -> None:
        self.log("ERROR", msg, **f)

    def audit_tool(self, entry: dict[str, Any]) -> None:
        entry = {"ts": utc_now_iso(), "trace_id": self.trace_id, "node": self.node, **entry}
        self._write(self.tools_path, entry)

    def write_json(self, name: str, data: Any) -> Path:
        p = self.dir / name
        p.write_text(json.dumps(data, indent=2, default=str))
        return p

    def write_text(self, name: str, text: str) -> Path:
        p = self.dir / name
        p.write_text(text)
        return p


def new_trace_id(scenario: str | None = None) -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    ms = int((time.time() % 1) * 1000)
    suffix = (scenario or "run").replace("/", "-")
    return f"{stamp}-{ms:03d}-{suffix}"
