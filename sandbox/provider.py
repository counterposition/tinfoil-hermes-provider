"""The ``tinfoil`` terminal backend: settings, health checks and environment creation."""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path
from typing import Any

from agent.terminal_env_provider import TerminalEnvironmentProvider

from .environment import Target, remote_home, tunnel_key

logger = logging.getLogger(__name__)

CLI_URL = "https://docs.tinfoil.sh/containers/cli"
CONTAINERS_URL = "https://docs.tinfoil.sh/containers/quickstart"
_TRUE = {"1", "true", "yes", "on"}


def find_cli() -> str | None:
    """The ``tinfoil`` CLI: ``TINFOIL_CLI``, then ``PATH``, then the usual install dirs."""
    from hermes_cli.config import get_env_value

    override = (get_env_value("TINFOIL_CLI") or "").strip()
    if override:
        path = Path(override).expanduser()
        return str(path) if path.is_file() and os.access(path, os.X_OK) else None
    found = shutil.which("tinfoil")
    if found:
        return found
    home = Path.home()
    for directory in (home / ".local" / "bin", home / "go" / "bin",
                      Path("/usr/local/bin"), Path("/opt/homebrew/bin")):
        candidate = directory / "tinfoil"
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def _setting(terminal: dict, key: str, env_name: str) -> str:
    """``terminal.tinfoil_<key>`` from config.yaml, else ``<env_name>`` (shell or the profile's .env)."""
    value = terminal.get(f"tinfoil_{key}")
    if value is None or not str(value).strip():
        from hermes_cli.config import get_env_value

        value = get_env_value(env_name)
    return str(value or "").strip()


def configured_target() -> Target | None:
    from hermes_cli.config import load_config_readonly

    terminal = load_config_readonly().get("terminal")
    terminal = terminal if isinstance(terminal, dict) else {}
    container = _setting(terminal, "container", "TINFOIL_CONTAINER")
    if not container:
        return None
    port = _setting(terminal, "ssh_port", "TINFOIL_CONTAINER_SSH_PORT")
    return Target(
        container=container,
        repo=_setting(terminal, "repo", "TINFOIL_CONTAINER_REPO"),
        user=_setting(terminal, "user", "TINFOIL_CONTAINER_USER") or "root",
        ssh_port=int(port) if port.isdigit() else 0,
        identity_file=_setting(terminal, "identity_file", "TINFOIL_CONTAINER_IDENTITY_FILE"),
        allow_debug=_setting(terminal, "allow_debug", "TINFOIL_CONTAINER_ALLOW_DEBUG").lower() in _TRUE,
    )


# Tools whose ``path`` argument Hermes resolves on this machine before the backend sees it.
_PATH_TOOLS = frozenset({"read_file", "write_file", "patch", "search_files"})


def container_home_paths(tool_name: str = "", args: dict | None = None, **_: Any) -> dict | None:
    """``pre_tool_call`` hook: point a ``~`` path at the container's home.

    Hermes expands ``~`` in file-tool paths with this machine's home for every
    container-style backend, so ``~/notes.md`` would land in a ``/Users/...``
    directory created inside the container. Rewriting the argument to the home
    the container login actually has keeps ``~`` meaning the same thing for the
    file tools as for the terminal.
    """
    if tool_name not in _PATH_TOOLS or not isinstance(args, dict):
        return None
    path = args.get("path")
    if not isinstance(path, str) or not (path == "~" or path.startswith("~/")):
        return None
    from hermes_cli.config import load_config_readonly

    terminal = load_config_readonly().get("terminal")
    if not isinstance(terminal, dict) or str(terminal.get("backend") or "").strip().lower() != "tinfoil":
        return None
    target = configured_target()
    if target is None:
        return None
    return {"action": "modify", "args": {"path": remote_home(target) + path[1:]}}


