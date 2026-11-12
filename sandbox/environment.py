"""Run Hermes' terminal, file and code tools inside a Tinfoil Container.

Every command goes through ``tinfoil ssh``, which opens SSH over the enclave's
attested TLS connection: verifying the enclave and connecting to it are the
same step, so there is no host key to trust and nothing to install into
``~/.ssh``. An SSH control socket keeps that verified connection open between
commands, the way Hermes' own SSH backend does.
"""

from __future__ import annotations

import contextlib
import hashlib
import logging
import os
import re
import shlex
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from tools.environments.base import BaseEnvironment, EnvironmentConnectionError

# Hermes' shared spawn helper. It writes stdin on its own thread and reports
# write errors to the base class, which file writes rely on. The official
# Singularity backend plugin imports it the same way.
from tools.environments.base_output import _popen_bash

logger = logging.getLogger(__name__)

# Windows OpenSSH has no Unix-socket connection sharing; each command reconnects there.
_MULTIPLEX = os.name != "nt"
# The first connection fetches and checks the enclave's attestation.
_CONNECT_TIMEOUT = 120
_READY = "hermes-tinfoil-ready"
# When the container record has no SSH port, the CLI logs which port it falls back to,
# on stderr, before every command: e.g. `level=info msg="No SSH port known for box, trying 22"`.
_PORT_FALLBACK = re.compile(r"No SSH port known for \S+, trying (\d+)")

# The home directory each container's login resolved to, keyed by (container, user).
_homes: dict[tuple[str, str], str] = {}


def remote_home(target: Target) -> str:
    """The login user's home inside the container: as detected on connect, else the usual one."""
    return _homes.get((target.container, target.user)) or (
        "/root" if target.user == "root" else f"/home/{target.user}")


@dataclass(frozen=True)
class Target:
    """Which container to reach and how."""

    container: str  # container name, or an enclave hostname
    repo: str = ""  # expected config, ``owner/name[@tag]``; required for a hostname
    user: str = "root"
    ssh_port: int = 0  # 0: the port the container publishes
    identity_file: str = ""
    allow_debug: bool = False


def tunnel_key() -> str:
    """The inference key the enclave's tunnel checks. An admin key is not one."""
    from agent.secret_scope import get_secret

    for name in ("TINFOIL_TUNNEL_API_KEY", "TINFOIL_API_KEY"):
        value = (get_secret(name) or "").strip()
        if value and not value.startswith("admin_"):
            return value
    return ""


def admin_key() -> str:
    """The admin key the CLI uses for the control plane, resolved the way the CLI does:
    ``TINFOIL_ADMIN_KEY``, else an ``admin_`` key in ``TINFOIL_API_KEY``."""
    from agent.secret_scope import get_secret

    admin = (get_secret("TINFOIL_ADMIN_KEY") or "").strip()
    if admin:
        return admin
    api_key = (get_secret("TINFOIL_API_KEY") or "").strip()
    return api_key if api_key.startswith("admin_") else ""


# What the ``tinfoil`` CLI and OpenSSH need from Hermes' environment. Hermes' process
# environment can hold every provider's API key, so nothing else is passed on.
_INHERITED = frozenset({
    "PATH", "HOME", "USER", "LOGNAME", "TMPDIR",
    # Locale, by name: a prefix match would also pass e.g. LC_SERVICE_TOKEN.
    "LANG", "LANGUAGE", "LC_ALL", "LC_CTYPE", "LC_COLLATE", "LC_MESSAGES",
    "LC_MONETARY", "LC_NUMERIC", "LC_TIME",
    "SSH_AUTH_SOCK", "SSL_CERT_FILE", "SSL_CERT_DIR",
    "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "no_proxy",
    # The CLI's own settings that are not credentials.
    "TINFOIL_CONTROLPLANE_URL", "TINFOIL_CONFIG",
})
# Windows: sockets need SYSTEMROOT, and Go and OpenSSH find the home directory
# (with the CLI's saved login and ~/.ssh) through USERPROFILE.
_INHERITED_WINDOWS = frozenset({
    "SYSTEMROOT", "WINDIR", "USERPROFILE", "USERNAME", "PROGRAMDATA", "TEMP", "TMP", "PATHEXT",
})


def inherited_env(environ=os.environ, *, windows: bool = os.name == "nt") -> dict[str, str]:
    """The allowlisted part of ``environ``, with no credentials in it."""
    if windows:  # variable names are case-insensitive there
        allowed = {name.upper() for name in _INHERITED | _INHERITED_WINDOWS}
        return {name: value for name, value in environ.items() if name.upper() in allowed}
    return {name: value for name, value in environ.items() if name in _INHERITED}


def client_env() -> dict[str, str]:
    """Environment for the local ``tinfoil`` CLI: the allowlist plus the Tinfoil keys it needs.

    The keys are for the CLI and the enclave's tunnel; the remote shell gets none of this
    unless the user's own SSH config forwards variables (``SendEnv``).
    """
    env = inherited_env()
    env["TINFOIL_NO_UPDATE_CHECK"] = "1"
    key = tunnel_key()
    if key:
        env["TINFOIL_TUNNEL_API_KEY"] = key
    admin = admin_key()
    if admin:
        env["TINFOIL_ADMIN_KEY"] = admin
    return env


def _last_lines(text: str, count: int = 6) -> str:
    lines = [line for line in (text or "").strip().splitlines() if line.strip()]
    return "\n".join(lines[-count:])


