# Loop Engineering

Companion to [`specs/003-loop-engineering-design.md`](../specs/003-loop-engineering-design.md).

## Principle
Build reusable, bounded **loops**, not one-off prompts. A loop is a contract: trigger, goal,
allowed tools/actions, verification, stopping rules, rollback, memory, escalation, terminal states.
The same `k8s_sre_repair_loop` contract serves every scenario.

## The loop never runs forever
Three hard guards, enforced in code (counters in state), stop the loop:
- `max_iterations` (default 6)
- `max_tool_calls` (default 40)
- `max_elapsed_seconds` (default 600)
Plus: immediate stop on any terminal state, and rollback-on-regression.

## Terminal states
| State | Meaning |
|---|---|
| `FIXED` | resolved and validated |
| `IMPROVED` | measurable gain, not fully resolved |
| `NO_ACTION_NEEDED` | already healthy / false alarm |
| `NEEDS_HUMAN` | escalated (denied action, low confidence, no progress) |
| `FAILED_SAFELY` | couldn't fix, no harm done |
| `ROLLED_BACK` | fix made it worse, reverted |

## Maker / Checker / Judge
Separation prevents a confident-but-wrong model from declaring victory: the Checker re-observes
the cluster independently, and the Judge decides using deterministic signals before any LLM opinion.

## SRE mindset encoded
Evidence over guesses · smallest safe fix · validate before/after · minimize blast radius ·
roll back if worse · document · escalate when unsure. Golden signals and error-budget thinking
inform which symptoms count as "abnormal" and when IMPROVED vs FIXED applies.
