"""Exercise Hermes' routing and HTTP boundaries without a Tinfoil account."""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest


@pytest.fixture
def proxy():
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append((self.path, self.headers.get("Authorization"), None))
            self.respond({"data": [{"id": "new-chat-model"}]})

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append((self.path, self.headers.get("Authorization"), body))
            self.respond({
                "id": "test-completion", "object": "chat.completion", "created": 0,
                "model": body["model"],
                "choices": [{"index": 0, "finish_reason": "stop", "message": {
                    "role": "assistant", "content": "A blue square.",
                    "reasoning": "Image inspected.",
                }}],
            })

        def respond(self, body):
            payload = json.dumps(body).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1", requests, server
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_catalog_and_doctor_use_the_configured_proxy(profile, monkeypatch, proxy):
    from hermes_cli.doctor_connectivity import build_probes
    from hermes_cli.models import provider_model_ids

    base_url, requests, server = proxy
    monkeypatch.setenv("TINFOIL_API_KEY", "catalog-test-key")
    monkeypatch.setenv("TINFOIL_BASE_URL", base_url)
    models = provider_model_ids(profile.name, force_refresh=True)
    assert models[:len(profile.fallback_models)] == list(profile.fallback_models)
    assert "new-chat-model" in models

    probe = dict(build_probes())[profile.display_name]
    assert "✓" in probe().lines[0][0]
    assert requests == [("/v1/models", "Bearer catalog-test-key", None)] * 2
    server.shutdown()
    server.server_close()
    assert "⚠" in probe().lines[0][0]


def test_automatic_vision_uses_tinfoil_and_honors_explicit_model(profile, monkeypatch, proxy):
    from agent.auxiliary_client import resolve_vision_provider_client
    from agent.transports.chat_completions import ChatCompletionsTransport

    base_url, requests, _ = proxy
    monkeypatch.setenv("TINFOIL_API_KEY", "vision-test-key")
    monkeypatch.setenv("TINFOIL_BASE_URL", base_url)
    runtime = {
        "provider": profile.name, "model": "gpt-oss-120b",
        "base_url": base_url, "api_key": "vision-test-key",
        "api_mode": "chat_completions",
    }
    provider, client, model = resolve_vision_provider_client(
        provider="auto", main_runtime=runtime,
    )
    assert provider == profile.name
    assert model == profile.default_vision_model()
    assert str(client.base_url).rstrip("/") == base_url
    try:
        response = client.chat.completions.create(
            model=model, messages=[{"role": "user", "content": "Describe the image."}],
        )
        normalized = ChatCompletionsTransport().normalize_response(response)
        assert normalized.content == "A blue square."
        assert normalized.reasoning == "Image inspected."
    finally:
        client.close()
    assert requests[-1][0:2] == ("/v1/chat/completions", "Bearer vision-test-key")
    assert requests[-1][2]["model"] == model

    provider, client, model = resolve_vision_provider_client(
        provider=profile.name, model="kimi-k3", main_runtime=runtime,
    )
    try:
        assert provider == profile.name
        assert model == "kimi-k3"
        assert str(client.base_url).rstrip("/") == base_url
    finally:
        client.close()
