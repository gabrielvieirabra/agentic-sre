# 007 — Safety and Permissions

## Problem
An autonomous agent with kubectl access is dangerous. It must be provably unable to touch anything
outside the local lab, and must make changes visible and reversible.

## Goals
- Default to **dry-run**. Mutations require explicit mode + safety gate.
- Hard scope: repo files + `sre-lab` namespace on the `minikube` context, nothing else.
- Every planned mutation is printed in a standard **planned-action block** before applying.

## Non-goals
- No production/cloud access. No host-file mutation outside repo. No secret exfiltration.

## Requirements

### Modes
| Mode | Reads | Proposes fix | Applies fix |
|---|---|---|---|
| `dry-run` (default) | yes | yes (prints plan) | **no** |
| `suggest-only` | yes | yes (writes suggested manifests to repo) | no |
| `apply-local-lab` | yes | yes | yes, gated (see below) |

### Scope lock (enforced in code)
The agent may modify only:
- files **inside the project repo** (path-escape rejected), and
- Kubernetes resources **inside `sre-lab`** on the **`minikube`** context.
All `kubectl` calls are forced to `--context minikube -n sre-lab`; anything else → reject +
`NEEDS_HUMAN`. Cloud credential env vars are stripped from tool subprocesses.

### Planned-action block (printed before every mutation)
```
Planned action:  <what will change>
Reason:          <why, tied to evidence>
Files/resources: <exact objects in sre-lab / repo paths>
Rollback:        <exact command to undo>
Validation:      <exact command that proves success>
Risk level:      <low | medium | high>
```

### Auto-apply criteria (apply-local-lab)
Auto-apply is permitted **only when all hold**:
1. target namespace is `sre-lab`,
2. resource is part of the lab (known label/owner),
3. a rollback command is known, and
4. a validation command is defined.
Otherwise → escalate `NEEDS_HUMAN`. (Per approved plan: auto-apply-with-rollback; no interactive
prompt inside apply-local-lab, but validation must pass or the change is rolled back.)

### Disallowed (always)
- deleting host files; touching real kube contexts; using cloud credentials;
- reading secrets not created for the lab; unbounded shell; modifying anything outside the project.

## Acceptance criteria
- In dry-run, no cluster mutation occurs; the planned-action block is printed.
- Any attempt to target a non-`sre-lab` resource is refused and escalates.
- Every applied change has a recorded rollback; a failed validation triggers rollback → ROLLED_BACK.

## Risks
- kubeconfig with multiple contexts → always pass `--context minikube` explicitly; never rely on current-context.
- A fix that "passes" validation but harms an unrelated lab resource → diff scope check limits blast radius.

## Open questions
- Add an `allow-list` file of lab resource names to make "part of the lab" explicit? (Recommended; add in Phase 5.)

## Test strategy
- Attempt mutations in each mode and assert allowed/denied behavior.
- Attempt out-of-scope ns/context/path and assert rejection + escalation.
