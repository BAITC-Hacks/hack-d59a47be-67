"""Exercise the optional boundary using synthetic in-process fakes, without AI code."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from backend.app import ai_adapter
from backend.app.ai_adapter import AIAdapter
from backend.app.contracts import RecommendationContext, RecommendationResult


@pytest.fixture
def context():
    return RecommendationContext.model_validate(
        {
            "contract_version": "1",
            "data_version": "synthetic-v1",
            "state_version": 0,
            "scenario_date": "2026-09-23",
            "profile": {"profile_ref": "synthetic-anonymous", "role": "Synthetic Analyst", "grade": "Junior"},
            "goal": {"target_role": "Synthetic Analyst", "target_grade": "Middle"},
            "current_skills": [{"skill_id": "SYNTH_SKILL", "level": 1.0}],
            "gaps": [{"skill_id": "SYNTH_SKILL", "current_level": 1.0, "target_level": 2.0, "gap": 1.0}],
            "eligible_candidates": [
                {
                    "event_id": "SYNTH_EVENT_1",
                    "title": "Synthetic practice one",
                    "effects": [
                        {"skill_id": "SYNTH_SKILL", "before": 1.0, "after": 2.0, "delta": 1.0},
                    ],
                    "evidence_ids": ["SYNTH_EVIDENCE_1"],
                },
                {
                    "event_id": "SYNTH_EVENT_2",
                    "title": "Synthetic practice two",
                    "effects": [
                        {"skill_id": "SYNTH_SKILL", "before": 1.0, "after": 1.5, "delta": 0.5},
                    ],
                    "evidence_ids": ["SYNTH_EVIDENCE_2"],
                },
            ],
            "facts": [
                {
                    "evidence_id": "SYNTH_EVIDENCE_1",
                    "kind": "candidate",
                    "subject_id": "SYNTH_EVENT_1",
                    "fact": "Synthetic event has a backend-calculated effect of one.",
                },
                {
                    "evidence_id": "SYNTH_EVIDENCE_2",
                    "kind": "candidate",
                    "subject_id": "SYNTH_EVENT_2",
                    "fact": "Synthetic event has a backend-calculated effect of half a point.",
                },
            ],
            "limit": 2,
        }
    )


def selection(event_id="SYNTH_EVENT_1", evidence_id="SYNTH_EVIDENCE_1"):
    return {
        "event_id": event_id,
        "explanation": {
            "text": "Synthetic explanation grounded in backend facts.",
            "evidence_ids": [evidence_id],
        },
    }


def injected_adapter(payload):
    adapter = AIAdapter()

    async def fake_recommend(context):
        return payload

    adapter._recommend = fake_recommend
    return adapter


def test_disabled_ai_does_not_import_optional_module(monkeypatch, context):
    def forbidden_import(name):
        pytest.fail("Disabled AI must not load an optional module")

    monkeypatch.setattr(ai_adapter.importlib, "import_module", forbidden_import)
    adapter = AIAdapter()
    assert adapter.capability == {"status": "not_configured", "engine": "none"}
    result = asyncio.run(adapter.recommend(context))
    assert result.model_dump() == {"status": "not_configured", "engine": "none", "recommendations": []}


@pytest.mark.parametrize("import_error", [ModuleNotFoundError, RuntimeError])
def test_missing_or_broken_optional_module_does_not_break_startup(monkeypatch, context, import_error):
    def missing_module(name):
        assert name == "backend.app.ai"
        raise import_error("synthetic optional-module error")

    monkeypatch.setattr(ai_adapter.importlib, "import_module", missing_module)
    adapter = AIAdapter(enabled=True)
    assert asyncio.run(adapter.recommend(context)).status == "not_configured"
    assert adapter.capability["engine"] == "none"


def test_sync_implementation_is_not_configured(monkeypatch, context):
    monkeypatch.setattr(
        ai_adapter.importlib, "import_module", lambda _: SimpleNamespace(recommend=lambda _: None)
    )
    adapter = AIAdapter(enabled=True)
    assert asyncio.run(adapter.recommend(context)).status == "not_configured"


def test_valid_async_module_is_loaded_and_preserves_input(monkeypatch, context):
    snapshot = context.model_dump()

    async def implementation(received):
        assert received is not context
        received.eligible_candidates.clear()
        received.facts[0].fact = "synthetic mutation inside implementation"
        return RecommendationResult.model_validate(
            {
                "status": "ok",
                "engine": "oleg",
                "recommendations": [selection()],
            }
        )

    monkeypatch.setattr(
        ai_adapter.importlib, "import_module", lambda _: SimpleNamespace(recommend=implementation)
    )
    adapter = AIAdapter(enabled=True)
    result = asyncio.run(adapter.recommend(context))
    assert adapter.capability == {"status": "configured", "engine": "oleg"}
    assert result.status == "ok" and result.recommendations[0].event_id == "SYNTH_EVENT_1"
    assert context.model_dump() == snapshot


@pytest.mark.parametrize(
    "payload",
    [
        {"status": "ok", "engine": "oleg", "recommendations": [selection("UNKNOWN_EVENT")]},
        {"status": "ok", "engine": "oleg", "recommendations": [selection(evidence_id="UNKNOWN_EVIDENCE")]},
        {"status": "ok", "engine": "oleg", "recommendations": [selection(), selection()]},
        {"status": "ok", "engine": "oleg", "recommendations": []},
        {"status": "ok", "engine": "none", "recommendations": [selection()]},
        {"status": "no_candidates", "engine": "oleg", "recommendations": []},
        {"status": "not_configured", "engine": "none", "recommendations": []},
        {"status": "ok", "engine": "oleg", "recommendations": [selection()], "private_profile": "forbidden"},
        {"status": "unavailable", "engine": "oleg", "recommendations": [selection()]},
        {"unexpected": "shape"},
    ],
)
def test_rejects_invalid_or_ungrounded_results(context, payload):
    result = asyncio.run(injected_adapter(payload).recommend(context))
    assert result.model_dump() == {"status": "unavailable", "engine": "oleg", "recommendations": []}


def test_enforces_request_limit_even_below_global_limit(context):
    context.limit = 1
    payload = {
        "status": "ok",
        "engine": "oleg",
        "recommendations": [
            selection(),
            selection("SYNTH_EVENT_2", "SYNTH_EVIDENCE_2"),
        ],
    }
    assert asyncio.run(injected_adapter(payload).recommend(context)).status == "unavailable"


def test_revalidates_constructed_model_instances(context):
    malformed = RecommendationResult.model_construct(status="ok", engine="oleg", recommendations=[])
    assert asyncio.run(injected_adapter(malformed).recommend(context)).status == "unavailable"


def test_empty_candidates_is_distinct_from_missing_ai(context):
    context.eligible_candidates.clear()
    result = asyncio.run(
        injected_adapter(
            {
                "status": "no_candidates",
                "engine": "oleg",
                "recommendations": [],
            }
        ).recommend(context)
    )
    assert result.status == "no_candidates" and result.engine == "oleg"
    assert asyncio.run(AIAdapter().recommend(context)).status == "not_configured"


def test_ai_exception_becomes_safe_unavailable(context):
    adapter = AIAdapter()

    async def failed(received):
        raise RuntimeError("synthetic private implementation information")

    adapter._recommend = failed
    result = asyncio.run(adapter.recommend(context))
    assert result.model_dump() == {"status": "unavailable", "engine": "oleg", "recommendations": []}


def test_timeout_is_bounded_without_waiting_for_deadline(monkeypatch, context):
    async def timeout(awaitable, *, timeout):
        assert timeout == 7
        awaitable.close()
        raise TimeoutError("synthetic timeout")

    monkeypatch.setattr(ai_adapter.asyncio, "wait_for", timeout)
    adapter = injected_adapter({"status": "ok", "engine": "oleg", "recommendations": [selection()]})
    assert asyncio.run(adapter.recommend(context)).status == "unavailable"


def test_cancellation_is_propagated(context):
    adapter = AIAdapter()

    async def cancelled(received):
        raise asyncio.CancelledError()

    adapter._recommend = cancelled
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(adapter.recommend(context))
