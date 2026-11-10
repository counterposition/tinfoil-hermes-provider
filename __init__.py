"""Tinfoil confidential-inference provider profile for Hermes Agent.

Installed as a standalone plugin with ``hermes plugins install
counterposition/tinfoil-hermes-provider``; it is not bundled with
hermes-agent. Provider discovery imports it from
``$HERMES_HOME/plugins/tinfoil-provider/`` because ``plugin.yaml`` declares
``kind: model-provider``.

All traffic goes through the local tinfoil-proxy (default 127.0.0.1:3301),
which verifies the enclave attestation and forwards OpenAI-compatible
requests. The API key passes through per-request as Bearer auth. The proxy
is the component that makes Tinfoil confidential; do not point ``base_url``
at Tinfoil's unverified direct endpoint by default.

The plugin starts an installed proxy when none is running (``proxy.py``) and
takes the model list, context windows and reasoning levels from Tinfoil's own
catalog (``catalog.py``), so neither needs attention from the user.
"""

import dataclasses
import json
import logging
import urllib.error
import urllib.request
from typing import Any

from agent.reasoning_effort import (
    EFFORT_LADDER,
    OPENAI_COMPAT_WIRE_EFFORTS,
    clamp_effort,
    requested_effort,
)
from providers import register_provider
from providers.base import ProviderProfile

from . import catalog, proxy

logger = logging.getLogger(__name__)

# Tinfoil's wire contracts differ from the models' native providers. Match
# known model IDs, not whole families: new releases can change their vocabulary.
# The live catalog answers first; this table covers a first run with no catalog.
# https://docs.tinfoil.sh/guides/reasoning
_THREE_LEVEL_EFFORTS = ("low", "high", "max")
_THREE_LEVEL_OVERRIDES = {"medium": "high", "xhigh": "max"}
_HARMONY_EFFORTS = ("low", "medium", "high")
_REASONING_EFFORTS = {
    "kimi-k3-1-pro": _THREE_LEVEL_EFFORTS,
    "kimi-k3": _THREE_LEVEL_EFFORTS,
    "glm-5-3": _THREE_LEVEL_EFFORTS,
    "glm-5-3-flash": _THREE_LEVEL_EFFORTS,
    "gpt-oss-120b": _HARMONY_EFFORTS,
    "gpt-oss-safeguard-120b": _HARMONY_EFFORTS,
    "deepseek-v4-1-flash": ("none", "low", "high", "xhigh", "max"),
    "deepseek-v4-flash": OPENAI_COMPAT_WIRE_EFFORTS,
    "gemma4-31b": OPENAI_COMPAT_WIRE_EFFORTS,
    "llama3-3-70b": (),
}

# What Hermes should assume about the stable models before it has seen the
# catalog. models.dev does not list Tinfoil, so without this the picker shows
# no context window and images are never sent to the models that accept them.
_STABLE_MODELS = {
    "kimi-k3-1-pro": {"context_window": 262144, "supports_vision": True, "supports_reasoning": True},
    "glm-5-3": {"context_window": 1048576, "supports_vision": False, "supports_reasoning": True},
    "gemma4-31b": {"context_window": 262144, "supports_vision": True, "supports_reasoning": True},
    "gpt-oss-120b": {"context_window": 131072, "supports_vision": False, "supports_reasoning": True},
    "llama3-3-70b": {"context_window": 131072, "supports_vision": False, "supports_reasoning": False},
}


class _KeepAuthorization(urllib.request.HTTPRedirectHandler):
    """The catalog request carries the API key, so it follows no redirect."""

    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


