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
PROVIDER_FILES = ("__init__.py", "plugin.yaml", "catalog.py", "proxy.py")
SANDBOX_DIR = REPO_ROOT / "sandbox"
SANDBOX_PLUGIN = "tinfoil-sandbox"  # ``name`` in sandbox/plugin.yaml
# Settings a developer's shell may carry; no test may inherit them.
TINFOIL_ENV = (
    "TINFOIL_API_KEY", "TINFOIL_BASE_URL", "TINFOIL_PROXY_BIN", "TINFOIL_PROXY_ARGS",
    "TINFOIL_TUNNEL_API_KEY",
    "TINFOIL_ADMIN_KEY", "TINFOIL_CLI", "TINFOIL_CONTAINER", "TINFOIL_CONTAINER_REPO",
    "TINFOIL_CONTAINER_USER", "TINFOIL_CONTAINER_SSH_PORT", "TINFOIL_CONTAINER_IDENTITY_FILE",
    "TINFOIL_CONTAINER_ALLOW_DEBUG",
)

_src = os.environ.get("HERMES_AGENT_SRC", "").strip()
if _src:
    sys.path.insert(0, str(Path(_src).expanduser().resolve()))

_tmp_home: Path | None = None


def install_plugin_copy(hermes_home: Path) -> None:
    """Mirror what ``hermes plugins install`` leaves behind: a flat clone."""
    target = hermes_home / "plugins" / PLUGIN_DIR_NAME
    target.mkdir(parents=True)
    for filename in PROVIDER_FILES:
        shutil.copy2(REPO_ROOT / filename, target / filename)


class _RepoRootIsNotATestPackage:
    """The repository root is the plugin package (``__init__.py`` with relative
    imports), so pytest would import that file on its own, where the relative
    imports cannot resolve. Collect the root as a plain directory instead."""

    @pytest.hookimpl(tryfirst=True)
    def pytest_collect_directory(self, path, parent):
        if Path(path).resolve() == REPO_ROOT:
            return pytest.Dir.from_parent(parent, path=path)
        return None


def pytest_configure(config):
    global _tmp_home
    if not config.pluginmanager.has_plugin("tinfoil-repo-root"):
        config.pluginmanager.register(_RepoRootIsNotATestPackage(), "tinfoil-repo-root")
    home = tempfile.TemporaryDirectory(prefix="tinfoil-plugin-hermes-home-")
    config.add_cleanup(home.cleanup)
    _tmp_home = Path(home.name)
    install_plugin_copy(_tmp_home)
    env = pytest.MonkeyPatch()
    config.add_cleanup(env.undo)
    env.setenv("HERMES_HOME", str(_tmp_home))
    # Never let a developer's real credentials leak into request-shape tests.
    for var in TINFOIL_ENV:
        env.delenv(var, raising=False)
    # Tests that exercise proxy start-up opt back in with a stand-in binary;
    # no other test may launch the developer's real tinfoil-proxy.
    env.setenv("TINFOIL_PROXY_AUTOSTART", "0")
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


@pytest.fixture(scope="session")
def plugin_module(profile):
    """The installed plugin package, for reaching its ``catalog`` and ``proxy`` modules."""
    return sys.modules[type(profile).__module__]


@pytest.fixture
def catalog(plugin_module):
    """The plugin's catalog memory, emptied before and after so tests cannot see each other's models."""
    import importlib

    module = importlib.import_module(f"{plugin_module.__name__}.catalog")

    def forget():
        with module._lock:
            module._rows.clear()
            module._warmed_homes.clear()
        module._snapshot_path().unlink(missing_ok=True)

    forget()
    yield module
    forget()
