"""Offline checks for reused trust configuration with per-call clients and secrets."""

import asyncio
import builtins
import copy
import json
import runpy
import ssl
import traceback

import certifi
import httpx
import pytest

from backend.app.ai import provider as provider_module
from backend.app.ai.provider import AsyncOpenAIProvider, ProviderError, _tls_context


@pytest.fixture(autouse=True)
def isolated_tls_cache():
    _tls_context.cache_clear()
    yield
    _tls_context.cache_clear()


def _payload(event_id):
    return {"eligible_candidates": [{"event_id": event_id, "title": "Synthetic event"}], "limit": 1}


def _response(event_id):
    return {
        "status": "completed",
        "output": [
            {
                "type": "message",
                "role": "assistant",
                "status": "completed",
                "content": [{"type": "output_text", "text": json.dumps({"event_ids": [event_id]})}],
            }
        ],
    }


def _observe_clients(monkeypatch):
    clients = []
    configurations = []
    real_client = httpx.AsyncClient

    def tracked_client(**kwargs):
        configurations.append(kwargs.copy())
        client = real_client(**kwargs)
        clients.append(client)
        return client

    monkeypatch.setattr(httpx, "AsyncClient", tracked_client)
    return clients, configurations


def test_cached_context_uses_certifi_with_hostname_and_certificate_verification(monkeypatch):
    monkeypatch.setenv("SSL_CERT_FILE", "/synthetic/nonexistent-ca-file.pem")
    monkeypatch.setenv("SSL_CERT_DIR", "/synthetic/nonexistent-ca-directory")
    reference = ssl.create_default_context(cafile=certifi.where())
    calls = []
    create_context = ssl.create_default_context

    def observed_creation(*args, **kwargs):
        calls.append((args, kwargs))
        return create_context(*args, **kwargs)

    monkeypatch.setattr(ssl, "create_default_context", observed_creation)
    context = _tls_context()
    assert _tls_context() is context
    assert calls == [((), {"cafile": certifi.where()})]
    assert context.protocol == ssl.PROTOCOL_TLS_CLIENT
    assert context.check_hostname is True
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.get_ca_certs(binary_form=True) == reference.get_ca_certs(binary_form=True)
    assert context.cert_store_stats()["x509_ca"] > 0
    assert _tls_context.cache_info().currsize == 1


def test_environment_cannot_enable_tls_session_key_logging(monkeypatch, tmp_path):
    monkeypatch.setenv("SSLKEYLOGFILE", str(tmp_path / "synthetic-keylog.txt"))
    context = _tls_context()
    # create_default_context may initially create a header-only file. No TLS
    # handshake occurs until the provider has disabled logging on the context.
    assert context.keylog_filename is None
    assert _tls_context() is context


def test_unreadable_ca_bundle_fails_safely_without_caching_failure(monkeypatch):
    create_context = ssl.create_default_context

    def fail_creation(*args, **kwargs):
        raise OSError("synthetic-sensitive-CA-path")

    monkeypatch.setattr(ssl, "create_default_context", fail_creation)
    provider = AsyncOpenAIProvider(
        "synthetic-key",
        "synthetic-model",
        transport=httpx.MockTransport(lambda request: pytest.fail("CA failure reached transport")),
    )
    with pytest.raises(ProviderError, match="^provider_unavailable$") as error:
        asyncio.run(provider.select_events(_payload("SYNTHETIC_A"), instructions="Select."))
    assert "synthetic-sensitive-CA-path" not in "".join(traceback.format_exception(error.value))
    assert _tls_context.cache_info().currsize == 0
    monkeypatch.setattr(ssl, "create_default_context", create_context)
    assert _tls_context().verify_mode == ssl.CERT_REQUIRED