def _problems() -> list[str]:
    """Why the backend cannot run commands yet; empty when it can. No network."""
    problems = []
    if find_cli() is None:
        problems.append(f"the tinfoil CLI is not installed ({CLI_URL})")
    if shutil.which("ssh") is None:
        problems.append("the OpenSSH client (ssh) is not installed")
    target = configured_target()
    if target is None:
        problems.append("no container is selected (hermes config set terminal.tinfoil_container <name>)")
    elif "." in target.container and not target.repo:
        problems.append("an enclave hostname needs terminal.tinfoil_repo (the expected owner/repo)")
    return problems


class TinfoilSandboxProvider(TerminalEnvironmentProvider):
    """A Tinfoil Container: your own image in an attested enclave, reached over verified SSH."""

    is_remote = True
    is_container = True

    @property
    def name(self) -> str:
        return "tinfoil"

    @property
    def display_name(self) -> str:
        return "Tinfoil Container"

    @property
    def description(self) -> str:
        return "Confidential container in an attested enclave, reached over verified SSH."

    @property
    def env_description(self) -> str:
        return "a Tinfoil confidential container (Linux)"

    @property
    def skip_container_guards(self) -> bool:
        # The container is the user's long-lived workspace and may hold data
        # they care about, so dangerous-command approval stays on.
        return False

    @property
    def default_cwd(self) -> str:
        return "~"

    @property
    def strip_env_keys(self) -> frozenset:
        return frozenset({"TINFOIL_API_KEY", "TINFOIL_TUNNEL_API_KEY", "TINFOIL_ADMIN_KEY"})

    def is_available(self) -> bool:
        return not _problems()

    def check_requirements(self, config: dict[str, Any]) -> bool:
        problems = _problems()
        for problem in problems:
            logger.error("Tinfoil container backend selected but %s.", problem)
        return not problems

    def probe(self) -> tuple[str, str]:
        problems = _problems()
        return ("needs_setup", problems[0][0].upper() + problems[0][1:] + ".") if problems else ("ready", "")

    def setup_instructions(self) -> list[str]:
        cli = find_cli()
        target = configured_target()
        lines = [f"Found {cli}." if cli else f"Install the tinfoil CLI: {CLI_URL}"]
        lines.append(
            f"Container: {target.container}." if target else
            "Select a container: hermes config set terminal.tinfoil_container <name>")
        lines += [
            f"The container must run an SSH server that accepts your key ({CONTAINERS_URL}).",
            "Sign the CLI in once (`tinfoil login`) so it can look the container up by name.",
        ]
        return lines

    def post_setup(self) -> None:
        """``hermes setup``: ask for the container when none is selected."""
        if configured_target() is not None:
            return
        try:
            from hermes_cli.config import load_config, save_config
            from hermes_cli.setup import prompt

            name = (prompt("Tinfoil container name (or enclave hostname)") or "").strip()
            if not name:
                return
            config = load_config()
            terminal = config.setdefault("terminal", {})
            terminal["tinfoil_container"] = name
            if "." in name:
                repo = (prompt("Expected config repository (owner/repo)") or "").strip()
                if repo:
                    terminal["tinfoil_repo"] = repo
            save_config(config)
        except Exception:
            logger.debug("Tinfoil container setup prompt skipped", exc_info=True)

    def doctor_checks(self) -> list[tuple[bool, str, str]]:
        cli = find_cli()
        target = configured_target()
        return [
            (cli is not None, "Tinfoil CLI", f"({cli})" if cli else f"(not found; install: {CLI_URL})"),
            (target is not None, "Tinfoil container",
             f"({target.container})" if target else "(set terminal.tinfoil_container)"),
            (bool(tunnel_key()), "Tinfoil tunnel key",
             "(TINFOIL_API_KEY)" if tunnel_key() else "(set TINFOIL_API_KEY to an inference key)"),
        ]

    def create_environment(self, *, cwd: str, timeout: int, task_id: str = "default",
                           image: str | None = None, container_config: dict[str, Any] | None = None,
                           **kwargs: Any):
        from .environment import TinfoilContainerEnvironment

        problems = _problems()
        if problems:
            raise RuntimeError("Tinfoil container backend is not ready: " + "; ".join(problems) + ".")
        return TinfoilContainerEnvironment(cli=find_cli(), target=configured_target(), cwd=cwd, timeout=timeout)
