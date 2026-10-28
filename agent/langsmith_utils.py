from __future__ import annotations

import os
from typing import Any


def langsmith_is_configured() -> bool:
    """Return whether LangSmith tracing looks intentionally enabled."""
    tracing = os.getenv("LANGSMITH_TRACING", "").strip().lower()
    if tracing not in {"1", "true", "yes", "on"}:
        return False
    return bool(os.getenv("LANGSMITH_API_KEY", "").strip())


def langsmith_project() -> str:
    return os.getenv("LANGSMITH_PROJECT", "").strip() or "default"


def langsmith_runnable_config(
    *,
    purpose: str,
    source: str,
    model: str,
) -> dict[str, Any]:
    tags = ["auto-prove", source, purpose.replace(" ", "-")]
    return {
        "run_name": f"auto-prove:{purpose}",
        "tags": tags,
        "metadata": {
            "app": "auto-prove",
            "llm_source": source,
            "model": model,
            "purpose": purpose,
        },
    }


def wait_for_langsmith_traces() -> None:
    """Flush LangChain/LangSmith background tracer workers when available."""
    if not langsmith_is_configured():
        return
    try:
        from langchain_core.tracers.langchain import wait_for_all_tracers
    except ImportError:
        return
    wait_for_all_tracers()
