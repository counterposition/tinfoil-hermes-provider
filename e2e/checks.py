"""Programmatic assertions for the E2E steps that do not need a TTY.

Each subcommand runs in a fresh process, so it sees exactly what a new
``hermes`` invocation would see after the previous step.
"""

from __future__ import annotations

import sys

SLUG = "tinfoil"


def canonical() -> int:
    from hermes_cli.models import CANONICAL_PROVIDERS

    entries = [p for p in CANONICAL_PROVIDERS if p.slug == SLUG]
    print("picker entry:", entries)
    return 0 if entries else 1


def models() -> int:
    import os

    from providers import get_provider_profile

    os.environ.setdefault("TINFOIL_API_KEY", "e2e-placeholder-key")
    from hermes_cli.models import provider_model_ids

    profile = get_provider_profile(SLUG)
    ids = provider_model_ids(SLUG, force_refresh=True)
    print("picker model list:", ids)
    curated = list(profile.fallback_models)
    if ids[: len(curated)] != curated:
        print("curated fallbacks do not lead the list")
        return 1
    live = profile.fetch_models(api_key="e2e-placeholder-key", base_url=profile.base_url)
    missing = [m for m in live if m not in ids]
    if missing:
        print("live models missing from the merged list:", missing)
        return 1
    print(f"curated first ({len(curated)}), then {len(ids) - len(curated)} live-only entries")
    return 0


def default_model() -> int:
    from providers import get_provider_profile

    print(get_provider_profile(SLUG).fallback_models[0])
    return 0


def selected_model(expected: str) -> int:
    from hermes_cli.config import load_config

    model = load_config().get("model")
    print("config.yaml model section:", model)
    if isinstance(model, dict) and model.get("provider") == SLUG and model.get("default") == expected:
        return 0
    return 1


def description(expected: str) -> int:
    from hermes_cli.models import CANONICAL_PROVIDERS
    from providers import get_provider_profile

    profile = get_provider_profile(SLUG)
    entry = next((p for p in CANONICAL_PROVIDERS if p.slug == SLUG), None)
    print("profile description:", profile.description if profile else None)
    print("picker description :", entry.tui_desc if entry else None)
    ok = profile is not None and profile.description == expected and entry is not None and entry.tui_desc == expected
    return 0 if ok else 1


def absent() -> int:
    from hermes_cli.models import CANONICAL_PROVIDERS
    from providers import get_provider_profile

    profile = get_provider_profile(SLUG)
    entries = [p for p in CANONICAL_PROVIDERS if p.slug == SLUG]
    print("profile:", profile, "picker entries:", entries)
    return 0 if profile is None and not entries else 1


if __name__ == "__main__":
    cmd, *rest = sys.argv[1:]
    sys.exit(globals()[cmd](*rest))
