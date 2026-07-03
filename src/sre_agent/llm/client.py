"""Ollama-backed LLM with structured (pydantic) output and a fallback model.

Local only. If the model cannot produce valid structured output even after the
fallback, callers get None and fall back to deterministic reasoning — the loop must
never hard-depend on a small model behaving perfectly.
"""

from __future__ import annotations

from typing import TypeVar

from langchain_ollama import ChatOllama
from pydantic import BaseModel

from sre_agent.config import Settings
from sre_agent.observability import RunLogger

T = TypeVar("T", bound=BaseModel)


class LLM:
    def __init__(self, settings: Settings) -> None:
        self.s = settings

    def _chat(self, model: str) -> ChatOllama:
        return ChatOllama(
            model=model,
            base_url=self.s.ollama_base_url,
            temperature=self.s.temperature,
            reasoning=False,  # qwen3 thinking off -> faster, cleaner JSON
        )

    def structured(
        self, system: str, user: str, schema: type[T], logger: RunLogger | None = None
    ) -> T | None:
        """Return a validated `schema` instance, or None if the model cannot comply."""
        for model in (self.s.model, self.s.model_fallback):
            try:
                runnable = self._chat(model).with_structured_output(schema)
                result = runnable.invoke([("system", system), ("human", user)])
                if isinstance(result, schema):
                    if logger and model != self.s.model:
                        logger.warn("primary model failed; used fallback", model=model)
                    return result
                # some versions return a dict
                if isinstance(result, dict):
                    return schema.model_validate(result)
            except Exception as e:  # noqa: BLE001 - defensive, try fallback next
                if logger:
                    logger.warn("structured LLM call failed", model=model, error=str(e))
                continue
        return None