class TinfoilProfile(ProviderProfile):
    """OpenAI-compatible Tinfoil traffic routed through tinfoil-proxy."""

    def get_hostname(self) -> str:
        # The default derivation would claim hostname "127.0.0.1" in the
        # URL-to-provider reverse map and misattribute unrelated local servers
        # (Ollama, LM Studio, vLLM) to Tinfoil. Selection is explicit instead.
        return ""

    def default_vision_model(self) -> str:
        # Keep automatic image analysis on the verified proxy even when the
        # main/auxiliary chat model is text-only. Explicit Hermes routes win.
        return "gemma4-31b"

    def missing_credentials_hint(self) -> str:
        return ("Create a Tinfoil API key at https://dash.tinfoil.sh, then run `hermes model` "
                "and choose Tinfoil.")

    # ── Proxy ────────────────────────────────────────────────────────────

    def create_client(self, **client_kwargs: Any) -> None:
        """Bring the verifier proxy up before Hermes builds a client for it.

        Always returns None: Hermes then uses its standard client against the
        loopback URL, so every request still passes through the proxy.
        """
        try:
            catalog.warm()
            proxy.ensure_running(str(client_kwargs.get("base_url") or self.base_url))
        except Exception:
            logger.debug("Tinfoil proxy check failed", exc_info=True)
        return None

    # ── Catalog ──────────────────────────────────────────────────────────

    def _read_catalog(self, base_url: str, api_key: str | None, timeout: float) -> list | None:
        request = urllib.request.Request(base_url.rstrip("/") + "/models")
        request.add_header("Accept", "application/json")
        if api_key:
            request.add_header("Authorization", f"Bearer {api_key}")
        opener = urllib.request.build_opener(_KeepAuthorization)
        with opener.open(request, timeout=timeout) as response:
            data = json.loads(response.read().decode())
        items = data if isinstance(data, list) else data.get("data")
        return items if isinstance(items, list) else None

    def _catalog_items(self, base_url: str | None, api_key: str | None, timeout: float) -> list | None:
        url = (base_url or "").strip() or self.base_url
        for attempt in range(2):
            try:
                return self._read_catalog(url, api_key, timeout)
            except (OSError, urllib.error.URLError, ValueError) as exc:
                logger.debug("fetch_models(%s): %s", self.name, exc)
                # Nothing answered on this machine: start the proxy once, then ask again.
                endpoint = proxy.loopback_endpoint(url)
                if attempt or endpoint is None or proxy.is_listening(*endpoint):
                    return None
                if not proxy.ensure_running(url):
                    return None
        return None

    def fetch_models(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float = 8.0,
    ) -> list[str] | None:
        """Chat models an agent can use, from the proxy's catalog.

        The catalog also lists embeddings, speech, safety and retired models;
        those stay out of the picker. They remain usable by typing the id.
        """
        if not self.supports_model_listing:
            return None
        items = self._catalog_items(base_url, api_key, timeout)
        if items is None:
            return None
        catalog.remember(items)
        return catalog.agent_model_ids(items)

    def get_model_context_length(self, model: str) -> int | None:
        return catalog.context_window(model)

    def live_context_length(self, model: str, *, api_key: str = "", base_url: str = "") -> int | None:
        if catalog.row(model) is None:
            self.fetch_models(api_key=api_key or None, base_url=base_url or None, timeout=5.0)
        return catalog.context_window(model)

    # ── Reasoning ────────────────────────────────────────────────────────

    def supported_reasoning_efforts(self, model: str | None) -> tuple[str, ...] | None:
        listed = catalog.efforts(model)
        if listed is not None:
            return tuple(level for level in listed if level in EFFORT_LADDER)
        return _REASONING_EFFORTS.get(catalog.bare(model))

    def build_api_kwargs_extras(
        self,
        *,
        reasoning_config: dict[str, Any] | None = None,
        model: str | None = None,
        **_context: Any,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Translate Hermes reasoning settings to Tinfoil's wire contract."""
        supported = self.supported_reasoning_efforts(model)
        if not supported or not isinstance(reasoning_config, dict):
            return {}, {}

        # Always-on models have no disable value. Send their minimum rather
        # than omit the field: Kimi/GLM default to max when it is absent.
        effort = (
            "none"
            if reasoning_config.get("enabled") is False
            else requested_effort(reasoning_config)
        )
        if effort not in EFFORT_LADDER:
            return {}, {}
        # Preserve the three-level low/middle/top mapping used by Hermes' Kimi
        # integration. All other gaps use Hermes' nearest-weaker clamp.
        overrides = (
            _THREE_LEVEL_OVERRIDES if supported == _THREE_LEVEL_EFFORTS else None
        )
        return {}, {"reasoning_effort": clamp_effort(effort, supported, overrides)}


def _model_capabilities() -> dict[str, dict[str, Any]]:
    """Stable models as shipped, corrected by the catalog this machine last saw."""
    declared = {model: {**facts, "supports_tools": True} for model, facts in _STABLE_MODELS.items()}
    try:
        catalog.warm()
        for model, facts in catalog.capabilities().items():
            declared[model] = {**declared.get(model, {}), **facts}
    except Exception:
        logger.debug("Tinfoil catalog snapshot unavailable", exc_info=True)
    return declared


# Fields newer Hermes releases added. An older core ignores what it does not
# define instead of failing to load the provider.
_OPTIONAL_FIELDS = {"model_capabilities": _model_capabilities()}
_known_fields = {field.name for field in dataclasses.fields(ProviderProfile)}

tinfoil = TinfoilProfile(
    name="tinfoil",
    env_vars=("TINFOIL_API_KEY", "TINFOIL_BASE_URL"),
    display_name="Tinfoil",
    description="Tinfoil — confidential inference in verified enclaves (local proxy)",
    signup_url="https://dash.tinfoil.sh/",
    base_url="http://127.0.0.1:3301/v1",
    auth_type="api_key",
    default_aux_model="gpt-oss-120b",
    # Stable models in Tinfoil's chat catalog, shown when the proxy is offline.
    # Experimental models appear through live discovery.
    fallback_models=(
        "kimi-k3-1-pro",
        "glm-5-3",
        "gemma4-31b",
        "gpt-oss-120b",
        "llama3-3-70b",
    ),
    **{name: value for name, value in _OPTIONAL_FIELDS.items() if name in _known_fields},
)

# Recent Hermes versions also ask the profile which effort levels setup should offer.
if hasattr(tinfoil, "setup_reasoning_efforts"):
    tinfoil.setup_reasoning_efforts = tinfoil.supported_reasoning_efforts

register_provider(tinfoil)
