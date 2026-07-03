"""LangGraph state and the typed sub-objects that flow through the repair loop.

See specs/002-agent-architecture.md and specs/003-loop-engineering-design.md.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class TerminalState(StrEnum):
    FIXED = "FIXED"
    IMPROVED = "IMPROVED"
    MITIGATED = "MITIGATED"  # on-call: bleeding stopped, root cause -> follow-up
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
    # on-call / incident-response incidents (mitigation-driven)
    BAD_DEPLOY = "bad-deploy"
    OVERLOAD = "overload"
    DEPENDENCY_DOWN = "dependency-down"
    CONFIG_DRIFT = "config-drift"
    UNKNOWN = "unknown"
    NONE = "none"


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class Severity(StrEnum):
    """Incident severity (blast radius / urgency). SEV1 = worst."""

    SEV1 = "SEV1"
    SEV2 = "SEV2"
    SEV3 = "SEV3"
    SEV4 = "SEV4"


class MitigationAction(StrEnum):
    ROLLBACK = "rollback"
    SCALE_OUT = "scale-out"
    RESTART = "restart"
    CONFIG_PATCH = "config-patch"
    DEPENDENCY_FALLBACK = "dependency-fallback"


class EfficiencyIssue(StrEnum):
    OVER_PROVISIONED = "over-provisioned"
    UNDER_PROVISIONED = "under-provisioned"
    NO_AUTOSCALING = "no-autoscaling"
    CAPACITY_RISK = "capacity-risk"
    EFFICIENT = "efficient"


class OptimizationAction(StrEnum):
    RIGHT_SIZE_DOWN = "right-size-down"
    RIGHT_SIZE_UP = "right-size-up"
    SET_HPA = "set-hpa"
    ADJUST_REPLICAS = "adjust-replicas"


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


class Alert(BaseModel):
    """An incoming alert (from a JSON file, Alertmanager-ish, or auto-synthesized)."""

    name: str
    signal: str  # e.g. DeployFailed, HighLatency, PodsDegraded, DependencyDown
    severity: Severity = Severity.SEV3
    source: str = "auto"  # "file" | "auto"
    description: str = ""
    labels: dict = Field(default_factory=dict)


class Mitigation(BaseModel):
    """A bounded, structured mitigation — stops the bleeding, not a root-cause fix."""

    action: MitigationAction
    summary: str
    target_kind: str  # Deployment | Service | ConfigMap
    target_name: str
    params: dict = Field(default_factory=dict)  # e.g. {"replicas": 3} or a JSON patch
    rollback: str = ""
    validation: str = ""
    risk_level: RiskLevel = RiskLevel.MEDIUM


class ResourceAnalysis(BaseModel):
    """Deterministic efficiency scorecard for one app (from kubectl top + spec)."""

    app: str
    replicas: int = 0
    cpu_usage_m: int = 0        # total observed millicores across pods
    mem_usage_mi: int = 0       # total observed MiB across pods
    cpu_request_m: int = 0      # per-pod request
    mem_request_mi: int = 0
    cpu_limit_m: int = 0        # per-pod limit (0 = unset)
    cpu_util_pct: float = 0.0   # usage / (request * replicas)
    mem_util_pct: float = 0.0
    hpa_present: bool = False
    cost_units: float = 0.0
    smells: list[str] = Field(default_factory=list)


class Recommendation(BaseModel):
    """A bounded, structured efficiency change (right-size / HPA / replicas)."""

    action: OptimizationAction
    summary: str
    target_kind: str  # Deployment | HorizontalPodAutoscaler
    target_name: str
    params: dict = Field(default_factory=dict)
    rollback: str = ""
    validation: str = ""
    risk_level: RiskLevel = RiskLevel.LOW
    est_savings: str = ""  # human-readable cost-units / $ delta


class CapacityPlan(BaseModel):
    peak_multiplier: float = 2.0
    current_replicas: int = 0
    required_replicas: int = 0
    note: str = ""


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
    rollback_command: str = ""  # human-readable, for the report
    applied: bool = False
    # structured rollback so the executor can undo safely (no free-form shell)
    target_kind: str = ""
    target_name: str = ""
    rollback_patch: str = ""  # JSON: Service selector patch, {"replicas":N}, or ConfigMap patch
    mitigation_action: str = ""  # set for on-call mitigations, drives mitigation-aware rollback


class AgentState(BaseModel):
    """Single state object threaded through every node."""

    # goal / context
    trace_id: str
    goal: str
    mode: str
    scenario: str | None = None
    target_app: str = "web"  # lab app this run observes/acts on (on-call may switch to depsvc)

    # observation
    cluster_snapshot: dict = Field(default_factory=dict)
    symptoms: list[Symptom] = Field(default_factory=list)
    incident: IncidentCategory = IncidentCategory.UNKNOWN

    # reasoning
    evidence: list[Evidence] = Field(default_factory=list)
    hypothesis: Hypothesis | None = None
    proposed_patch: ProposedPatch | None = None

    # memory recall (past similar incidents + a proven fix pattern)
    recalled: list[str] = Field(default_factory=list)
    matched_pattern: str = ""

    # on-call / incident response
    alert: Alert | None = None
    severity: Severity | None = None
    mitigation: Mitigation | None = None
    incident_timeline: list[str] = Field(default_factory=list)
    followups: list[str] = Field(default_factory=list)

    # efficiency / capacity / cost
    efficiency_issue: EfficiencyIssue | None = None
    analysis: ResourceAnalysis | None = None
    recommendation: Recommendation | None = None
    capacity_plan: CapacityPlan | None = None
    load_result: dict | None = None
    load_test: bool = False  # opt-in: run a best-effort hey load test as evidence
    peak_multiplier: float = 2.0

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