def test_missing_optional_ca_dependency_fails_safely_when_ai_is_used(monkeypatch):
    real_import = builtins.__import__

    def blocked_import(name, *args, **kwargs):
        if name == "certifi":
            raise ImportError("synthetic-sensitive-module-path")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocked_import)
    provider = AsyncOpenAIProvider(
        "synthetic-key",
        "synthetic-model",
        transport=httpx.MockTransport(lambda request: pytest.fail("missing dependency reached transport")),
    )
    with pytest.raises(ProviderError, match="^provider_unavailable$") as error:
        asyncio.run(provider.select_events(_payload("SYNTHETIC_A"), instructions="Select."))
    assert "synthetic-sensitive-module-path" not in "".join(traceback.format_exception(error.value))
    assert _tls_context.cache_info().currsize == 0


def test_context_reused_across_event_loops_without_reusing_clients_keys_or_payloads(monkeypatch):
    clients, configurations = _observe_clients(monkeypatch)
    source_payloads = [_payload("SYNTHETIC_LOOP_A"), _payload("SYNTHETIC_LOOP_B")]
    originals = copy.deepcopy(source_payloads)
    keys = ["synthetic-loop-key-A", "synthetic-loop-key-B"]
    seen = []
    loops = []

    def handler(request):
        loops.append(asyncio.get_running_loop())
        body = json.loads(request.content)
        data = json.loads(body["input"][0]["content"])
        seen.append((request.headers["authorization"], data))
        assert all(key not in request.content.decode() for key in keys)
        event_id = data["eligible_candidates"][0]["event_id"]
        return httpx.Response(200, json=_response(event_id))

    for key, data in zip(keys, source_payloads, strict=True):
        provider = AsyncOpenAIProvider(key, "synthetic-model", transport=httpx.MockTransport(handler))
        assert asyncio.run(provider.select_events(data, instructions="Return event IDs.")) == [
            data["eligible_candidates"][0]["event_id"]
        ]

    assert seen == [(f"Bearer {key}", data) for key, data in zip(keys, originals, strict=True)]
    assert source_payloads == originals
    assert loops[0] is not loops[1]
    assert all(loop.is_closed() for loop in loops)
    assert clients[0] is not clients[1]
    assert all(client.is_closed for client in clients)
    assert configurations[0]["verify"] is configurations[1]["verify"] is _tls_context()
    assert all(config["trust_env"] is False for config in configurations)
    assert all(config["follow_redirects"] is False for config in configurations)
    assert _tls_context.cache_info().misses == 1
    assert all("authorization" not in client.headers for client in clients)


@pytest.mark.parametrize("outcome", ["http_error", "cancelled"])
def test_client_is_closed_on_error_or_cancellation_with_shared_verification(monkeypatch, outcome):
    clients, configurations = _observe_clients(monkeypatch)

    async def scenario():
        started = asyncio.Event()

        async def handler(request):
            started.set()
            if outcome == "http_error":
                return httpx.Response(503)
            await asyncio.Event().wait()

        provider = AsyncOpenAIProvider(
            "synthetic-key", "synthetic-model", transport=httpx.MockTransport(handler)
        )
        task = asyncio.create_task(provider.select_events(_payload("SYNTHETIC_A"), instructions="Select."))
        await started.wait()
        if outcome == "cancelled":
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            with pytest.raises(ProviderError, match="^provider_unavailable$"):
                await task

    asyncio.run(scenario())
    assert len(clients) == 1
    assert clients[0].is_closed
    assert configurations[0]["verify"] is _tls_context()
    assert configurations[0]["trust_env"] is False


def test_provider_module_import_does_not_load_optional_transport_or_ca_bundle(monkeypatch):
    real_import = builtins.__import__

    def blocked_import(name, *args, **kwargs):
        if name in {"httpx", "certifi"}:
            pytest.fail("disabled-AI import tried to load an optional transport dependency")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocked_import)
    namespace = runpy.run_path(provider_module.__file__, run_name="synthetic_disabled_ai_import")
    assert namespace["_tls_context"].cache_info().currsize == 0
    namespace["AsyncOpenAIProvider"]("synthetic-key", "synthetic-model")
