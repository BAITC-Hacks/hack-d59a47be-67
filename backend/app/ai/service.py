"""Async recommendation orchestration using the team's existing Python models."""

from __future__ import annotations

import asyncio
import logging
import math
from typing import Protocol

from backend.app.contracts import RecommendationContext, RecommendationResult

from .prompts import PROMPT_VERSION, SELECTION_INSTRUCTIONS
from .provider import ProviderError
from .validation import InvalidContext, InvalidSelection, build_result, validate_context

logger = logging.getLogger(__name__)


class SelectionProvider(Protocol):
    async def select_events(self, payload: dict, *, instructions: str) -> list[str]: ...


def unavailable(reason: str) -> RecommendationResult:
    # Only fixed internal categories are logged, never exception text, payload or credentials.
    allowed = {
        "configuration",
        "invalid_context",
        "invalid_output",
        "timeout",
        "provider_unavailable",
        "internal_error",
    }
    logger.warning(
        "AI recommendation unavailable: %s (%s)",
        reason if reason in allowed else "internal_error",
        PROMPT_VERSION,
    )
    return RecommendationResult(status="unavailable", engine="oleg", recommendations=[])


class RecommendationEngine:
    def __init__(self, provider: SelectionProvider, timeout_seconds: float = 6.0):
        if not math.isfinite(timeout_seconds) or not 0 < timeout_seconds <= 6.0:
            raise ValueError("AI deadline must fit within the backend's seven-second timeout")
        self.provider = provider
        self.timeout_seconds = timeout_seconds

    async def recommend(self, context: RecommendationContext) -> RecommendationResult:
        try:
            # Never mutate the caller's prepared context or trust construct/copy bypasses.
            snapshot = RecommendationContext.model_validate_json(context.model_dump_json(warnings=False))
            validate_context(snapshot)
            if not snapshot.eligible_candidates:
                return RecommendationResult(status="no_candidates", engine="oleg", recommendations=[])
            async with asyncio.timeout(self.timeout_seconds):
                event_ids = await self.provider.select_events(
                    snapshot.model_dump(mode="json"), instructions=SELECTION_INSTRUCTIONS
                )
            return build_result(snapshot, event_ids)
        except InvalidContext:
            return unavailable("invalid_context")
        except InvalidSelection:
            return unavailable("invalid_output")
        except ProviderError as error:
            return unavailable(error.code)
        except TimeoutError:
            return unavailable("timeout")
        except Exception:
            return unavailable("internal_error")
        # CancelledError is intentionally not caught. HTTP disconnect/outer timeout cancels I/O.
