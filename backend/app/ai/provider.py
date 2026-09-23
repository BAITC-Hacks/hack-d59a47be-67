"""Small, stateless Responses transport. Never returns provider text or logs secrets."""

from __future__ import annotations

import asyncio
import json
import math
import ssl
from functools import lru_cache
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    import httpx

RESPONSES_URL = "https://api.openai.com/v1/responses"
MAX_REQUEST_BYTES = 200 * 1024
MAX_RESPONSE_BYTES = 64 * 1024
MAX_OUTPUT_TOKENS = 1024
ProviderErrorCode = Literal["timeout", "provider_unavailable", "invalid_output"]


@lru_cache(maxsize=1)
def _tls_context() -> ssl.SSLContext:
    """Reuse only the public CA configuration, never a client or user state.

    This matches httpx's verified, trust_env=False default. Loading the pinned
    CA bundle once avoids repeating certificate parsing for every request.
    Credentials, connections and responses remain scoped to the async call.
    """
    import certifi

    context = ssl.create_default_context(cafile=certifi.where())
    context.keylog_filename = None
    return context


class ProviderError(Exception):
    """Only a fixed code can escape the transport; no body, URL, key or header."""

    def __init__(self, code: ProviderErrorCode):
        if code not in {"timeout", "provider_unavailable", "invalid_output"}:
            code = "provider_unavailable"
        self.code = code
        super().__init__(code)


def _object_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _finite_float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("non-finite number")
    return number


def _reject_constant(value: str) -> None:
    raise ValueError("non-finite constant")


def _load_json(value: str | bytes) -> Any:
    try:
        if isinstance(value, bytes):
            value = value.decode("utf-8")
        return json.loads(
            value,
            object_pairs_hook=_object_without_duplicates,
            parse_float=_finite_float,
            parse_constant=_reject_constant,
        )
    except (ValueError, TypeError, RecursionError):
        raise ProviderError("invalid_output") from None


def _selection(response: bytes, allowed_ids: list[str], limit: int) -> list[str]:
    envelope = _load_json(response)
    if (
        not isinstance(envelope, dict)
        or envelope.get("status") != "completed"
        or envelope.get("error") is not None
        or envelope.get("incomplete_details") is not None
        or not isinstance(envelope.get("output"), list)
    ):
        raise ProviderError("invalid_output")

    messages = []
    for item in envelope["output"]:
        if not isinstance(item, dict):
            raise ProviderError("invalid_output")
        if item.get("type") == "reasoning":
            continue
        if item.get("type") != "message":
            # No tools were supplied; do not accept function calls or other actions.
            raise ProviderError("invalid_output")
        messages.append(item)
    if len(messages) != 1:
        raise ProviderError("invalid_output")
    message = messages[0]
    content = message.get("content")
    if (
        message.get("role") != "assistant"
        or message.get("status") != "completed"
        or not isinstance(content, list)
        or len(content) != 1
        or not isinstance(content[0], dict)
        or content[0].get("type") != "output_text"
        or not isinstance(content[0].get("text"), str)
    ):
        # Refusals, multiple text parts and incomplete messages all fail closed.
        raise ProviderError("invalid_output")

    selection = _load_json(content[0]["text"])
    if not isinstance(selection, dict) or set(selection) != {"event_ids"}:
        raise ProviderError("invalid_output")
    event_ids = selection["event_ids"]
    if (
        not isinstance(event_ids, list)
        or not 1 <= len(event_ids) <= limit
        or any(not isinstance(event_id, str) or event_id not in allowed_ids for event_id in event_ids)
        or len(set(event_ids)) != len(event_ids)
    ):
        raise ProviderError("invalid_output")
    return event_ids


