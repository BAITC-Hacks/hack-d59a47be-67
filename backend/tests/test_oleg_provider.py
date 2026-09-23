"""Synthetic offline transport tests: never use environment credentials or network."""

import asyncio
import builtins
import copy
import json
import traceback

import httpx
import pytest

from backend.app.ai.provider import (
    MAX_REQUEST_BYTES,
    MAX_RESPONSE_BYTES,
    RESPONSES_URL,
    AsyncOpenAIProvider,
    ProviderError,
)

FAKE_KEY = "synthetic-key-for-offline-tests"
INSTRUCTIONS = "Choose events from the supplied data and return event_ids as JSON."


def payload():
    return {
        "eligible_candidates": [
            {"event_id": "SYNTHETIC_EVENT_A", "title": "Synthetic A"},
            {"event_id": "SYNTHETIC_EVENT_B", "title": "Synthetic B"},
        ],
        "limit": 2,
    }


def envelope(selection=None):
    return {
        "status": "completed",
        "error": None,
        "incomplete_details": None,
        "output": [
            {
                "type": "message",
                "role": "assistant",
                "status": "completed",
                "content": [
                    {
                        "type": "output_text",
                        "text": json.dumps(selection or {"event_ids": ["SYNTHETIC_EVENT_B"]}),
                    }
                ],
            }
        ],
    }


def invoke(handler, *, data=None, timeout=6.0):
    provider = AsyncOpenAIProvider(
        api_key=FAKE_KEY,
        model="synthetic-model",
        timeout_seconds=timeout,
        transport=httpx.MockTransport(handler),
    )
    return asyncio.run(provider.select_events(payload() if data is None else data, instructions=INSTRUCTIONS))


def test_stateless_structured_request_and_ordered_selection(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-be-read")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://not-the-provider.invalid")
    monkeypatch.setenv("HTTPS_PROXY", "https://must-not-be-used.invalid")
    seen = []

    def handler(request):
        seen.append(request)
        assert request.method == "POST"
        assert str(request.url) == RESPONSES_URL
        assert request.headers["authorization"] == f"Bearer {FAKE_KEY}"
        assert request.headers["accept-encoding"] == "identity"
        body = json.loads(request.content)
        assert body["model"] == "synthetic-model"
        assert body["instructions"] == INSTRUCTIONS
        assert body["input"] == [{"role": "user", "content": json.dumps(payload(), separators=(",", ":"))}]
        assert body["store"] is False
        assert 128 <= body["max_output_tokens"] <= 2048
        assert "tools" not in body
        assert "previous_response_id" not in body
        assert "conversation" not in body
        assert FAKE_KEY not in request.content.decode()
        text_format = body["text"]["format"]
        assert text_format["type"] == "json_schema"
        assert text_format["strict"] is True
        schema = text_format["schema"]
        assert schema["required"] == ["event_ids"]
        assert schema["additionalProperties"] is False
        assert schema["properties"]["event_ids"] == {
            "type": "array",
            "items": {"type": "string", "enum": ["SYNTHETIC_EVENT_A", "SYNTHETIC_EVENT_B"]},
            "minItems": 1,
            "maxItems": 2,
        }
        return httpx.Response(200, json=envelope({"event_ids": ["SYNTHETIC_EVENT_B", "SYNTHETIC_EVENT_A"]}))

    assert invoke(handler) == ["SYNTHETIC_EVENT_B", "SYNTHETIC_EVENT_A"]
    assert len(seen) == 1


@pytest.mark.parametrize("status", [301, 307, 400, 401, 429, 500, 503])
def test_http_error_no_retry_no_redirect_no_details(status, caplog):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            status,
            headers={"location": "https://untrusted.invalid", "retry-after": "0"},
            text=f"upstream error includes {FAKE_KEY} and confidential content",
        )

    with pytest.raises(ProviderError, match="^provider_unavailable$") as error:
        invoke(handler)
    assert len(calls) == 1
    assert error.value.code == "provider_unavailable"
    assert FAKE_KEY not in repr(error.value)
    assert "confidential" not in caplog.text


