"""LangGraph state and the typed sub-objects that flow through the repair loop.

See specs/002-agent-architecture.md and specs/003-loop-engineering-design.md.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class TerminalState(StrEnum):
    FIXED = "FIXED"
    IMPROVED = "IMPROVED"
    NO_ACTION_NEEDED = "NO_ACTION_NEEDED"
    NEEDS_HUMAN = "NEEDS_HUMAN"
    FAILED_SAFELY = "FAILED_SAFELY"
    ROLLED_BACK = "ROLLED_BACK"


class IncidentCategory(StrEnum):
    IMAGE_PULL = "image-pull"
    READINESS_PROBE = "readiness-probe"
    SERVICE_ENDPOINTS = "service-endpoints"
    CRASH_LOOP = "crash-loop"
    PENDING_SCHEDULING = "pending-scheduling"
    UNKNOWN = "unknown"
    NONE = "none"


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class Symptom(BaseModel):
    kind: str  # e.g. "pod", "service"
    name: str
    detail: str  # human-readable, deterministically derived


class Evidence(BaseModel):
    source: str  # tool name that produced it
    summary: str


class Hypothesis(BaseModel):
    root_cause: str
    confidence: float = 0.0
    rationale: str = ""


class ProposedPatch(BaseModel):
    """A bounded, structured fix — not a free-form shell command."""

    summary: str
    target_kind: str  # Deployment | Service
    target_name: str
    kubectl_patch: str = ""  # JSON strategic-merge patch applied with `kubectl patch`
    rollback: str = ""
    validation: str = ""
    risk_level: RiskLevel = RiskLevel.MEDIUM


class ValidationResult(BaseModel):
    healthy: bool = False
    rollout_complete: bool = False
    ready_replicas: int = 0
    desired_replicas: int = 0
    endpoints: int = 0
    http_ok: bool | None = None
    detail: str = ""


class AppliedAction(BaseModel):
    description: str
    command: str
    rollback_command: str = ""
    applied: bool = False


class AgentState(BaseModel):
    """Single state object threaded through every node."""

    # goal / context
    trace_id: str
    goal: str
    mode: str
    scenario: str | None = None

    # observation
    cluster_snapshot: dict = Field(default_factory=dict)
    symptoms: list[Symptom] = Field(default_factory=list)
    incident: IncidentCategory = IncidentCategory.UNKNOWN

    # reasoning
    evidence: list[Evidence] = Field(default_factory=list)
    hypothesis: Hypothesis | None = None
    proposed_patch: ProposedPatch | None = None

    # action
    planned_action_block: str = ""
    applied_actions: list[AppliedAction] = Field(default_factory=list)

    # verification
    baseline_validation: ValidationResult | None = None
    validation: ValidationResult | None = None
    eval_score: float | None = None

    # loop bookkeeping / guards
    iteration_count: int = 0
    tool_call_count: int = 0
    elapsed_seconds: float = 0.0

    # outcome
    terminal_state: TerminalState | None = None
    escalation_reason: str = ""

    model_config = {"arbitrary_types_allowed": True}
