"""Optional AI plugin discovered by the existing backend AIAdapter."""

from backend.app.contracts import RecommendationContext, RecommendationResult

from .config import AISettings, ConfigurationError
from .provider import AsyncOpenAIProvider
from .service import RecommendationEngine, unavailable
from .validation import InvalidContext, validate_context

__all__ = ["recommend"]


async def recommend(context: RecommendationContext) -> RecommendationResult:
    """Select using OpenAI; report unavailability without disguising a fallback as AI.

    AI_ENABLED is controlled by the backend adapter. This module does not read .env
    files, import a database, recalculate skills, or modify the shared contract.
    """
    try:
        validate_context(context)
    except (InvalidContext, TypeError, ValueError, AttributeError):
        return unavailable("invalid_context")
    if not context.eligible_candidates:
        return RecommendationResult(status="no_candidates", engine="oleg", recommendations=[])
    try:
        settings = AISettings.from_env()
    except ConfigurationError:
        return unavailable("configuration")
    provider = AsyncOpenAIProvider(
        api_key=settings.api_key.get_secret_value(),
        model=settings.model,
        timeout_seconds=settings.timeout_seconds,
    )
    return await RecommendationEngine(provider, settings.timeout_seconds).recommend(context)