@pytest.mark.parametrize("exception_type", [httpx.ConnectError, httpx.RemoteProtocolError, httpx.ReadError])
def test_network_errors_are_sanitized(exception_type):
    def handler(request):
        raise exception_type(f"sensitive provider details {FAKE_KEY}", request=request)

    with pytest.raises(ProviderError) as error:
        invoke(handler)
    assert error.value.code == "provider_unavailable"
    rendered = "".join(traceback.format_exception(error.value))
    assert FAKE_KEY not in rendered


def test_http_timeout_is_classified_without_retry():
    calls = []

    def handler(request):
        calls.append(request)
        raise httpx.ReadTimeout(FAKE_KEY, request=request)

    with pytest.raises(ProviderError, match="^timeout$"):
        invoke(handler)
    assert len(calls) == 1


def test_wall_clock_deadline_cancels_waiting_transport():
    cancelled = []

    async def handler(request):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.append(True)

    with pytest.raises(ProviderError, match="^timeout$"):
        invoke(handler, timeout=0.01)
    assert cancelled == [True]


def test_outer_cancellation_is_propagated():
    async def scenario():
        started = asyncio.Event()
        cancelled = asyncio.Event()

        async def handler(request):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        provider = AsyncOpenAIProvider(FAKE_KEY, "synthetic", transport=httpx.MockTransport(handler))
        task = asyncio.create_task(provider.select_events(payload(), instructions=INSTRUCTIONS))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert cancelled.is_set()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "selection",
    [
        [],
        "not an object",
        {},
        {"event_ids": []},
        {"event_ids": "SYNTHETIC_EVENT_A"},
        {"event_ids": [None]},
        {"event_ids": [True]},
        {"event_ids": [[]]},
        {"event_ids": ["SYNTHETIC_EVENT_A", "SYNTHETIC_EVENT_A"]},
        {"event_ids": ["SYNTHETIC_EVENT_A", "SYNTHETIC_EVENT_B", "SYNTHETIC_EVENT_C"]},
        {"event_ids": ["UNKNOWN_EVENT"]},
        {"event_ids": ["SYNTHETIC_EVENT_A"], "status": "ok"},
    ],
)
def test_untrusted_selection_is_rejected(selection):
    response = envelope()
    response["output"][0]["content"][0]["text"] = json.dumps(selection)
    with pytest.raises(ProviderError, match="^invalid_output$"):
        invoke(lambda request: httpx.Response(200, json=response))


@pytest.mark.parametrize(
    "text",
    [
        '{"event_ids":["SYNTHETIC_EVENT_A"],"event_ids":["SYNTHETIC_EVENT_B"]}',
        '{"event_ids":[NaN]}',
        '{"event_ids":[Infinity]}',
        '{"event_ids":[-Infinity]}',
        '{"event_ids":[1e999]}',
        '```json\n{"event_ids":["SYNTHETIC_EVENT_A"]}\n```',
        '{"event_ids":',
    ],
)
def test_model_json_is_strict(text):
    response = envelope()
    response["output"][0]["content"][0]["text"] = text
    with pytest.raises(ProviderError, match="^invalid_output$"):
        invoke(lambda request: httpx.Response(200, json=response))


@pytest.mark.parametrize("kind", ["refusal", "incomplete", "failed", "missing", "multiple", "tool", "role"])
def test_response_must_have_one_completed_assistant_text(kind):
    response = envelope()
    message = response["output"][0]
    if kind == "refusal":
        message["content"] = [{"type": "refusal", "refusal": "No"}]
    elif kind == "incomplete":
        response["status"] = "incomplete"
        response["incomplete_details"] = {"reason": "max_output_tokens"}
    elif kind == "failed":
        response["error"] = {"code": "server_error", "message": "sensitive"}
    elif kind == "missing":
        response["output"] = []
        response["output_text"] = '{"event_ids":["SYNTHETIC_EVENT_A"]}'
    elif kind == "multiple":
        message["content"].append(copy.deepcopy(message["content"][0]))
    elif kind == "tool":
        response["output"].append({"type": "function_call", "name": "unrequested_action"})
    else:
        message["role"] = "user"
    with pytest.raises(ProviderError, match="^invalid_output$"):
        invoke(lambda request: httpx.Response(200, json=response))


