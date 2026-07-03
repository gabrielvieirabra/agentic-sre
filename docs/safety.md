# Safety

Companion to [`specs/007-safety-and-permissions.md`](../specs/007-safety-and-permissions.md).

## Default posture: dry-run
Out of the box the agent **reads and proposes but never mutates**. Mutation requires explicitly
choosing `apply-local-lab` mode.

## Three modes
- `dry-run` — read + propose; prints the planned-action block; no changes.
- `suggest-only` — writes suggested manifests into the repo; still no cluster changes.
- `apply-local-lab` — applies gated changes to `sre-lab` only, with rollback + validation.

## Hard scope (enforced in code, not prompt)
The agent may touch only:
- files **inside this repo** (path-escape and symlink escape rejected), and
- Kubernetes resources in **`sre-lab`** on the **`minikube`** context.

Every `kubectl` invocation is forced to `--context minikube -n sre-lab`. Cloud credential env
vars are stripped from tool subprocesses. Anything out of scope → refuse and escalate `NEEDS_HUMAN`.

## Planned-action block (before every mutation)
```
Planned action:  ...
Reason:          ...
Files/resources: ...
Rollback:        ...
Validation:      ...
Risk level:      low | medium | high
```

## Auto-apply gate
Auto-apply only when: target ns is `sre-lab`, resource is part of the lab, rollback is known, and
validation is defined. If validation fails after applying, the change is rolled back → `ROLLED_BACK`.

## Never
Delete host files · touch real kube contexts · use cloud credentials · read non-lab secrets ·
run unbounded shell · modify anything outside the project.
