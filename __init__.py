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

from providers import register_provider
from providers.base import ProviderProfile


# Hermes effort vocabulary -> values accepted by Tinfoil's full-range models.
# Unsupported per-model values return HTTP 400, so every narrower family is
# translated explicitly below.
_FULL_RANGE = ("none", "minimal", "low", "medium", "high", "xhigh", "max")


class TinfoilProfile(ProviderProfile):
    """OpenAI-compatible Tinfoil traffic routed through tinfoil-proxy."""

    def get_hostname(self) -> str:
        # The default derivation would claim hostname "127.0.0.1" in the
        # URL-to-provider reverse map and misattribute unrelated local servers
        # (Ollama, LM Studio, vLLM) to Tinfoil. Selection is explicit instead.
        return ""

    def build_api_kwargs_extras(
        self,
        *,
        reasoning_config: dict | None = None,
        model: str | None = None,
        **context: Any,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Translate Hermes reasoning settings to Tinfoil's wire contract."""
        if not isinstance(reasoning_config, dict):
            return {}, {}

        model_lower = (model or "").lower()
        effort = str(reasoning_config.get("effort") or "medium").strip().lower()
        disabled = reasoning_config.get("enabled") is False

        if "kimi" in model_lower:
            # Tinfoil's Kimi reasoning models accept a narrower effort range
            # and do not expose a disable value.
            if disabled:
                return {}, {}
            mapped = {"low": "low", "medium": "high", "high": "high"}.get(effort, "max")
            return {}, {"reasoning_effort": mapped}

        if "gpt-oss" in model_lower:
            # gpt-oss accepts only low/medium/high and has no disable value.
            if disabled:
                return {}, {}
            if effort in ("xhigh", "max", "ultra"):
                effort = "high"
            if effort not in ("low", "medium", "high"):
                effort = "medium"
            return {}, {"reasoning_effort": effort}

        if any(family in model_lower for family in ("deepseek", "glm", "gemma")):
            if disabled:
                return {}, {"reasoning_effort": "none"}
            if effort == "ultra":
                effort = "max"
            if effort not in _FULL_RANGE:
                effort = "medium"
            return {}, {"reasoning_effort": effort}

        # Unknown/new models are safer with the server default than a field
        # whose accepted values Hermes cannot yet know.
        return {}, {}


tinfoil = TinfoilProfile(
    name="tinfoil",
    env_vars=("TINFOIL_API_KEY", "TINFOIL_BASE_URL"),
    display_name="Tinfoil",
    description="Tinfoil — confidential inference in verified enclaves (local proxy)",
    signup_url="https://tinfoil.sh/",
    base_url="http://127.0.0.1:3301/v1",
    auth_type="api_key",
    default_aux_model="gpt-oss-120b",
    # Snapshot of the proxy's chat-capable, tool-calling catalog
    # (GET /v1/models, 2026-09-03), most-capable-first. Not a contract: the
    # picker merges this list with the live catalog at selection time.
    fallback_models=(
        "kimi-k3",
        "glm-5-3",
        "deepseek-v4-flash",
        "gemma4-31b",
        "gpt-oss-120b",
        "llama3-3-70b",
    ),
)

register_provider(tinfoil)
