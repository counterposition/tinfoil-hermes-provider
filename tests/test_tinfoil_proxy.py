"""Hermes starts an installed verifier proxy on demand, and never routes around it."""

from __future__ import annotations

import importlib
import json
import os
import signal
import socket
import stat
import sys
import time

import pytest

# Stands in for tinfoil-proxy: binds the port it is given, then answers the
# verification and catalog routes. It records its argv and environment for the test.
STAND_IN = '''#!{python}
import json, os, sys
from http.server import BaseHTTPRequestHandler, HTTPServer

port = int(sys.argv[sys.argv.index("--port") + 1])
record = {record}  # baked in: the proxy is started with an allowlisted environment


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps({{"data": [{{"id": "started-on-demand", "type": "chat"}}]}}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


server = HTTPServer(("127.0.0.1", port), Handler)
with open(record, "w") as out:
    json.dump({{"pid": os.getpid(), "argv": sys.argv[1:], "env": sorted(os.environ)}}, out)
server.serve_forever()
'''


@pytest.fixture
def proxy(plugin_module):
    module = importlib.import_module(f"{plugin_module.__name__}.proxy")
    module._last_failure.clear()
    module._warned.clear()
    yield module
    module._last_failure.clear()
    module._warned.clear()


@pytest.fixture
def free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@pytest.fixture
def stand_in(tmp_path, monkeypatch):
    """An installed proxy binary, plus the record it writes once it is serving."""
    binary = tmp_path / "tinfoil-proxy"
    record = tmp_path / "started.json"
    binary.write_text(STAND_IN.format(python=sys.executable, record=repr(str(record))),
                      encoding="utf-8")
    binary.chmod(binary.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("TINFOIL_PROXY_BIN", str(binary))
    monkeypatch.setenv("TINFOIL_PROXY_AUTOSTART", "1")
    yield record
    if record.exists():
        os.kill(json.loads(record.read_text())["pid"], signal.SIGTERM)


def test_starts_the_installed_proxy_when_nothing_listens(proxy, stand_in, free_port, monkeypatch):
    monkeypatch.setenv("TINFOIL_API_KEY", "must-not-reach-the-proxy")
    monkeypatch.setenv("FAL_KEY", "must-not-reach-the-proxy")
    assert not proxy.is_listening("127.0.0.1", free_port)
    assert proxy.ensure_running(f"http://127.0.0.1:{free_port}/v1", wait=15) is True
    started = json.loads(stand_in.read_text())
    assert started["argv"][:2] == ["--port", str(free_port)]
    # The proxy outlives Hermes, so it is not handed the agent's credentials.
    assert "TINFOIL_API_KEY" not in started["env"]
    assert "FAL_KEY" not in started["env"]
    assert proxy.is_verified("127.0.0.1", free_port)
    # A second call finds it running and starts nothing.
    stand_in.unlink()
    assert proxy.ensure_running(f"http://127.0.0.1:{free_port}/v1") is True
    assert not stand_in.exists()
    stand_in.write_text(json.dumps(started))  # hand the pid back for teardown


def test_extra_flags_reach_the_started_proxy(proxy, stand_in, free_port, monkeypatch):
    """Pinning an enclave, or any other proxy flag, does not require giving up auto-start."""
    monkeypatch.setenv("TINFOIL_PROXY_ARGS", "-e inference.tinfoil.sh -r 'tinfoilsh/confidential-model-router'")
    assert proxy.ensure_running(f"http://127.0.0.1:{free_port}/v1", wait=15) is True
    assert json.loads(stand_in.read_text())["argv"][-4:] == [
        "-e", "inference.tinfoil.sh", "-r", "tinfoilsh/confidential-model-router",
    ]


def _only_env(monkeypatch, values: dict[str, str]) -> None:
    for name in list(os.environ):
        monkeypatch.delenv(name)
    for name, value in values.items():
        monkeypatch.setenv(name, value)


# What the detached proxy needs to run, reach the network and trust certificates.
_RUNTIME_ENV = {
    "PATH": "/usr/bin:/bin", "HOME": "/home/u", "USER": "u", "TMPDIR": "/tmp/u", "TZ": "UTC",
    "LANG": "en_CA.UTF-8", "LC_ALL": "C", "LC_CTYPE": "UTF-8", "LC_MESSAGES": "C",
    "SSL_CERT_FILE": "/etc/ssl/cert.pem", "SSL_CERT_DIR": "/etc/ssl/certs",
    "HTTPS_PROXY": "http://corp:3128", "https_proxy": "http://corp:3128",
    "NO_PROXY": "localhost", "no_proxy": "localhost",
}

# Hermes and user secrets under names no suffix rule catches, plus the old suffix
# cases, other TINFOIL_* variables and runtime knobs the proxy has no use for.
_WITHHELD_ENV = {
    "FAL_KEY": "x", "VOICE_TOOLS_OPENAI_KEY": "x", "API_SERVER_KEY": "x", "GATEWAY_PROXY_KEY": "x",
    "OPENAI_API_KEY": "x", "GITHUB_TOKEN": "x", "DB_PASSWORD": "x", "TINFOIL_ADMIN_KEY": "x",
    "TINFOIL_API_KEY": "x", "TINFOIL_USER_CACHE_SECRET": "x", "TINFOIL_PROXY_ARGS": "-e x",
    "LC_SERVICE_TOKEN": "x", "ALL_PROXY": "socks5://x", "GODEBUG": "x", "DYLD_INSERT_LIBRARIES": "x",
    "LD_PRELOAD": "x", "PYTHONPATH": "x", "SSH_AUTH_SOCK": "/tmp/agent",
}


def test_proxy_environment_is_an_allowlist(proxy, monkeypatch):
    """The proxy outlives Hermes, so it inherits only what it needs, whatever the other names are."""
    _only_env(monkeypatch, {**_RUNTIME_ENV, **_WITHHELD_ENV})
    assert proxy._child_env() == _RUNTIME_ENV


def test_proxy_environment_on_windows(proxy, monkeypatch):
    """Windows names are case-insensitive, and a Go program there also needs its system dirs."""
    windows = {"Path": r"C:\Windows", "SystemRoot": r"C:\Windows", "windir": r"C:\Windows",
               "USERPROFILE": r"C:\Users\u", "TEMP": r"C:\Temp", "TMP": r"C:\Temp",
               "Https_Proxy": "http://corp:3128"}
    _only_env(monkeypatch, {**windows, **_WITHHELD_ENV})
    with monkeypatch.context() as patch:
        patch.setattr(proxy.os, "name", "nt")
        child = proxy._child_env()
    assert child == windows


def test_model_list_brings_the_proxy_up(profile, catalog, proxy, stand_in, free_port):
    """`hermes model` on a machine where the proxy was never started still lists live models."""
    models = profile.fetch_models(api_key="k", base_url=f"http://127.0.0.1:{free_port}/v1")
    assert models == ["started-on-demand"]


def test_client_construction_brings_the_proxy_up_and_keeps_the_standard_client(
    profile, catalog, proxy, stand_in, free_port,
):
    assert profile.create_client(api_key="k", base_url=f"http://127.0.0.1:{free_port}/v1") is None
    assert proxy.is_listening("127.0.0.1", free_port)


@pytest.mark.parametrize("base_url", [
    "https://inference.tinfoil.sh/v1",  # the unverified direct endpoint
    "http://proxy.internal:3301/v1",  # a proxy on another machine
    "http://127.0.0.1/v1",  # no port to bind
    "",
])
def test_never_starts_a_proxy_for_an_address_it_does_not_own(proxy, stand_in, base_url):
    assert proxy.ensure_running(base_url, wait=1) is False
    assert not stand_in.exists()


def test_opting_out_leaves_the_port_alone(proxy, stand_in, free_port, monkeypatch):
    monkeypatch.setenv("TINFOIL_PROXY_AUTOSTART", "0")
    assert proxy.ensure_running(f"http://127.0.0.1:{free_port}/v1", wait=1) is False
    assert not stand_in.exists()


def test_missing_binary_fails_closed_without_raising(proxy, free_port, monkeypatch, tmp_path, caplog):
    monkeypatch.setenv("TINFOIL_PROXY_AUTOSTART", "1")
    monkeypatch.setenv("TINFOIL_PROXY_BIN", str(tmp_path / "not-installed"))
    url = f"http://127.0.0.1:{free_port}/v1"
    with caplog.at_level("WARNING"):
        assert proxy.ensure_running(url, wait=1) is False
        assert proxy.ensure_running(url, wait=1) is False
    # Said once, with where to get it; the request then fails against the dead port.
    assert sum("tinfoil-proxy" in record.getMessage() for record in caplog.records) == 1
    assert not proxy.is_listening("127.0.0.1", free_port)


def test_a_proxy_that_never_verifies_is_not_reported_ready(proxy, free_port, monkeypatch, tmp_path):
    pid_file = tmp_path / "pid"
    binary = tmp_path / "tinfoil-proxy"
    binary.write_text(
        f"#!{sys.executable}\nimport os, time\n"
        f"open({str(pid_file)!r}, 'w').write(str(os.getpid()))\ntime.sleep(30)\n",
        encoding="utf-8")
    binary.chmod(binary.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("TINFOIL_PROXY_AUTOSTART", "1")
    monkeypatch.setenv("TINFOIL_PROXY_BIN", str(binary))
    started = time.monotonic()
    try:
        assert proxy.ensure_running(f"http://127.0.0.1:{free_port}/v1", wait=1.5) is False
        assert time.monotonic() - started < 10
    finally:
        if pid_file.exists():
            os.kill(int(pid_file.read_text()), signal.SIGTERM)
