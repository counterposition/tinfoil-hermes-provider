"""Start the local Tinfoil verifier proxy when Hermes needs it and it is not running.

The proxy is still the only route to Tinfoil: it checks the enclave's
attestation and pins the attested TLS key before forwarding anything. This
module only removes the chore of keeping it running. It starts a
``tinfoil-proxy`` binary that is already installed; it never downloads one,
and it never falls back to a direct connection. If the proxy cannot be
started, requests fail with a connection error instead of leaving unverified.
"""

from __future__ import annotations

import logging
import os
import shlex
import shutil
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

INSTALL_URL = "https://docs.tinfoil.sh/local-proxy/cli"
_BINARY = "tinfoil-proxy"
_LOOPBACK = {"127.0.0.1", "localhost", "::1"}
_OFF = {"0", "false", "no", "off"}
# A proxy outlives the Hermes process that started it, so it gets no agent secrets.
_SECRET_SUFFIXES = ("_API_KEY", "_TOKEN", "_SECRET", "_PASSWORD", "_ADMIN_KEY")
_RETRY_AFTER_FAILURE = 30.0

_lock = threading.Lock()
_last_failure: dict[tuple[str, int], float] = {}
_warned: set[str] = set()


def _warn_once(key: str, message: str, *args: object) -> None:
    if key not in _warned:
        _warned.add(key)
        logger.warning(message, *args)


def loopback_endpoint(base_url: str) -> tuple[str, int] | None:
    """``(host, port)`` when *base_url* names a proxy on this machine, else None."""
    try:
        parts = urlsplit(base_url or "")
        host, port = (parts.hostname or "").lower(), parts.port
    except ValueError:
        return None
    if parts.scheme != "http" or host not in _LOOPBACK or not port:
        return None
    return host, port


def is_listening(host: str, port: int, timeout: float = 0.25) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def is_verified(host: str, port: int, timeout: float = 2.0) -> bool:
    """Whether the proxy has finished attesting. A bound port alone does not show that."""
    display_host = f"[{host}]" if ":" in host else host
    url = f"http://{display_host}:{port}/verification-document"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return response.status == 200
    except (OSError, urllib.error.URLError, ValueError):
        return False


def _setting(name: str) -> str:
    """A plugin setting from the shell or the profile's ``.env`` (``hermes config set``)."""
    try:
        from hermes_cli.config import get_env_value

        return (get_env_value(name) or "").strip()
    except Exception:
        return os.environ.get(name, "").strip()


def autostart_enabled() -> bool:
    return _setting("TINFOIL_PROXY_AUTOSTART").lower() not in _OFF


def find_binary() -> str | None:
    """An installed ``tinfoil-proxy``: ``TINFOIL_PROXY_BIN``, then ``PATH``, then usual install dirs."""
    override = _setting("TINFOIL_PROXY_BIN")
    if override:
        path = Path(override).expanduser()
        return str(path) if path.is_file() and os.access(path, os.X_OK) else None
    found = shutil.which(_BINARY)
    if found:
        return found
    home = Path.home()
    for directory in (home / "go" / "bin", home / ".local" / "bin",
                      Path("/usr/local/bin"), Path("/opt/homebrew/bin")):
        candidate = directory / _BINARY
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def _log_path() -> Path:
    from plugins.plugin_storage import plugin_data_dir

    return plugin_data_dir("tinfoil-provider") / "proxy.log"


def _child_env() -> dict[str, str]:
    return {name: value for name, value in os.environ.items()
            if not name.upper().endswith(_SECRET_SUFFIXES)}


def _spawn(binary: str, port: int) -> subprocess.Popen:
    options: dict[str, object] = {}
    if os.name == "nt":
        options["creationflags"] = (
            subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        )
    else:
        options["start_new_session"] = True  # survive the terminal that started Hermes
    # The user's own flags come last so they win, e.g. to pin an enclave and its repo.
    extra = shlex.split(_setting("TINFOIL_PROXY_ARGS"))
    with open(_log_path(), "ab") as log:
        return subprocess.Popen(
            [binary, "--port", str(port), "--log-format", "text", *extra],
            stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
            env=_child_env(), close_fds=True, **options,
        )


def ensure_running(base_url: str, *, wait: float = 20.0) -> bool:
    """Make sure a verified proxy answers at *base_url*; start one if nothing listens there.

    Returns whether the proxy is up. It never raises: a caller that gets False
    simply proceeds, and its request fails closed against the dead port.
    """
    endpoint = loopback_endpoint(base_url)
    if endpoint is None:
        return False  # a proxy on another machine is the user's to run
    host, port = endpoint
    if is_listening(host, port):
        return True
    if not autostart_enabled():
        return False
    with _lock:
        if is_listening(host, port):
            return True
        if time.monotonic() - _last_failure.get(endpoint, float("-inf")) < _RETRY_AFTER_FAILURE:
            return False
        binary = find_binary()
        if binary is None:
            _last_failure[endpoint] = time.monotonic()
            _warn_once(
                "missing",
                "Tinfoil: no verifier proxy is listening on %s:%s and no tinfoil-proxy binary is "
                "installed. Install it (%s) or start the Tinfoil Proxy app.", host, port, INSTALL_URL)
            return False
        try:
            child = _spawn(binary, port)
        except OSError as exc:
            _last_failure[endpoint] = time.monotonic()
            _warn_once("spawn", "Tinfoil: could not start %s: %s", binary, exc)
            return False
        logger.info("Tinfoil: started %s on %s:%s (pid %s)", binary, host, port, child.pid)
        deadline = time.monotonic() + wait
        while time.monotonic() < deadline:
            if is_verified(host, port, timeout=1.0):
                return True
            # Another Hermes may have won the port; then our child exits and theirs answers.
            if child.poll() is not None and not is_listening(host, port):
                break
            time.sleep(0.25)
        _last_failure[endpoint] = time.monotonic()
        _warn_once(
            "unverified",
            "Tinfoil: the verifier proxy did not finish attestation within %.0fs "
            "(it needs network access at startup). See %s", wait, _log_path())
        return False
