"""In-process boundary only; no LLM client, external service or arithmetic."""

import asyncio
import importlib
import inspect

from .contracts import RecommendationContext, RecommendationResult


class AIAdapter:
    def __init__(self, enabled: bool = False):
        self._recommend = None
        if enabled:
            try:
                implementation = importlib.import_module("backend.app.ai")
                recommend = getattr(implementation, "recommend", None)
                if inspect.iscoroutinefunction(recommend):
                    self._recommend = recommend
            except Exception:
                # Missing/broken optional AI must not prevent infrastructure startup.
                pass

    @property
    def capability(self) -> dict:
        return {
            "status": "configured" if self._recommend else "not_configured",
            "engine": "oleg" if self._recommend else "none",
        }

    async def recommend(self, context: RecommendationContext) -> RecommendationResult:
        if self._recommend is None:
            return RecommendationResult(status="not_configured", engine="none", recommendations=[])
        try:
            result = await asyncio.wait_for(self._recommend(context.model_copy(deep=True)), timeout=10)
            if isinstance(result, RecommendationResult):
                result = result.model_dump()
            result = RecommendationResult.model_validate(result)
            allowed = {item.event_id for item in context.eligible_candidates}
            evidence = {fact.evidence_id for fact in context.facts}
            if result.engine != "oleg" or len(result.recommendations) > context.limit:
                raise ValueError("Invalid engine or limit")
            if any(
                item.event_id not in allowed or not set(item.explanation.evidence_ids) <= evidence
                for item in result.recommendations
            ):
                raise ValueError("Unknown candidate or evidence")
            if result.status == "no_candidates" and context.eligible_candidates:
                raise ValueError("Eligible candidates are present")
            return result
        except Exception:
            return RecommendationResult(status="unavailable", engine="oleg", recommendations=[])
