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


def install_plugin_copy(hermes_home: Path) -> None:
    """Mirror what ``hermes plugins install`` leaves behind: a flat clone."""
    target = hermes_home / "plugins" / PLUGIN_DIR_NAME
    target.mkdir(parents=True)
    for filename in ("__init__.py", "plugin.yaml"):
        shutil.copy2(REPO_ROOT / filename, target / filename)


def pytest_configure(config):
    global _tmp_home
    home = tempfile.TemporaryDirectory(prefix="tinfoil-plugin-hermes-home-")
    config.add_cleanup(home.cleanup)
    _tmp_home = Path(home.name)
    install_plugin_copy(_tmp_home)
    env = pytest.MonkeyPatch()
    config.add_cleanup(env.undo)
    env.setenv("HERMES_HOME", str(_tmp_home))
    # Never let a developer's real credentials leak into request-shape tests.
    for var in ("TINFOIL_API_KEY", "TINFOIL_BASE_URL"):
        env.delenv(var, raising=False)
    try:
        import providers  # noqa: F401
    except ImportError as exc:
        raise pytest.UsageError(
            "hermes-agent is not importable. Run pytest from an environment that "
            "has hermes-agent's dependencies and set HERMES_AGENT_SRC to a "
            f"checkout (got {exc})."
        ) from exc


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
