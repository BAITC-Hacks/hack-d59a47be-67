"""AI orchestration contract checks; no live requests, dataset or credentials."""

import asyncio
import json
import warnings
from pathlib import Path

import pytest

from backend.app import ai
from backend.app.ai.config import DEFAULT_MODEL, AISettings, ConfigurationError
from backend.app.ai.prompts import SELECTION_INSTRUCTIONS
from backend.app.ai.provider import ProviderError
from backend.app.ai.service import RecommendationEngine
from backend.app.ai_adapter import AIAdapter
from backend.app.contracts import RecommendationContext

FIXTURE = Path(__file__).resolve().parents[2] / "contracts/examples/ai_context.synthetic.json"


@pytest.fixture
def context():
    return RecommendationContext.model_validate_json(FIXTURE.read_text())


class FakeSelection:
    def __init__(self, ids=None, error=None):
        self.ids = ids or ["SYNTHETIC_EVENT_001"]
        self.error = error
        self.calls = []

    async def select_events(self, payload, *, instructions):
        self.calls.append((payload, instructions))
        if self.error:
            raise self.error
        return self.ids


def test_engine_selects_and_builds_four_grounded_factors_without_mutating(context):
    before = context.model_dump_json()
    provider = FakeSelection()
    result = asyncio.run(RecommendationEngine(provider).recommend(context))
    assert result.status == "ok" and result.engine == "oleg"
    assert context.model_dump_json() == before
    facts = {fact.evidence_id: fact for fact in context.facts}
    selected = result.recommendations[0]
    assert {facts[key].kind for key in selected.explanation.evidence_ids} == {
        "goal",
        "gap",
        "history",
        "candidate",
    }
    assert selected.explanation.text == " ".join(facts[key].fact for key in selected.explanation.evidence_ids)
    assert provider.calls[0][1] == SELECTION_INSTRUCTIONS
    assert "full_name" not in json.dumps(provider.calls[0][0])


@pytest.mark.parametrize("ids", [["NOT_ELIGIBLE"], ["SYNTHETIC_EVENT_001"] * 2, [], "SYNTHETIC_EVENT_001"])
def test_invalid_provider_selection_is_unavailable_not_invented_fallback(context, ids):
    provider = FakeSelection()
    provider.ids = ids
    result = asyncio.run(RecommendationEngine(provider).recommend(context))
    assert result.status == "unavailable" and result.recommendations == []
    assert len(provider.calls) == 1


def test_missing_evidence_fails_before_provider(context):
    context.facts = [fact for fact in context.facts if fact.kind != "history"]
    provider = FakeSelection()
    result = asyncio.run(RecommendationEngine(provider).recommend(context))
    assert result.status == "unavailable"
    assert not provider.calls


def test_corrupted_context_does_not_leak_values_in_pydantic_warnings(context, caplog, capsys):
    context.profile.role = ["SYNTHETIC_PRIVATE_MARKER"]
    provider = FakeSelection()
    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter("always")
        result = asyncio.run(RecommendationEngine(provider).recommend(context))
    assert result.status == "unavailable" and not provider.calls
    output = capsys.readouterr()
    assert "SYNTHETIC_PRIVATE_MARKER" not in output.out + output.err + caplog.text
    assert not captured


def test_empty_candidates_skip_provider(context):
    context.eligible_candidates.clear()
    provider = FakeSelection()
    result = asyncio.run(RecommendationEngine(provider).recommend(context))
    assert result.status == "no_candidates" and result.recommendations == []
    assert not provider.calls


@pytest.mark.parametrize("error", [ProviderError("timeout"), RuntimeError("DO_NOT_LOG_SECRET")])
def test_errors_are_redacted_and_do_not_retry(context, error, caplog):
    provider = FakeSelection(error=error)
    result = asyncio.run(RecommendationEngine(provider).recommend(context))
    assert result.status == "unavailable"
    assert len(provider.calls) == 1
    assert "DO_NOT_LOG_SECRET" not in caplog.text


def test_deadline_cancels_provider_and_keeps_event_loop_responsive(context):
    cancelled = []

    class SlowProvider:
        async def select_events(self, payload, *, instructions):
            try:
                await asyncio.sleep(30)
            finally:
                cancelled.append(True)

    async def scenario():
        ticks = []

        async def tick():
            await asyncio.sleep(0.005)
            ticks.append(True)

        result, _ = await asyncio.gather(
            RecommendationEngine(SlowProvider(), 0.03).recommend(context), tick()
        )
        assert result.status == "unavailable" and ticks == [True]

    asyncio.run(scenario())
    assert cancelled == [True]


def test_external_cancellation_propagates(context):
    provider = FakeSelection(error=asyncio.CancelledError())
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(RecommendationEngine(provider).recommend(context))


def test_provider_mutation_does_not_change_facts_used_for_explanation(context):
    class MutatingProvider:
        async def select_events(self, payload, *, instructions):
            payload["facts"][0]["fact"] = "UNTRUSTED_CHANGED_FACT"
            return [payload["eligible_candidates"][0]["event_id"]]

    result = asyncio.run(RecommendationEngine(MutatingProvider()).recommend(context))
    assert result.status == "ok"
    assert "UNTRUSTED_CHANGED_FACT" not in result.recommendations[0].explanation.text


def test_disabled_backend_never_calls_module_or_provider(context, monkeypatch):
    async def forbidden(_):
        pytest.fail("Disabled adapter must not call AI")

    monkeypatch.setattr(ai, "recommend", forbidden)
    assert asyncio.run(AIAdapter(enabled=False).recommend(context)).status == "not_configured"


def test_enabled_backend_loads_actual_export_and_uses_configured_provider(context, monkeypatch):
    provider = FakeSelection()
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-key-never-sent")
    monkeypatch.setattr(ai, "AsyncOpenAIProvider", lambda **kwargs: provider)
    adapter = AIAdapter(enabled=True)
    assert adapter.capability == {"status": "configured", "engine": "oleg"}
    assert asyncio.run(adapter.recommend(context)).status == "ok"
    assert len(provider.calls) == 1


def test_missing_key_returns_safe_unavailable_without_network(context, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    def forbidden(**kwargs):
        pytest.fail("Missing credentials must not construct a provider")

    monkeypatch.setattr(ai, "AsyncOpenAIProvider", forbidden)
    assert asyncio.run(ai.recommend(context)).status == "unavailable"


def test_configuration_hides_secret_and_has_configurable_snapshot():
    settings = AISettings.from_env({"OPENAI_API_KEY": "synthetic-test-secret"})
    assert "synthetic-test-secret" not in repr(settings)
    assert "synthetic-test-secret" not in str(settings.api_key)
    assert settings.model == DEFAULT_MODEL
    changed = AISettings.from_env({"OPENAI_API_KEY": "synthetic", "OPENAI_MODEL": "test-model"})
    assert changed.model == "test-model"


@pytest.mark.parametrize("value", ["NaN", "Infinity", "0", "7", "-1", "not-a-number"])
def test_config_rejects_unbounded_or_invalid_timeouts(value):
    with pytest.raises(ConfigurationError, match="invalid_timeout_setting"):
        AISettings.from_env({"OPENAI_API_KEY": "synthetic", "AI_TIMEOUT_SECONDS": value})


@pytest.mark.parametrize("env", [{}, {"OPENAI_API_KEY": " "}, {"OPENAI_API_KEY": "with\nnewline"}])
def test_missing_or_invalid_key_raises_only_static_message(env):
    with pytest.raises(ConfigurationError, match="^missing_or_invalid_api_key$"):
        AISettings.from_env(env)
