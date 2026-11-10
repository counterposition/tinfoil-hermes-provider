"""The provider takes its model list, context windows and reasoning levels from Tinfoil's catalog."""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest
from agent.transports.chat_completions import ChatCompletionsTransport

# Shaped like the rows Tinfoil's /v1/models returns; the ids are invented.
CATALOG = [
    {"id": "nova-9", "type": "chat", "tool_calling": True, "context_window": 400000,
     "multimodal": True, "reasoning": True, "supported_efforts": ["low", "high", "max"]},
    {"id": "nova-9-lite", "type": "chat", "tool_calling": True, "context_window": 64000,
     "multimodal": False, "reasoning": False, "supported_efforts": []},
    {"id": "nova-8", "type": "chat", "tool_calling": True, "deprecated": True},
    {"id": "nova-chat-only", "type": "chat", "tool_calling": False},
    {"id": "nova-embed", "type": "embedding", "tool_calling": False, "context_window": 8192},
    {"id": "nova-voice", "type": "tts", "tool_calling": False},
    {"id": "nova-guard", "type": "safety", "tool_calling": True},
    {"id": "nova-plain"},
]


@pytest.fixture
def served_catalog():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            payload = json.dumps({"object": "list", "data": CATALOG}).encode()
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
        yield f"http://127.0.0.1:{server.server_port}/v1"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def _reasoning_effort(profile, model, effort):
    kwargs = ChatCompletionsTransport().build_kwargs(
        model, [{"role": "user", "content": "hi"}],
        provider_profile=profile, reasoning_config={"effort": effort},
    )
    return kwargs.get("reasoning_effort")


def test_picker_lists_only_models_an_agent_can_drive(profile, catalog, served_catalog):
    models = profile.fetch_models(api_key="catalog-test-key", base_url=served_catalog)
    # Retired, tool-less and non-chat rows are left out; a row with no metadata is kept.
    assert models == ["nova-9", "nova-9-lite", "nova-plain"]


def test_new_model_gets_its_reasoning_levels_from_the_catalog(profile, catalog, served_catalog):
    assert _reasoning_effort(profile, "nova-9", "medium") is None  # unknown until the catalog is read
    profile.fetch_models(base_url=served_catalog)
    assert profile.supported_reasoning_efforts("nova-9") == ("low", "high", "max")
    assert _reasoning_effort(profile, "nova-9", "medium") == "high"
    assert _reasoning_effort(profile, "nova-9", "ultra") == "max"
    assert profile.supported_reasoning_efforts("nova-9-lite") == ()
    assert _reasoning_effort(profile, "nova-9-lite", "high") is None
    # A row that does not describe reasoning leaves the question open.
    assert profile.supported_reasoning_efforts("nova-plain") is None


def test_catalog_overrides_the_shipped_table(profile, catalog):
    model = profile.fallback_models[0]
    shipped = profile.supported_reasoning_efforts(model)
    catalog.remember([{"id": model, "type": "chat", "supported_efforts": ["low", "medium"]}])
    assert profile.supported_reasoning_efforts(model) == ("low", "medium") != shipped


def test_context_window_comes_from_the_catalog(profile, catalog, served_catalog):
    assert profile.get_model_context_length("nova-9") is None
    # The live lookup reads the catalog itself when the model is not yet known.
    assert profile.live_context_length("nova-9", base_url=served_catalog) == 400000
    assert profile.get_model_context_length("tinfoil/Nova-9") == 400000
    assert profile.get_model_context_length("nova-plain") is None


def test_catalog_survives_a_restart(profile, catalog, served_catalog, hermes_home):
    profile.fetch_models(base_url=served_catalog)
    assert (hermes_home / "plugin-data" / "tinfoil-provider" / "catalog.json").is_file()
    with catalog._lock:  # what a new Hermes process starts with
        catalog._rows.clear()
        catalog._warmed_homes.clear()
    assert profile.supported_reasoning_efforts("nova-9") is None
    profile.create_client(base_url=served_catalog)  # client construction loads the saved catalog
    assert profile.supported_reasoning_efforts("nova-9") == ("low", "high", "max")
    capabilities = catalog.capabilities()
    assert capabilities["nova-9"] == {
        "supports_reasoning": True, "supports_vision": True,
        "supports_tools": True, "context_window": 400000,
    }
    assert "nova-embed" not in capabilities


def test_unreachable_catalog_falls_back_to_the_shipped_list(profile, catalog):
    # Nothing listens here and proxy start-up is off in the suite.
    assert profile.fetch_models(base_url="http://127.0.0.1:9/v1") is None


def test_stable_models_declare_capabilities_before_any_catalog(profile):
    declared = getattr(profile, "model_capabilities", None)
    if declared is None:
        pytest.skip("this Hermes has no ProviderProfile.model_capabilities")
    for model in profile.fallback_models:
        assert declared[model]["context_window"] > 0
        assert declared[model]["supports_tools"] is True
    assert declared[profile.default_vision_model()]["supports_vision"] is True


def test_missing_key_message_says_where_to_get_one(profile):
    hint = profile.missing_credentials_hint()
    assert "hermes model" in hint and "tinfoil.sh" in hint
