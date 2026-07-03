"""kubectl / cluster tools with strict contracts (specs/005-tool-contracts.md).

Every call: forced `--context <ctx> -n <ns>`, per-call timeout, structured error capture,
and an audit-log entry. Mutating calls are gated on apply mode and record a rollback command.
Cloud credential env vars are stripped from the subprocess environment.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from typing import Any

from pydantic import BaseModel

from sre_agent.config import Settings
from sre_agent.observability import RunLogger

# env vars scrubbed from every tool subprocess (defense in depth; specs/007)
_CLOUD_ENV_PREFIXES = ("AWS_", "GOOGLE_", "GCP_", "AZURE_", "DIGITALOCEAN_", "DO_")

# transient apiserver errors worth a quick retry (local Docker VM under memory pressure);
# safe because all our kubectl ops are idempotent/declarative
_TRANSIENT = ("TLS handshake timeout", "Unable to connect to the server",
              "connection refused", "i/o timeout", "EOF")


class ToolResult(BaseModel):
    ok: bool
    data: Any = None
    error: str = ""
    duration_ms: int = 0


def _clean_env() -> dict[str, str]:
    return {k: v for k, v in os.environ.items()
            if not any(k.startswith(p) for p in _CLOUD_ENV_PREFIXES)}


def _parse_hey(out: str) -> dict | None:
    """Extract {rps, p95_ms, error_rate} from `hey` text output. None if unparseable."""
    if not out:
        return None
    rps = re.search(r"Requests/sec:\s*([\d.]+)", out)
    p95 = re.search(r"95%\s+in\s+([\d.]+)\s+secs", out)
    codes = re.findall(r"\[(\d+)\]\s+(\d+)\s+responses", out)
    total = sum(int(n) for _, n in codes)
    ok2xx = sum(int(n) for c, n in codes if c.startswith("2"))
    if not rps and not codes:
        return None
    return {
        "rps": float(rps.group(1)) if rps else 0.0,
        "p95_ms": round(float(p95.group(1)) * 1000, 1) if p95 else None,
        "error_rate": round((total - ok2xx) / total, 3) if total else None,
    }


class Tools:
    """Bound to a Settings + RunLogger; exposes the safe tool surface used by nodes."""

    def __init__(self, settings: Settings, logger: RunLogger) -> None:
        self.s = settings
        self.log = logger
        self.calls = 0  # total tool invocations (loop guard)

    # ---- low level ------------------------------------------------------
    def _run(self, argv: list[str], *, safety: str, timeout: int,
             rollback: str = "", stdin: str | None = None) -> ToolResult:
        self.calls += 1
        start = time.time()
        res = ToolResult(ok=False, error="not run")
        for attempt in range(3):  # retry transient apiserver errors
            try:
                proc = subprocess.run(
                    argv, capture_output=True, text=True, timeout=timeout, env=_clean_env(),
                    input=stdin,
                )
                dur = int((time.time() - start) * 1000)
                ok = proc.returncode == 0
                res = ToolResult(
                    ok=ok, data=proc.stdout,
                    error="" if ok else (proc.stderr.strip() or f"exit {proc.returncode}"),
                    duration_ms=dur,
                )
            except subprocess.TimeoutExpired:
                res = ToolResult(ok=False, error=f"timeout after {timeout}s",
                                 duration_ms=int((time.time() - start) * 1000))
            except Exception as e:  # never raise raw
                res = ToolResult(ok=False, error=f"{type(e).__name__}: {e}",
                                 duration_ms=int((time.time() - start) * 1000))
            if res.ok or not any(t in res.error for t in _TRANSIENT) or attempt == 2:
                break
            time.sleep(3)  # brief backoff before retrying a transient failure

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

    def get_hpa(self, name: str) -> ToolResult:
        return self.get_json("hpa", name)

    def rollout_status(self, kind_name: str, timeout_s: int = 90) -> ToolResult:
        return self._kubectl(["rollout", "status", kind_name, f"--timeout={timeout_s}s"],
                             timeout=timeout_s + 10)

    def top_pods(self) -> ToolResult:
        # Saturation evidence (needs metrics-server). Best-effort; may be empty early.
        return self._kubectl(["top", "pods", "--no-headers"], timeout=15)

    def run_load_test(self, app: str, duration_s: int = 8, concurrency: int = 20,
                      port: int = 18080) -> ToolResult:
        """Best-effort load test via `hey` over a temporary port-forward (READ probe).

        Returns {rps, p95_ms, error_rate}; ok=False (skipped) if hey is missing or the
        port-forward/load fails — never raises, never mutates cluster state.
        """
        self.calls += 1
        if not shutil.which("hey"):
            return ToolResult(ok=False, error="hey not installed (load test skipped)")
        pf = None
        try:
            pf = subprocess.Popen(
                ["kubectl", "--context", self.s.kube_context, "-n", self.s.namespace,
                 "port-forward", f"svc/{app}", f"{port}:80"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=_clean_env(),
            )
            time.sleep(3)  # let the port-forward establish
            proc = subprocess.run(
                ["hey", "-z", f"{duration_s}s", "-c", str(concurrency),
                 f"http://localhost:{port}/"],
                capture_output=True, text=True, timeout=duration_s + 25, env=_clean_env(),
            )
            data = _parse_hey(proc.stdout)
            res = ToolResult(ok=bool(data), data=data or None,
                             error="" if data else "could not parse hey output")
        except Exception as e:  # noqa: BLE001 - best-effort, never break the loop
            res = ToolResult(ok=False, error=f"load test failed: {type(e).__name__}: {e}")
        finally:
            if pf is not None:
                pf.terminate()
        self.log.audit_tool({"tool": "run_load_test", "argv_summary": f"hey {app}",
                             "safety_class": "READ", "mode": self.s.mode.value,
                             "exit_ok": res.ok, "error": res.error})
        return res

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

    def scale(self, kind_name: str, replicas: int, rollback: str = "") -> ToolResult:
        gated = self._gate(f"scale {kind_name} -> {replicas}")
        if gated is not None:
            return gated
        return self._kubectl(["scale", kind_name, f"--replicas={replicas}"],
                             safety="MUTATE_LAB", timeout=30, rollback=rollback)

    def rollout_restart(self, kind_name: str, rollback: str = "") -> ToolResult:
        gated = self._gate(f"rollout restart {kind_name}")
        if gated is not None:
            return gated
        return self._kubectl(["rollout", "restart", kind_name],
                             safety="MUTATE_LAB", timeout=30, rollback=rollback)

    def set_hpa(self, deploy: str, min_replicas: int, max_replicas: int,
                cpu_percent: int, rollback: str = "") -> ToolResult:
        """Create/replace an autoscaling/v2 HPA for a Deployment (apply from stdin)."""
        gated = self._gate(f"set hpa {deploy} {min_replicas}-{max_replicas}@{cpu_percent}%")
        if gated is not None:
            return gated
        manifest = "\n".join([
            "apiVersion: autoscaling/v2",
            "kind: HorizontalPodAutoscaler",
            f"metadata: {{name: {deploy}, namespace: {self.s.namespace}}}",
            "spec:",
            f"  scaleTargetRef: {{apiVersion: apps/v1, kind: Deployment, name: {deploy}}}",
            f"  minReplicas: {min_replicas}",
            f"  maxReplicas: {max_replicas}",
            "  metrics:",
            "    - type: Resource",
            "      resource:",
            "        name: cpu",
            "        target:",
            "          type: Utilization",
            f"          averageUtilization: {cpu_percent}",
            "",
        ])
        argv = ["kubectl", "--context", self.s.kube_context, "-n", self.s.namespace,
                "apply", "-f", "-"]
        return self._run(argv, safety="MUTATE_LAB", timeout=30, rollback=rollback, stdin=manifest)

    def delete_hpa(self, name: str) -> ToolResult:
        gated = self._gate(f"delete hpa {name}")
        if gated is not None:
            return gated
        return self._kubectl(["delete", "hpa", name, "--ignore-not-found"],
                             safety="MUTATE_LAB", timeout=30)
