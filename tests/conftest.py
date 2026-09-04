"""Load the plugin through Hermes' real discovery path, never by direct import.

``pytest_configure`` runs before any test module is imported, which matters:
``hermes_cli.models`` extends the picker's provider list at import time from
whatever the registry has discovered, so ``HERMES_HOME`` must already point
at a directory that contains this plugin under ``plugins/tinfoil-provider/``
when the first Hermes module loads.

Set ``HERMES_AGENT_SRC`` to a hermes-agent checkout when it is not already
importable (e.g. when running pytest from an environment that has the
checkout's dependencies but not the checkout itself on ``sys.path``).
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
PLUGIN_DIR_NAME = "tinfoil-provider"  # ``name`` in plugin.yaml
PROVIDER_SLUG = "tinfoil"  # ``ProviderProfile(name=...)``

_src = os.environ.get("HERMES_AGENT_SRC", "").strip()
if _src:
    sys.path.insert(0, str(Path(_src).expanduser().resolve()))

_tmp_home: Path | None = None


def clear_provider_caches() -> None:
    """The recipe from upstream's test_installed_plugin_discovery.py."""
    import providers as _pkg

    _pkg._REGISTRY.clear()
    _pkg._ALIASES.clear()
    _pkg._PROVIDER_LIST_CACHE = None
    _pkg._discovered = False
    for mod in list(sys.modules):
        if mod.startswith(("plugins.model_providers", "_hermes_user_provider")):
            del sys.modules[mod]


def install_plugin_copy(hermes_home: Path) -> Path:
    """Mirror what ``hermes plugins install`` leaves behind: a flat clone."""
    target = hermes_home / "plugins" / PLUGIN_DIR_NAME
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(
        REPO_ROOT,
        target,
        ignore=shutil.ignore_patterns(".git", "__pycache__", ".pytest_cache", ".venv"),
    )
    return target


def pytest_configure(config):
    global _tmp_home
    _tmp_home = Path(tempfile.mkdtemp(prefix="tinfoil-plugin-hermes-home-"))
    install_plugin_copy(_tmp_home)
    os.environ["HERMES_HOME"] = str(_tmp_home)
    # Never let a developer's real credentials leak into request-shape tests.
    for var in ("TINFOIL_API_KEY", "TINFOIL_BASE_URL"):
        os.environ.pop(var, None)
    try:
        import providers  # noqa: F401
    except ImportError as exc:
        raise pytest.UsageError(
            "hermes-agent is not importable. Run pytest from an environment that "
            "has hermes-agent's dependencies and set HERMES_AGENT_SRC to a "
            f"checkout (got {exc})."
        ) from exc
    clear_provider_caches()


def pytest_unconfigure(config):
    if _tmp_home is not None:
        shutil.rmtree(_tmp_home, ignore_errors=True)


@pytest.fixture(scope="session")
def hermes_home() -> Path:
    assert _tmp_home is not None
    return _tmp_home


@pytest.fixture(scope="session")
def profile(hermes_home):
    from providers import get_provider_profile

    discovered = get_provider_profile(PROVIDER_SLUG)
    assert discovered is not None, "plugin was not discovered from $HERMES_HOME/plugins/"
    return discovered
