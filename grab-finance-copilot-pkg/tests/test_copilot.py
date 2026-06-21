"""Agent-loop convergence and fallback tests."""
from types import SimpleNamespace

from google import genai

from core import copilot


def _tool_call_response():
    function_call = SimpleNamespace(
        name="get_group_financials",
        args={"metrics": ["revenue"], "years": ["FY2025"]},
    )
    part = SimpleNamespace(function_call=function_call, text=None)
    content = SimpleNamespace(parts=[part])
    return SimpleNamespace(candidates=[SimpleNamespace(content=content)])


def _text_response(text: str):
    part = SimpleNamespace(function_call=None, text=text)
    content = SimpleNamespace(parts=[part])
    return SimpleNamespace(candidates=[SimpleNamespace(content=content)])


class _FakeModels:
    def __init__(self, synthesis_error: bool = False):
        self.calls = 0
        self.synthesis_error = synthesis_error

    def generate_content(self, **kwargs):
        self.calls += 1
        if kwargs["config"].tools:
            return _tool_call_response()
        if self.synthesis_error:
            raise RuntimeError("synthesis unavailable")
        return _text_response("Grab's FY2025 revenue was $3,370M.")


class _FakeClient:
    def __init__(self, synthesis_error: bool = False):
        self.models = _FakeModels(synthesis_error=synthesis_error)


class _NoToolModels:
    def generate_content(self, **kwargs):
        return _text_response("I can answer without evidence.")


class _NoToolClient:
    def __init__(self):
        self.models = _NoToolModels()


def _configure_fake_vertex(monkeypatch, client):
    monkeypatch.setenv("VERTEX_PROJECT_ID", "test-project")
    monkeypatch.setattr(copilot, "MAX_AGENT_STEPS", 2)
    monkeypatch.setattr(genai, "Client", lambda **kwargs: client)


def test_step_limit_forces_verified_synthesis(monkeypatch):
    client = _FakeClient()
    _configure_fake_vertex(monkeypatch, client)

    response = copilot.ask("What was Grab's revenue in FY2025?")

    assert client.models.calls == 3
    assert response.mode == "agent"
    assert response.verification is not None and response.verification.ok
    assert response.verification.checked == 1
    assert "did not converge" not in response.answer.lower()


def test_synthesis_failure_returns_collected_facts(monkeypatch):
    client = _FakeClient(synthesis_error=True)
    _configure_fake_vertex(monkeypatch, client)

    response = copilot.ask("What was Grab's revenue in FY2025?")

    assert response.mode == "retrieval_only"
    assert len(response.retrieval.facts) == 1
    assert response.retrieval.facts[0].value == 3370
    assert "did not converge" not in response.answer.lower()
    assert "synthesis unavailable" not in response.answer


def test_model_answer_without_tool_evidence_falls_back_to_retrieval(monkeypatch):
    monkeypatch.setenv("VERTEX_PROJECT_ID", "test-project")
    monkeypatch.setattr(genai, "Client", lambda **kwargs: _NoToolClient())

    response = copilot.ask("What was Grab's revenue in FY2025?")

    assert response.mode == "retrieval_only"
    assert response.retrieval.facts
    assert "no tool evidence" in response.answer
