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
"""

from typing import Any

from agent.reasoning_effort import (
    EFFORT_LADDER,
    OPENAI_COMPAT_WIRE_EFFORTS,
    clamp_effort,
    requested_effort,
)
from providers import register_provider
from providers.base import ProviderProfile


# Tinfoil's wire contracts differ from the models' native providers. Match
# known model IDs, not whole families: new releases can change their vocabulary.
# https://docs.tinfoil.sh/guides/reasoning (verified 2026-09-13)
_THREE_LEVEL_EFFORTS = ("low", "high", "max")
_REASONING_EFFORTS = {
    "kimi-k3": _THREE_LEVEL_EFFORTS,
    "glm-5-3": _THREE_LEVEL_EFFORTS,
    "glm-5-3-flash": _THREE_LEVEL_EFFORTS,
    "gpt-oss-120b": ("low", "medium", "high"),
    "gpt-oss-safeguard-120b": ("low", "medium", "high"),
    "deepseek-v4-flash": OPENAI_COMPAT_WIRE_EFFORTS,
    "gemma4-31b": OPENAI_COMPAT_WIRE_EFFORTS,
    "llama3-3-70b": (),
}


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

    def supported_reasoning_efforts(self, model: str | None) -> tuple[str, ...] | None:
        bare_model = (model or "").strip().lower().rsplit("/", 1)[-1]
        return _REASONING_EFFORTS.get(bare_model)

    def build_api_kwargs_extras(
        self,
        *,
        reasoning_config: dict | None = None,
        model: str | None = None,
        **context: Any,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Translate Hermes reasoning settings to Tinfoil's wire contract."""
        supported = self.supported_reasoning_efforts(model)
        if not supported or not isinstance(reasoning_config, dict):
            return {}, {}

        # Always-on models have no disable value. Send their minimum rather
        # than omit the field: Kimi/GLM default to max when it is absent.
        effort = "none" if reasoning_config.get("enabled") is False else requested_effort(reasoning_config)
        if effort not in EFFORT_LADDER:
            return {}, {}
        # Preserve the three-level low/middle/top mapping used by Hermes' Kimi
        # integration. All other gaps use Hermes' nearest-weaker clamp.
        overrides = {"medium": "high", "xhigh": "max"} if supported == _THREE_LEVEL_EFFORTS else None
        return {}, {"reasoning_effort": clamp_effort(effort, supported, overrides)}


tinfoil = TinfoilProfile(
    name="tinfoil",
    env_vars=("TINFOIL_API_KEY", "TINFOIL_BASE_URL"),
    display_name="Tinfoil",
    description="Tinfoil — confidential inference in verified enclaves (local proxy)",
    signup_url="https://tinfoil.sh/",
    base_url="http://127.0.0.1:3301/v1",
    auth_type="api_key",
    default_aux_model="gpt-oss-120b",
    # Stable, non-deprecated chat models from GET /v1/models (2026-09-13).
    # Experimental and retiring models remain available through live discovery.
    fallback_models=(
        "kimi-k3",
        "glm-5-3",
        "gemma4-31b",
        "gpt-oss-120b",
        "llama3-3-70b",
    ),
)

register_provider(tinfoil)
