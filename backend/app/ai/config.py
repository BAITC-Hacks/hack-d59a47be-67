"""AI-only settings. Reading environment variables never makes a network call."""

from __future__ import annotations

import math
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field

from pydantic import SecretStr

DEFAULT_MODEL = "gpt-4.1-mini-2025-04-14"


class ConfigurationError(ValueError):
    """A deliberately redacted error: environment values are never interpolated."""


@dataclass(frozen=True)
class AISettings:
    api_key: SecretStr = field(repr=False)
    model: str = DEFAULT_MODEL
    timeout_seconds: float = 6.0

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> AISettings:
        env = os.environ if environ is None else environ
        key = env.get("OPENAI_API_KEY", "").strip()
        if not key or any(character.isspace() for character in key):
            raise ConfigurationError("missing_or_invalid_api_key")
        model = env.get("OPENAI_MODEL", DEFAULT_MODEL).strip()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", model):
            raise ConfigurationError("invalid_model_setting")
        try:
            timeout = float(env.get("AI_TIMEOUT_SECONDS", "6.0"))
        except (TypeError, ValueError):
            raise ConfigurationError("invalid_timeout_setting") from None
        if not math.isfinite(timeout) or not 0.1 <= timeout <= 6.0:
            raise ConfigurationError("invalid_timeout_setting")
        return cls(api_key=SecretStr(key), model=model, timeout_seconds=timeout)