class AsyncOpenAIProvider:
    """One async request; ``transport`` is injectable for isolated synthetic tests.

    Payload is a prepared context from the orchestrator, never raw employee data.
    No environment proxy, custom URL, redirect, retry, tool or response persistence.
    This class deliberately does not load credentials or application configuration.
    """

    __slots__ = ("_api_key", "_model", "_timeout_seconds", "_transport")

    def __init__(
        self,
        api_key: str,
        model: str,
        timeout_seconds: float = 6.0,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        if (
            not isinstance(api_key, str)
            or not api_key.strip()
            or not isinstance(model, str)
            or not model.strip()
            or isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(timeout_seconds)
            or not 0 < timeout_seconds <= 6.0
        ):
            raise ProviderError("provider_unavailable")
        self._api_key = api_key
        self._model = model
        self._timeout_seconds = timeout_seconds
        self._transport = transport

    async def select_events(self, payload: dict[str, Any], *, instructions: str) -> list[str]:
        # Keep the optional AI HTTP dependency outside module import: the backend
        # must still start in the disabled-AI configuration without httpx installed.
        try:
            import httpx
        except ImportError:
            raise ProviderError("provider_unavailable") from None

        try:
            limit = payload["limit"]
            allowed_ids = [item["event_id"] for item in payload["eligible_candidates"]]
            if (
                type(limit) is not int
                or not 1 <= limit <= 3
                or not 1 <= len(allowed_ids) <= 1000
                or any(not isinstance(item, str) or not item for item in allowed_ids)
                or len(set(allowed_ids)) != len(allowed_ids)
                or not isinstance(instructions, str)
                or not instructions.strip()
            ):
                raise ValueError("invalid prepared input")
            context_json = json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
            body = json.dumps(
                {
                    "model": self._model,
                    "instructions": instructions,
                    "input": [{"role": "user", "content": context_json}],
                    "store": False,
                    "max_output_tokens": MAX_OUTPUT_TOKENS,
                    "text": {
                        "format": {
                            "type": "json_schema",
                            "name": "career_quest_event_selection",
                            "strict": True,
                            "schema": {
                                "type": "object",
                                "properties": {
                                    "event_ids": {
                                        "type": "array",
                                        "items": {"type": "string", "enum": allowed_ids},
                                        "minItems": 1,
                                        "maxItems": limit,
                                    }
                                },
                                "required": ["event_ids"],
                                "additionalProperties": False,
                            },
                        }
                    },
                },
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
            ).encode("utf-8")
            if len(body) > MAX_REQUEST_BYTES:
                raise ValueError("prepared input too large")
        except (KeyError, TypeError, ValueError, RecursionError):
            raise ProviderError("invalid_output") from None

        try:
            async with asyncio.timeout(self._timeout_seconds):
                async with httpx.AsyncClient(
                    transport=self._transport,
                    verify=_tls_context(),
                    timeout=self._timeout_seconds,
                    follow_redirects=False,
                    trust_env=False,
                ) as client:
                    async with client.stream(
                        "POST",
                        RESPONSES_URL,
                        headers={
                            "Authorization": f"Bearer {self._api_key}",
                            "Content-Type": "application/json",
                            "Accept": "application/json",
                            "Accept-Encoding": "identity",
                        },
                        content=body,
                    ) as response:
                        if response.status_code != 200:
                            # Never read a provider error body or forward its details.
                            raise ProviderError("provider_unavailable")
                        if response.headers.get("content-encoding", "identity").lower() != "identity":
                            raise ProviderError("invalid_output")
                        length = response.headers.get("content-length")
                        if length is not None and (
                            not length.isdecimal() or int(length) > MAX_RESPONSE_BYTES
                        ):
                            raise ProviderError("invalid_output")
                        chunks = bytearray()
                        async for chunk in response.aiter_bytes(chunk_size=4096):
                            if len(chunks) + len(chunk) > MAX_RESPONSE_BYTES:
                                raise ProviderError("invalid_output")
                            chunks.extend(chunk)
            return _selection(bytes(chunks), allowed_ids, limit)
        except (TimeoutError, httpx.TimeoutException):
            raise ProviderError("timeout") from None
        except (httpx.HTTPError, ValueError, OSError, ImportError):
            raise ProviderError("provider_unavailable") from None