class TinfoilContainerEnvironment(BaseEnvironment):
    """Spawn-per-call: each ``execute()`` runs ``tinfoil ssh <container> -- bash -c ...``."""

    _passwordless_sudo_probe_supported = True
    # cleanup() only closes the shared connection. The container keeps running, and the
    # next command reconnects, so a background job's poller still gets its real result.
    cleanup_stops_processes = False

    def __init__(self, cli: str, target: Target, cwd: str = "~", timeout: int = 60):
        super().__init__(cwd=cwd, timeout=timeout)
        self.cli, self.target = cli, target
        self.ssh_port = target.ssh_port  # pinned after the first connection when unset
        if not shutil.which("ssh"):
            raise RuntimeError("The OpenSSH client (ssh) is not installed or not in PATH.")
        control_dir = Path(tempfile.gettempdir()) / "hermes-tinfoil"
        control_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        # Short and stable: the path must fit macOS's 104-byte socket limit, and a stable
        # name lets a new environment reuse a connection that is still open.
        identity = "|".join(map(str, (target.container, target.repo, target.user, target.ssh_port)))
        self.control_socket = control_dir / f"{hashlib.sha256(identity.encode()).hexdigest()[:16]}.sock"
        self._connect()
        home = self._remote_home()
        _homes[(target.container, target.user)] = home
        if self.cwd == "~":
            self.cwd = home
        self.init_session()

    # ── Command construction ─────────────────────────────────────────────

    def _ssh_options(self) -> list[str]:
        # LogLevel=ERROR: ssh's own notices would otherwise land in command output,
        # which Hermes' file tools parse.
        options = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=30", "-o", "LogLevel=ERROR"]
        if _MULTIPLEX:
            options += ["-o", f"ControlPath={self.control_socket}",
                        "-o", "ControlMaster=auto", "-o", "ControlPersist=300"]
        if self.target.identity_file:
            options += ["-i", os.path.expanduser(self.target.identity_file), "-o", "IdentitiesOnly=yes"]
        return options

    def _command(self, remote: list[str]) -> list[str]:
        target = self.target
        argv = [self.cli, "ssh", target.container, "--user", target.user]
        if self.ssh_port:
            argv += ["--port", str(self.ssh_port)]
        if target.repo:
            argv += ["--repo", target.repo]
        if target.allow_debug:
            argv.append("--allow-debug")
        # After ``--`` the CLI hands ssh its options first, then the remote command.
        return [*argv, "--", *self._ssh_options(), *remote]

    def _capture(self, remote: str, timeout: float) -> subprocess.CompletedProcess:
        return subprocess.run(
            self._command([remote]), capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=timeout, check=False, stdin=subprocess.DEVNULL, env=client_env())

    # ── Connection ───────────────────────────────────────────────────────

    def _connect(self) -> None:
        name = self.target.container
        hint = (
            f"Check that the container is running (`tinfoil container get {name}`), that this "
            "machine's SSH key is authorized in it, and that TINFOIL_API_KEY is set. If the CLI "
            "asked to confirm an unknown config, set terminal.tinfoil_repo to the expected "
            "owner/repo. Then retry: the connection is re-established automatically.")
        try:
            result = self._capture(f"echo {_READY}", timeout=_CONNECT_TIMEOUT)
        except subprocess.TimeoutExpired:
            raise EnvironmentConnectionError(
                f"Connecting to Tinfoil container {name} timed out", retry_hint=hint) from None
        if result.returncode != 0 or _READY not in result.stdout:
            detail = _last_lines(result.stderr) or _last_lines(result.stdout) or f"exit {result.returncode}"
            raise EnvironmentConnectionError(
                f"Could not open a verified connection to Tinfoil container {name}: {detail}",
                retry_hint=hint)
        # Pass the port the CLI fell back to from now on, so its log line stops
        # appearing in front of every command's output.
        fallback = _PORT_FALLBACK.search(result.stderr or "")
        if not self.ssh_port and fallback:
            self.ssh_port = int(fallback.group(1))

    def _remote_home(self) -> str:
        with contextlib.suppress(Exception):
            result = self._capture("echo $HOME", timeout=30)
            home = result.stdout.strip().splitlines()[-1] if result.stdout.strip() else ""
            if result.returncode == 0 and home.startswith("/"):
                return home
        return remote_home(self.target)

    # ── BaseEnvironment contract ─────────────────────────────────────────

    def _run_bash(self, cmd_string: str, *, login: bool = False, timeout: int = 120,
                  stdin_data: str | None = None) -> subprocess.Popen:
        # ssh joins the remote words with spaces for the remote shell, so the script is quoted once.
        bash = ["bash", "-l", "-c"] if login else ["bash", "-c"]
        return _popen_bash(self._command([*bash, shlex.quote(cmd_string)]), stdin_data, env=client_env())

    def cleanup(self):
        if not _MULTIPLEX or not self.control_socket.exists():
            return
        with contextlib.suppress(OSError, subprocess.SubprocessError):
            subprocess.run(
                ["ssh", "-o", f"ControlPath={self.control_socket}", "-O", "exit", "tinfoil-container"],
                capture_output=True, timeout=5, stdin=subprocess.DEVNULL, check=False,
                env=inherited_env())
        with contextlib.suppress(OSError):
            self.control_socket.unlink()
