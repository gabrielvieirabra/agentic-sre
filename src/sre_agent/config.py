"""Runtime configuration for the SRE agent.

Loads from environment / `.env` (see `.env.example`). Enforces the local-only,
`sre-lab`-scoped safety posture described in specs/007-safety-and-permissions.md.
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Mode(StrEnum):
    DRY_RUN = "dry-run"
    SUGGEST_ONLY = "suggest-only"
    APPLY_LOCAL_LAB = "apply-local-lab"


class Settings(BaseSettings):
    """Typed settings; the namespace/context locks are safety-critical."""

    model_config = SettingsConfigDict(
        env_file=str(REPO_ROOT / ".env"),
        env_prefix="",
        extra="ignore",
    )

    # LLM (local Ollama only)
    ollama_base_url: str = Field("http://localhost:11434", alias="OLLAMA_BASE_URL")
    model: str = Field("qwen3:8b", alias="SRE_MODEL")
    model_fallback: str = Field("qwen2.5:3b", alias="SRE_MODEL_FALLBACK")
    temperature: float = Field(0.1, alias="SRE_TEMPERATURE")

    # Safety / scope
    mode: Mode = Field(Mode.DRY_RUN, alias="SRE_MODE")
    namespace: str = Field("sre-lab", alias="SRE_NAMESPACE")
    kube_context: str = Field("minikube", alias="SRE_KUBE_CONTEXT")

    # Loop guards
    max_iterations: int = Field(6, alias="SRE_MAX_ITERATIONS")
    max_tool_calls: int = Field(40, alias="SRE_MAX_TOOL_CALLS")
    max_elapsed_seconds: int = Field(600, alias="SRE_MAX_ELAPSED_SECONDS")

    # Observability / memory
    runs_dir: Path = Field(REPO_ROOT / "runs", alias="SRE_RUNS_DIR")
    memory_db: Path = Field(REPO_ROOT / "memory" / "sre_memory.sqlite", alias="SRE_MEMORY_DB")
    log_level: str = Field("INFO", alias="SRE_LOG_LEVEL")

    @field_validator("namespace")
    @classmethod
    def _namespace_locked(cls, v: str) -> str:
        # Hard safety rail: this project only ever operates on the lab namespace.
        if v != "sre-lab":
            raise ValueError("SRE_NAMESPACE is locked to 'sre-lab' for safety.")
        return v

    @property
    def can_mutate(self) -> bool:
        return self.mode is Mode.APPLY_LOCAL_LAB


def load_settings() -> Settings:
    return Settings()
