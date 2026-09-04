"""Behavioral tests for the installable Tinfoil model-provider plugin."""

from __future__ import annotations

import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread

import pytest

from agent.transports.chat_completions import ChatCompletionsTransport
from conftest import PLUGIN_DIR_NAME, PROVIDER_SLUG


class _ModelsHandler(BaseHTTPRequestHandler):
    authorization = ""

    def do_GET(self):
        type(self).authorization = self.headers.get("Authorization", "")
        if self.path.rstrip("/") != "/v1/models":
            self.send_response(404)
            self.end_headers()
            return
        body = json.dumps({
            "data": [{"id": "stub-secure-model"}, {"id": "stub-tool-model"}]
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass


def _build_request(profile, model: str, reasoning_config: dict | None):
    transport = object.__new__(ChatCompletionsTransport)
    return transport._build_kwargs_from_profile(
        profile,
        model,
        [{"role": "user", "content": "hi"}],
        None,
        {"reasoning_config": reasoning_config},
    )


class TestTinfoilDiscovery:
    def test_discovered_from_flat_install_directory(self, hermes_home, profile):
        """The contract this plugin depends on: step 2b of provider discovery."""
        installed = hermes_home / "plugins" / PLUGIN_DIR_NAME
        assert (installed / "plugin.yaml").is_file()
        module = sys.modules[f"_hermes_user_provider_{PLUGIN_DIR_NAME.replace('-', '_')}"]
        assert module.__file__ == str(installed / "__init__.py")
        assert type(profile).__module__ == module.__name__

    def test_picker_canonical_providers_include_tinfoil(self, profile):
        from hermes_cli.models import CANONICAL_PROVIDERS

        entry = next((p for p in CANONICAL_PROVIDERS if p.slug == PROVIDER_SLUG), None)
        assert entry is not None
        assert entry.label == profile.display_name
        assert entry.tui_desc == profile.description

    def test_profile_contract(self, profile):
        assert profile.auth_type == "api_key"
        assert profile.base_url.startswith("http://127.0.0.1:")
        assert profile.base_url.endswith("/v1")
        assert profile.supports_health_check is True
        assert profile.fallback_models
        assert all(isinstance(m, str) and m for m in profile.fallback_models)
        assert profile.default_aux_model in profile.fallback_models

    def test_loopback_hostname_is_not_claimed(self, profile):
        assert profile.get_hostname() == ""

        from agent.model_metadata import _URL_TO_PROVIDER

        assert _URL_TO_PROVIDER.get("127.0.0.1") != PROVIDER_SLUG

    def test_live_model_fetch_uses_proxy_path_and_bearer_auth(self, profile):
        server = HTTPServer(("127.0.0.1", 0), _ModelsHandler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base_url = f"http://127.0.0.1:{server.server_address[1]}/v1"
            assert profile.fetch_models(
                api_key="tinfoil-test-key",
                base_url=base_url,
            ) == ["stub-secure-model", "stub-tool-model"]
            assert _ModelsHandler.authorization == "Bearer tinfoil-test-key"
        finally:
            server.shutdown()
            server.server_close()


class TestTinfoilConfigurationWiring:
    def test_env_vars_are_injected_with_correct_visibility(self, profile):
        from hermes_cli.config import OPTIONAL_ENV_VARS, _inject_profile_env_vars

        _inject_profile_env_vars()
        assert OPTIONAL_ENV_VARS["TINFOIL_API_KEY"]["category"] == "provider"
        assert OPTIONAL_ENV_VARS["TINFOIL_API_KEY"]["password"] is True
        assert OPTIONAL_ENV_VARS["TINFOIL_BASE_URL"]["category"] == "provider"
        assert OPTIONAL_ENV_VARS["TINFOIL_BASE_URL"]["password"] is False

    def test_registry_derives_base_url_env_var(self, profile):
        from hermes_cli.auth import PROVIDER_REGISTRY

        config = PROVIDER_REGISTRY[PROVIDER_SLUG]
        assert config.inference_base_url == profile.base_url
        assert config.api_key_env_vars == ("TINFOIL_API_KEY",)
        assert config.base_url_env_var == "TINFOIL_BASE_URL"

    def test_runtime_resolution_honors_base_url_env(self, profile, monkeypatch, tmp_path):
        from hermes_cli.runtime_provider import resolve_runtime_provider

        hermes_home = tmp_path / "hermes-home"
        hermes_home.mkdir()
        monkeypatch.setenv("HERMES_HOME", str(hermes_home))
        monkeypatch.setenv("TINFOIL_BASE_URL", "http://127.0.0.1:43999/v1/")

        runtime = resolve_runtime_provider(
            requested=PROVIDER_SLUG,
            explicit_api_key="tinfoil-test-key",
            target_model=profile.fallback_models[0],
        )

        assert runtime["provider"] == PROVIDER_SLUG
        assert runtime["api_mode"] == "chat_completions"
        assert runtime["base_url"] == "http://127.0.0.1:43999/v1"

    def test_runtime_resolution_defaults_to_local_proxy(self, profile, monkeypatch):
        from hermes_cli.runtime_provider import resolve_runtime_provider

        monkeypatch.delenv("TINFOIL_BASE_URL", raising=False)
        runtime = resolve_runtime_provider(
            requested=PROVIDER_SLUG,
            explicit_api_key="tinfoil-test-key",
            target_model=profile.fallback_models[0],
        )
        assert runtime["base_url"] == profile.base_url


class TestTinfoilReasoningRequestShape:
    @pytest.mark.parametrize(
        ("effort", "expected"),
        [("medium", "high"), ("xhigh", "max"), ("max", "max"), ("ultra", "max")],
    )
    def test_kimi_efforts_are_clamped(self, profile, effort, expected):
        kwargs = _build_request(profile, "kimi-k3", {"effort": effort})
        assert kwargs["reasoning_effort"] == expected
        assert "reasoning_effort" not in kwargs.get("extra_body", {})

    @pytest.mark.parametrize("effort", ["xhigh", "max", "ultra"])
    def test_gpt_oss_high_efforts_are_clamped(self, profile, effort):
        kwargs = _build_request(profile, "gpt-oss-120b", {"effort": effort})
        assert kwargs["reasoning_effort"] == "high"
        assert "reasoning_effort" not in kwargs.get("extra_body", {})

    @pytest.mark.parametrize("model", ["deepseek-v4-flash", "glm-5-3", "gemma4-31b"])
    def test_full_range_ultra_maps_to_max(self, profile, model):
        kwargs = _build_request(profile, model, {"effort": "ultra"})
        assert kwargs["reasoning_effort"] == "max"

    @pytest.mark.parametrize(
        "effort", ["none", "minimal", "low", "medium", "high", "xhigh", "max"]
    )
    def test_full_range_efforts_pass_through(self, profile, effort):
        kwargs = _build_request(profile, "deepseek-v4-flash", {"effort": effort})
        assert kwargs["reasoning_effort"] == effort

    @pytest.mark.parametrize("model", ["deepseek-v4-flash", "glm-5-3", "gemma4-31b"])
    def test_full_range_disable_is_explicit_none(self, profile, model):
        kwargs = _build_request(profile, model, {"enabled": False})
        assert kwargs["reasoning_effort"] == "none"

    @pytest.mark.parametrize("model", ["kimi-k3", "gpt-oss-120b"])
    def test_narrow_family_disable_omits_unsupported_value(self, profile, model):
        kwargs = _build_request(profile, model, {"enabled": False})
        assert "reasoning_effort" not in kwargs
        assert "reasoning_effort" not in kwargs.get("extra_body", {})

    @pytest.mark.parametrize(
        ("model", "reasoning_config"),
        [("deepseek-v4-flash", None), ("future-secure-model", {"effort": "high"})],
    )
    def test_unspecified_or_unknown_reasoning_omits_field(
        self, profile, model, reasoning_config
    ):
        kwargs = _build_request(profile, model, reasoning_config)
        assert "reasoning_effort" not in kwargs
        assert "reasoning_effort" not in kwargs.get("extra_body", {})
