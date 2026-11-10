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
    return ChatCompletionsTransport().build_kwargs(
        model,
        [{"role": "user", "content": "hi"}],
        provider_profile=profile,
        reasoning_config=reasoning_config,
    )


class TestTinfoilDiscovery:
    def test_discovered_from_flat_install_directory(self, hermes_home, profile):
        installed = hermes_home / "plugins" / PLUGIN_DIR_NAME
        assert (installed / "plugin.yaml").is_file()
        module = sys.modules[type(profile).__module__]
        assert module.__file__ == str(installed / "__init__.py")

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
    def test_manifest_declares_key_and_optional_url(self, hermes_home, profile):
        import hermes_yaml

        path = hermes_home / "plugins" / PLUGIN_DIR_NAME / "plugin.yaml"
        manifest = hermes_yaml.safe_load(path.read_text(encoding="utf-8"))
        (key,) = manifest["requires_env"]
        (url,) = manifest["optional_env"]
        assert (key["name"], url["name"]) == profile.env_vars
        assert key["name"] == "TINFOIL_API_KEY"
        assert key["secret"] is True
        assert url["name"] == "TINFOIL_BASE_URL"
        assert url.get("secret", False) is False

    def test_installer_has_no_runtime_dependencies_to_prepare(self):
        from conftest import REPO_ROOT
        from pm.plugin_declarations import read_python_declaration

        assert not read_python_declaration(REPO_ROOT).installs_packages

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
    @pytest.mark.parametrize("model", ["kimi-k3-1-pro", "kimi-k3", "glm-5-3", "glm-5-3-flash"])
    @pytest.mark.parametrize(
        ("effort", "expected"),
        [("none", "low"), ("minimal", "low"), ("low", "low"),
         ("medium", "high"), ("high", "high"), ("xhigh", "max"),
         ("max", "max"), ("ultra", "max")],
    )
    def test_three_level_efforts_are_clamped(self, profile, model, effort, expected):
        kwargs = _build_request(profile, model, {"effort": effort})
        assert kwargs["reasoning_effort"] == expected
        assert "reasoning_effort" not in kwargs.get("extra_body", {})

    @pytest.mark.parametrize("effort", ["xhigh", "max", "ultra"])
    def test_gpt_oss_high_efforts_are_clamped(self, profile, effort):
        kwargs = _build_request(profile, "gpt-oss-120b", {"effort": effort})
        assert kwargs["reasoning_effort"] == "high"
        assert "reasoning_effort" not in kwargs.get("extra_body", {})

    @pytest.mark.parametrize("model", ["deepseek-v4-flash", "gemma4-31b"])
    def test_full_range_ultra_maps_to_max(self, profile, model):
        kwargs = _build_request(profile, model, {"effort": "ultra"})
        assert kwargs["reasoning_effort"] == "max"

    @pytest.mark.parametrize(
        "effort", ["none", "minimal", "low", "medium", "high", "xhigh", "max"]
    )
    def test_full_range_efforts_pass_through(self, profile, effort):
        kwargs = _build_request(profile, "deepseek-v4-flash", {"effort": effort})
        assert kwargs["reasoning_effort"] == effort

    @pytest.mark.parametrize(
        ("effort", "expected"),
        [("none", "none"), ("minimal", "low"), ("medium", "low"), ("xhigh", "xhigh"), ("ultra", "max")],
    )
    def test_deepseek_v4_1_efforts_use_documented_values(self, profile, effort, expected):
        kwargs = _build_request(profile, "deepseek-v4-1-flash", {"effort": effort})
        assert kwargs["reasoning_effort"] == expected

    @pytest.mark.parametrize("model", ["deepseek-v4-1-flash", "deepseek-v4-flash", "gemma4-31b"])
    def test_full_range_disable_is_explicit_none(self, profile, model):
        kwargs = _build_request(profile, model, {"enabled": False})
        assert kwargs["reasoning_effort"] == "none"

    @pytest.mark.parametrize("model", ["kimi-k3-1-pro", "kimi-k3", "glm-5-3", "glm-5-3-flash", "gpt-oss-120b"])
    def test_always_on_models_use_minimum_when_disabled(self, profile, model):
        kwargs = _build_request(profile, model, {"enabled": False})
        assert kwargs["reasoning_effort"] == "low"
        assert "reasoning_effort" not in kwargs.get("extra_body", {})

    @pytest.mark.parametrize(
        ("model", "reasoning_config"),
        [("deepseek-v4-flash", None), ("deepseek-v4-flash", {}),
         ("kimi-k3", {"effort": "invalid"}),
         ("future-secure-model", {"effort": "high"}),
         ("glm-future", {"effort": "high"}),
         ("llama3-3-70b", {"effort": "high"})],
    )
    def test_unspecified_or_unknown_reasoning_omits_field(
        self, profile, model, reasoning_config
    ):
        kwargs = _build_request(profile, model, reasoning_config)
        assert "reasoning_effort" not in kwargs
        assert "reasoning_effort" not in kwargs.get("extra_body", {})

    def test_request_overrides_win_without_mutating_session_settings(self, profile):
        from copy import deepcopy

        reasoning = {"effort": "ultra"}
        messages = [{"role": "user", "content": "hi"}]
        original = deepcopy((reasoning, messages))
        transport = ChatCompletionsTransport()
        kwargs = transport.build_kwargs(
            "glm-5-3", messages, provider_profile=profile,
            reasoning_config=reasoning, session_id="test-session",
            request_overrides={"reasoning_effort": "low", "max_tokens": 123},
        )
        assert kwargs["reasoning_effort"] == "low"
        assert kwargs["max_tokens"] == 123
        assert "prompt_cache_key" not in kwargs
        assert (reasoning, messages) == original
        # A later request through the same transport uses its own model's range.
        assert transport.build_kwargs(
            "openai/gpt-oss-120b", messages, provider_profile=profile,
            reasoning_config=reasoning,
        )["reasoning_effort"] == "high"


def test_repository_passes_the_installer_security_scan():
    """`hermes plugins install` refuses a 'dangerous' verdict and asks for
    confirmation on 'caution'; the published tree must stay 'safe'."""
    from conftest import REPO_ROOT
    from tools.plugin_guard import scan_plugin

    result = scan_plugin(REPO_ROOT, source="tinfoil-hermes-provider")
    blocking = [f for f in result.findings if f.severity in ("critical", "high")]
    assert result.verdict == "safe", blocking
