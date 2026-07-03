# 005 — Tool Contracts

## Problem
The agent must act only through well-defined, safe, auditable tools — never arbitrary shell.

## Goals
- Every tool has: input schema, output schema, timeout, error handling, **safety classification**,
  and an **audit log entry**.
- Dangerous (mutating) tools require the safety gate before executing.

## Non-goals
- No unbounded shell. No host-file mutation outside the repo. No cloud credential use.

## Safety classification
- `READ` — never mutates; always allowed.
- `MUTATE_LAB` — mutates cluster state; allowed only in `apply-local-lab`, ns-locked to `sre-lab`,
  and only after the safety gate passes.
- `WRITE_REPO` — writes files inside the repo only.

## Common contract (all tools)
```
timeout: per-tool seconds (default 30)
on_error: capture stderr+exit code → structured ToolError (never raises raw)
audit: append JSON {trace_id, ts, tool, args_summary, safety_class, mode, exit_code, duration_ms}
       to the run's tool log; mutating calls also record the rollback command used
returns: pydantic ToolResult {ok, data, error, duration_ms}
```

## Tool catalog
| Tool | Class | Input (key fields) | Output | Timeout |
|---|---|---|---|---|
| `minikube_status` | READ | — | status, nodes | 15 |
| `kubectl_get` | READ | kind, name?, ns=sre-lab, `-o` | parsed json/table | 20 |
| `kubectl_describe` | READ | kind, name, ns | text | 20 |
| `kubectl_logs` | READ | pod, container?, tail, ns | text | 20 |
| `kubectl_rollout_status` | READ | kind/name, ns, timeout | complete? | 60 |
| `collect_metrics` | READ | target (pod/ns) | cpu/mem (from metrics-server) | 20 |
| `run_http_check` | READ | url/svc, expect_status | status, latency_ms | 15 |
| `run_load_test` | READ | url, duration, rps (hey/k6) | rps, p95, error_rate | 60 |
| `read_file` | READ | path (repo-relative) | contents | 5 |
| `kubectl_apply` | MUTATE_LAB | manifest/path, ns=sre-lab | applied objs | 30 |
| `kubectl_patch` | MUTATE_LAB | kind/name, patch, ns | patched | 30 |
| `kubectl_rollout_undo` | MUTATE_LAB | kind/name, ns, to-revision? | reverted | 60 |
| `helm_template_or_apply` | MUTATE_LAB | chart, values, ns | rendered/applied | 60 |
| `write_file` | WRITE_REPO | path (repo-relative), content | bytes | 5 |
| `run_shell_command` | READ/MUTATE | argv (allowlist only) | stdout/exit | 30 |
| `git_diff` | READ | paths | diff | 10 |
| `git_commit` (optional) | WRITE_REPO | message | sha | 10 |

## Guardrails (enforced in code, not prompt)
- `kubectl_*`: force `--context minikube` and `-n sre-lab`; reject any other context/ns.
- `read_file`/`write_file`: resolve path; reject anything outside repo root; reject symlink escape.
- `run_shell_command`: argv allowlist (`kubectl`, `minikube`, `hey`, `curl` to localhost, `kustomize`);
  no shell string interpolation; no `rm`, no pipes, hard timeout.
- Cloud credential env vars are scrubbed from the tool subprocess environment.

## Acceptance criteria
- Every tool call produces an audit entry; every mutating call records a rollback command.
- Attempting a MUTATE_LAB tool in dry-run/suggest-only returns a gated no-op (with the plan).
- A path-escape or non-`sre-lab` target is rejected with a `ToolError`, not executed.

## Risks
- Parsing kubectl output drift → prefer `-o json` and pydantic-validate.
- Allowlist too strict → surface a clear "not allowed" error the agent can reason about.

## Open questions
- Ship `run_load_test` with `hey` or `k6`? (MVP: `hey` if present, else skip perf load evals.)

## Test strategy
- Unit tests per tool: happy path, timeout, error capture, safety rejection, audit entry present.
- Fuzz path-escape attempts for file tools.
