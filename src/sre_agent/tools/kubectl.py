"""kubectl / cluster tools with strict contracts (specs/005-tool-contracts.md).

Every call: forced `--context <ctx> -n <ns>`, per-call timeout, structured error capture,
and an audit-log entry. Mutating calls are gated on apply mode and record a rollback command.
Cloud credential env vars are stripped from the subprocess environment.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from typing import Any

from pydantic import BaseModel

from sre_agent.config import Settings
from sre_agent.observability import RunLogger

# env vars scrubbed from every tool subprocess (defense in depth; specs/007)
_CLOUD_ENV_PREFIXES = ("AWS_", "GOOGLE_", "GCP_", "AZURE_", "DIGITALOCEAN_", "DO_")


class ToolResult(BaseModel):
    ok: bool
    data: Any = None
    error: str = ""
    duration_ms: int = 0


def _clean_env() -> dict[str, str]:
    return {k: v for k, v in os.environ.items()
            if not any(k.startswith(p) for p in _CLOUD_ENV_PREFIXES)}


class Tools:
    """Bound to a Settings + RunLogger; exposes the safe tool surface used by nodes."""

    def __init__(self, settings: Settings, logger: RunLogger) -> None:
        self.s = settings
        self.log = logger
        self.calls = 0  # total tool invocations (loop guard)

    # ---- low level ------------------------------------------------------
    def _run(self, argv: list[str], *, safety: str, timeout: int,
             rollback: str = "") -> ToolResult:
        self.calls += 1
        start = time.time()
        try:
            proc = subprocess.run(
                argv, capture_output=True, text=True, timeout=timeout, env=_clean_env(),
            )
            dur = int((time.time() - start) * 1000)
            ok = proc.returncode == 0
            res = ToolResult(
                ok=ok,
                data=proc.stdout,
                error="" if ok else (proc.stderr.strip() or f"exit {proc.returncode}"),
                duration_ms=dur,
            )
        except subprocess.TimeoutExpired:
            res = ToolResult(ok=False, error=f"timeout after {timeout}s",
                             duration_ms=int((time.time() - start) * 1000))
        except Exception as e:  # never raise raw
            res = ToolResult(ok=False, error=f"{type(e).__name__}: {e}",
                             duration_ms=int((time.time() - start) * 1000))

        self.log.audit_tool({
            "tool": argv[0] + " " + (argv[1] if len(argv) > 1 else ""),
            "argv_summary": " ".join(argv[:6]) + (" ..." if len(argv) > 6 else ""),
            "safety_class": safety, "mode": self.s.mode.value,
            "exit_ok": res.ok, "duration_ms": res.duration_ms,
            "error": res.error, "rollback": rollback,
        })
        return res

    def _kubectl(self, args: list[str], *, safety: str = "READ", timeout: int = 20,
                 rollback: str = "") -> ToolResult:
        base = ["kubectl", "--context", self.s.kube_context, "-n", self.s.namespace]
        return self._run(base + args, safety=safety, timeout=timeout, rollback=rollback)

    # ---- READ tools -----------------------------------------------------
    def minikube_status(self) -> ToolResult:
        return self._run(["minikube", "status", "-p", self.s.kube_context],
                         safety="READ", timeout=15)

    def get_json(self, kind: str, name: str | None = None) -> ToolResult:
        args = ["get", kind] + ([name] if name else []) + ["-o", "json"]
        res = self._kubectl(args)
        if res.ok and res.data:
            try:
                res.data = json.loads(res.data)
            except json.JSONDecodeError as e:
                return ToolResult(ok=False, error=f"json parse: {e}", duration_ms=res.duration_ms)
        return res

    def describe(self, kind: str, name: str) -> ToolResult:
        return self._kubectl(["describe", kind, name])

    def logs(self, pod: str, tail: int = 40) -> ToolResult:
        return self._kubectl(["logs", pod, "--tail", str(tail)])

    def get_events(self) -> ToolResult:
        return self._kubectl(["get", "events", "--sort-by", ".lastTimestamp", "-o", "json"])

    def rollout_status(self, kind_name: str, timeout_s: int = 90) -> ToolResult:
        return self._kubectl(["rollout", "status", kind_name, f"--timeout={timeout_s}s"],
                             timeout=timeout_s + 10)

    # ---- MUTATE_LAB tools (gated) --------------------------------------
    def _gate(self, description: str) -> ToolResult | None:
        """Return a gated no-op result unless we are in apply-local-lab mode."""
        if not self.s.can_mutate:
            self.log.info("mutation gated (dry-run/suggest-only)", action=description)
            return ToolResult(ok=False, error=f"gated: mode={self.s.mode.value} (no mutation)")
        return None

    def patch(self, kind: str, name: str, patch_json: str, rollback: str = "") -> ToolResult:
        gated = self._gate(f"patch {kind}/{name}")
        if gated is not None:
            return gated
        return self._kubectl(["patch", kind, name, "--type", "strategic", "-p", patch_json],
                             safety="MUTATE_LAB", timeout=30, rollback=rollback)

    def rollout_undo(self, kind_name: str) -> ToolResult:
        gated = self._gate(f"rollout undo {kind_name}")
        if gated is not None:
            return gated
        return self._kubectl(["rollout", "undo", kind_name], safety="MUTATE_LAB", timeout=60)
