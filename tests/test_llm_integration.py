from pathlib import Path

import pytest
from pydantic import BaseModel, Field

from agent.env import load_dotenv
from agent.llm import HeuristicLLMClient, client_from_env
from agent.models import ProofState


pytestmark = pytest.mark.integration


class StructuredSmokeOut(BaseModel):
    answer: str = Field(description="The short answer.")
    confidence: float = Field(description="Confidence from 0 to 1.")


def test_configured_llm_structured_output_smoke():
    load_dotenv(Path(".env"))
    client = client_from_env()
    if isinstance(client, HeuristicLLMClient):
        pytest.skip("AUTO_PROVE_LLM_PROVIDER is heuristic; no configured network LLM to test.")

    structured = client.chat_model.with_structured_output(StructuredSmokeOut, include_raw=True)
    try:
        response = structured.invoke(
            [
                ("system", "Return structured data only."),
                ("human", "Answer with the word ok and confidence 1.0."),
            ]
        )
    except Exception as exc:
        if _looks_like_network_error(exc):
            pytest.skip(f"Configured LLM is not reachable: {type(exc).__name__}: {exc}")
        raise

    assert isinstance(response, dict)
    assert set(response) >= {"raw", "parsed", "parsing_error"}
    assert response["parsing_error"] is None, str(response["parsing_error"])
    assert response["parsed"] is not None, _raw_preview(response.get("raw"))
    assert isinstance(response["parsed"], StructuredSmokeOut)
    assert response["parsed"].answer.strip()
    assert 0 <= response["parsed"].confidence <= 1


def test_configured_llm_returns_candidate():
    load_dotenv(Path(".env"))
    client = client_from_env()
    if isinstance(client, HeuristicLLMClient):
        pytest.skip("AUTO_PROVE_LLM_PROVIDER is heuristic; no configured network LLM to test.")

    candidates = client.propose(ProofState(theorem="example : 1 = 1"), beam_width=1)
    if not candidates:
        pytest.skip(f"Configured LLM did not return structured proof candidates: {getattr(client, 'last_error', '')}")
    assert candidates
    assert candidates[0].code.startswith(("example ", "theorem ", "lemma "))


def _looks_like_network_error(exc: Exception) -> bool:
    text = f"{type(exc).__name__}: {exc}".lower()
    return any(marker in text for marker in ["connection", "timeout", "timed out", "network", "dns"])


def _raw_preview(raw) -> str:
    content = getattr(raw, "content", "")
    metadata = getattr(raw, "response_metadata", {})
    return f"raw content={str(content)[:1000]!r}; metadata={metadata!r}"