def test_duplicate_messages_rejected_but_reasoning_item_allowed():
    response = envelope()
    response["output"].insert(0, {"type": "reasoning", "summary": []})
    assert invoke(lambda request: httpx.Response(200, json=response)) == ["SYNTHETIC_EVENT_B"]
    response["output"].append(copy.deepcopy(response["output"][-1]))
    with pytest.raises(ProviderError, match="^invalid_output$"):
        invoke(lambda request: httpx.Response(200, json=response))


@pytest.mark.parametrize("raw", [b"[]", b"\xff", b'{"status":"completed","status":"failed"}', b"["])
def test_response_json_must_be_strict_utf8_object(raw):
    with pytest.raises(ProviderError, match="^invalid_output$"):
        invoke(lambda request: httpx.Response(200, content=raw))


def test_huge_request_is_rejected_before_network():
    data = payload()
    data["facts"] = ["x" * MAX_REQUEST_BYTES]
    with pytest.raises(ProviderError, match="^invalid_output$"):
        invoke(lambda request: pytest.fail("oversized request reached network"), data=data)


def test_response_size_limit_interrupts_and_closes_stream():
    class ChunkStream(httpx.AsyncByteStream):
        consumed = 0
        closed = False

        async def __aiter__(self):
            for _ in range(40):
                self.consumed += 4096
                yield b" " * 4096

        async def aclose(self):
            self.closed = True

    stream = ChunkStream()
    with pytest.raises(ProviderError, match="^invalid_output$"):
        invoke(lambda request: httpx.Response(200, stream=stream))
    assert stream.consumed == MAX_RESPONSE_BYTES + 4096
    assert stream.closed


@pytest.mark.parametrize(
    "headers",
    [
        {"content-length": str(MAX_RESPONSE_BYTES + 1)},
        {"content-length": "not-a-number"},
        {"content-encoding": "gzip"},
    ],
)
def test_oversized_or_encoded_response_not_read(headers):
    class MustNotRead(httpx.AsyncByteStream):
        async def __aiter__(self):
            pytest.fail("invalid response body was read")
            yield b""

    with pytest.raises(ProviderError, match="^invalid_output$"):
        invoke(lambda request: httpx.Response(200, headers=headers, stream=MustNotRead()))


@pytest.mark.parametrize("limit", [True, 0, 4, "2", None])
def test_invalid_input_limit_never_reaches_network(limit):
    data = payload()
    data["limit"] = limit
    with pytest.raises(ProviderError, match="^invalid_output$"):
        invoke(lambda request: pytest.fail("invalid context reached network"), data=data)


def test_missing_optional_dependency_returns_safe_error(monkeypatch):
    real_import = builtins.__import__

    def blocked_import(name, *args, **kwargs):
        if name == "httpx":
            raise ImportError("httpx is not installed")
        return real_import(name, *args, **kwargs)

    provider = AsyncOpenAIProvider(FAKE_KEY, "synthetic-model")
    monkeypatch.setattr(builtins, "__import__", blocked_import)
    with pytest.raises(ProviderError, match="^provider_unavailable$"):
        asyncio.run(provider.select_events(payload(), instructions=INSTRUCTIONS))


@pytest.mark.parametrize("timeout", [0, -1, 7, float("nan"), float("inf"), True])
def test_invalid_deadline_and_safe_repr(timeout):
    with pytest.raises(ProviderError, match="^provider_unavailable$"):
        AsyncOpenAIProvider(FAKE_KEY, "synthetic", timeout_seconds=timeout)
    assert FAKE_KEY not in repr(AsyncOpenAIProvider(FAKE_KEY, "synthetic"))
