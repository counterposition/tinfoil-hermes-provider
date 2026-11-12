"""The ``tinfoil`` terminal backend, loaded the way Hermes loads an installed plugin."""

from __future__ import annotations

import json
import shutil
import stat
import sys
from pathlib import Path

import pytest
from conftest import SANDBOX_DIR, SANDBOX_PLUGIN

# Stands in for the tinfoil CLI. ``tinfoil ssh <container> ... -- <ssh options> <command>``
# records how it was called, then runs the command the way sshd would: the
# remote words joined with spaces and handed to a shell. Its record and refusal
# files are baked in, since the plugin passes the CLI only an allowlisted environment.
STAND_IN = '''#!{python}
import json, os, sys

args = sys.argv[1:]
with open({record!r}, "a") as out:
    out.write(json.dumps({{
        "argv": args,
        "tunnel_key": os.environ.get("TINFOIL_TUNNEL_API_KEY", ""),
        "admin_key": os.environ.get("TINFOIL_ADMIN_KEY", ""),
        "env": dict(os.environ),
    }}) + "\\n")
if os.path.exists({refuse!r}):
    sys.stderr.write("attestation verification failed: measurement mismatch\\n")
    sys.exit(1)
if "--port" not in args[:args.index("--")]:
    # The real CLI logs this before every command when the container record has no SSH port.
    sys.stderr.write('time="2026-11-10T22:44:19-05:00" level=info '
                     'msg="No SSH port known for ' + args[1] + ', trying 22"\\n')
    sys.stderr.flush()
rest = args[args.index("--") + 1:]
while rest and rest[0].startswith("-"):
    rest = rest[2:]
os.execvp("sh", ["sh", "-c", " ".join(rest)])
'''


def _write_config(home, **terminal):
    """config.yaml with the plugin enabled; JSON is YAML, so no YAML writer is needed."""
    config = {"plugins": {"enabled": [SANDBOX_PLUGIN]}}
    if terminal:
        config["terminal"] = {"backend": "tinfoil", **terminal}
    (home / "config.yaml").write_text(json.dumps(config), encoding="utf-8")


def _select(home, **terminal):
    _write_config(home, **terminal)


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A Hermes home holding only the sandbox plugin, installed and enabled."""
    from hermes_cli import plugins

    home = tmp_path / "hermes-home"
    shutil.copytree(SANDBOX_DIR, home / "plugins" / SANDBOX_PLUGIN,
                    ignore=shutil.ignore_patterns("__pycache__"))
    _write_config(home)
    bundled = tmp_path / "no-bundled-plugins"
    bundled.mkdir()
    workspace = tmp_path / "container-home"
    workspace.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_BUNDLED_PLUGINS", str(bundled))
    monkeypatch.setenv("HOME", str(workspace))  # what the stand-in container calls home
    plugins._reset_plugin_managers_for_tests()
    plugins.discover_plugins()
    yield home
    plugins._reset_plugin_managers_for_tests()


@pytest.fixture
def backend(home):
    from agent import terminal_env_registry as registry
    from hermes_cli import plugins

    manager = plugins.get_plugin_manager()
    [row] = [p for p in manager.list_plugins() if p["name"] == SANDBOX_PLUGIN]
    assert row["enabled"] is True and not row.get("error"), row
    provider = registry.get_provider("tinfoil", scope=manager.scope_key)
    assert provider is not None, "the plugin did not register its terminal backend"
    return provider


class StandIn:
    """The stand-in CLI: ``cli()`` lists its calls, ``cli.refuse()`` makes it fail attestation."""

    def __init__(self, directory: Path):
        self.binary = directory / "tinfoil"
        self.record = directory / "calls.jsonl"
        self.refusal = directory / "refuse"
        self.binary.write_text(STAND_IN.format(
            python=sys.executable, record=str(self.record), refuse=str(self.refusal)), encoding="utf-8")
        self.binary.chmod(self.binary.stat().st_mode | stat.S_IXUSR)

    def __call__(self):
        if not self.record.exists():
            return []
        return [json.loads(line) for line in self.record.read_text().splitlines()]

    def refuse(self):
        self.refusal.touch()


@pytest.fixture
def cli(tmp_path, monkeypatch):
    stand_in = StandIn(tmp_path)
    monkeypatch.setenv("TINFOIL_CLI", str(stand_in.binary))
    return stand_in




@pytest.fixture
def environment(home, backend, cli, monkeypatch, tmp_path):
    monkeypatch.setenv("TINFOIL_API_KEY", "tk_inference_key")
    _select(home, tinfoil_container="my-box", tinfoil_repo="acme/workspace@v1.2.0")
    env = backend.create_environment(cwd="~", timeout=30, task_id="t1")
    yield env
    env.cleanup()
    # The base class keeps its session files in the container's /tmp, which here is this machine's.
    for leftover in (env._snapshot_path, env._cwd_file):
        Path(leftover).unlink(missing_ok=True)


def test_backend_is_classified_for_hermes_policy_sites(backend):
    from agent import terminal_env_registry as registry
    from tools.approval import _should_skip_container_guards

    assert "tinfoil" in registry.plugin_backend_names()
    assert registry.provider_flag("tinfoil", "is_remote") is True
    assert registry.provider_flag("tinfoil", "is_container") is True
    # A long-lived workspace: dangerous commands still ask first.
    assert _should_skip_container_guards("tinfoil") is False
    assert backend.default_cwd == "~"
    assert "TINFOIL_API_KEY" in registry.plugin_strip_env_keys()


def test_needs_setup_until_the_cli_and_a_container_are_present(home, backend, cli, monkeypatch, tmp_path):
    assert backend.is_available() is False
    status, detail = backend.probe()
    assert status == "needs_setup" and "container" in detail
    with pytest.raises(RuntimeError, match="terminal.tinfoil_container"):
        backend.create_environment(cwd="~", timeout=30)

    _select(home, tinfoil_container="my-box")
    assert backend.is_available() is True
    assert backend.probe() == ("ready", "")
    assert all(ok for ok, _label, _detail in backend.doctor_checks()[:2])

    monkeypatch.setenv("TINFOIL_CLI", str(tmp_path / "not-installed"))
    assert backend.is_available() is False
    assert "CLI" in backend.probe()[1]


def test_a_bare_hostname_must_name_the_expected_config(home, backend, cli):
    _select(home, tinfoil_container="box.acme.containers.tinfoil.dev")
    assert backend.is_available() is False
    assert "tinfoil_repo" in backend.probe()[1]
    _select(home, tinfoil_container="box.acme.containers.tinfoil.dev", tinfoil_repo="acme/workspace")
    assert backend.is_available() is True


def test_container_can_be_named_in_the_environment(home, backend, cli, monkeypatch):
    monkeypatch.setenv("TINFOIL_CONTAINER", "env-box")
    assert backend.is_available() is True
    assert any("env-box" in line for line in backend.setup_instructions())


def test_commands_run_in_the_container_through_the_attested_tunnel(environment, cli, tmp_path):
    result = environment.execute("echo hello from $(basename $PWD)")
    assert result["returncode"] == 0
    assert result["output"].strip() == "hello from container-home"
    assert environment.cwd == str(tmp_path / "container-home")  # "~" resolved inside the container

    for call in cli():
        argv = call["argv"]
        assert argv[:4] == ["ssh", "my-box", "--user", "root"]
        assert argv[argv.index("--repo") + 1] == "acme/workspace@v1.2.0"
        assert "--allow-debug" not in argv
        assert call["tunnel_key"] == "tk_inference_key"
        ssh_options = argv[argv.index("--") + 1:]
        assert "BatchMode=yes" in ssh_options  # never waits on a prompt
        assert "ControlMaster=auto" in ssh_options  # one verified connection, reused


def test_cli_port_notice_stays_out_of_tool_output(environment, cli):
    # Hermes' file tools parse command output; a stray CLI log line in front of it made
    # read_file report a written file as empty and search_files report grep as missing.
    from tools.file_operations import ShellFileOperations

    files = ShellFileOperations(environment)
    assert not files.write_file("hello.py", "print(sum(range(1, 101)))\n").error
    read = files.read_file("hello.py")
    assert read.error is None and "print(sum(range(1, 101)))" in read.content
    assert "level=info" not in environment.execute("echo ok")["output"]
    later = cli()[-1]["argv"]
    assert later[later.index("--port") + 1] == "22"  # the port the CLI fell back to, pinned


def test_file_tool_tilde_paths_mean_the_container_home(environment, home, tmp_path):
    # Hermes expands ``~`` in file-tool paths with this machine's home; the plugin's
    # pre_tool_call hook rewrites it to the home the container login resolved.
    from hermes_cli.plugins import _dispatch_pre_tool_call_hooks

    container_home = str(tmp_path / "container-home")
    _, args = _dispatch_pre_tool_call_hooks("write_file", {"path": "~/health/report.py", "content": "x"})
    assert args == {"path": container_home + "/health/report.py", "content": "x"}
    assert _dispatch_pre_tool_call_hooks("read_file", {"path": "/etc/hostname"})[1] is None
    assert _dispatch_pre_tool_call_hooks("terminal", {"command": "ls ~"})[1] is None

    _write_config(home)  # another backend selected: paths are left alone
    assert _dispatch_pre_tool_call_hooks("read_file", {"path": "~/notes.md"})[1] is None


def test_working_directory_and_exports_persist_between_commands(environment):
    assert environment.execute("mkdir -p project && cd project && export GREETING=hi")["returncode"] == 0
    result = environment.execute("echo $GREETING in $(basename $PWD)")
    assert result["output"].strip() == "hi in project"


def test_file_content_reaches_the_container_over_stdin(environment, tmp_path):
    payload = "line one\nline 'two' with $dollars and \"quotes\"\n"
    assert environment.execute("cat > notes.txt", stdin_data=payload)["returncode"] == 0
    assert (tmp_path / "container-home" / "notes.txt").read_text() == payload


def test_failed_commands_report_their_exit_code(environment):
    result = environment.execute("echo before; exit 7")
    assert result["returncode"] == 7 and "before" in result["output"]


def test_refused_attestation_is_a_connection_failure_not_a_command_result(
    home, backend, cli, monkeypatch,
):
    from tools.environments.base import EnvironmentConnectionError

    _select(home, tinfoil_container="my-box")
    cli.refuse()
    with pytest.raises(EnvironmentConnectionError) as failure:
        backend.create_environment(cwd="~", timeout=30)
    assert "measurement mismatch" in failure.value.reason
    assert "tinfoil container get my-box" in failure.value.retry_hint


def test_admin_key_is_never_sent_as_the_tunnel_key(home, backend, cli, monkeypatch):
    monkeypatch.setenv("TINFOIL_API_KEY", "admin_org_key")
    cli.refuse()
    _select(home, tinfoil_container="my-box", tinfoil_user="app", tinfoil_ssh_port=2022,
            tinfoil_allow_debug=True)
    with pytest.raises(RuntimeError):
        backend.create_environment(cwd="~", timeout=30)
    [call] = cli()
    assert call["tunnel_key"] == ""
    # The CLI itself accepts an admin key in TINFOIL_API_KEY for the control plane.
    assert call["admin_key"] == "admin_org_key"
    assert "TINFOIL_API_KEY" not in call["env"]
    assert call["argv"][:7] == ["ssh", "my-box", "--user", "app", "--port", "2022", "--allow-debug"]
    assert backend.doctor_checks()[2][0] is False


def test_only_allowlisted_variables_reach_the_cli(home, backend, cli, monkeypatch):
    # Hermes' process environment holds every provider's key; the CLI and ssh get none of them.
    leaked = {
        "OPENROUTER_API_KEY": "sk-or-secret",
        "ANTHROPIC_API_KEY": "sk-ant-secret",
        "GITHUB_TOKEN": "ghp_secret",
        "TINFOIL_API_KEY": "tk_inference_key",
        "TINFOIL_SOMETHING_SECRET": "unknown-tinfoil-setting",
        "BASH_ENV": "/nonexistent/startup.sh",
        "LC_SERVICE_TOKEN": "looks-like-a-locale",
    }
    kept = {
        "SSH_AUTH_SOCK": "/tmp/agent.sock",
        "LC_MESSAGES": "C",
        "no_proxy": ".internal",
        "SSL_CERT_FILE": "/etc/ssl/cert.pem",
        "TINFOIL_CONTROLPLANE_URL": "https://controlplane.example",
    }
    for name, value in {**leaked, **kept}.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("TINFOIL_ADMIN_KEY", "admin_org_key")
    _select(home, tinfoil_container="my-box")
    environment = backend.create_environment(cwd="~", timeout=30, task_id="t-env")
    try:
        # A command with stdin goes through Hermes' spawn helper, the connect step through _capture.
        assert environment.execute("cat > env.txt", stdin_data="x\n")["returncode"] == 0
    finally:
        environment.cleanup()
        for leftover in (environment._snapshot_path, environment._cwd_file):
            Path(leftover).unlink(missing_ok=True)

    calls = cli()
    assert len(calls) >= 2
    for call in calls:
        env = call["env"]
        assert not set(leaked) & set(env), sorted(set(leaked) & set(env))
        assert {name: env.get(name) for name in kept} == kept
        assert env["PATH"] and env["HOME"]
        assert env["TINFOIL_TUNNEL_API_KEY"] == "tk_inference_key"
        assert env["TINFOIL_ADMIN_KEY"] == "admin_org_key"
        assert env["TINFOIL_NO_UPDATE_CHECK"] == "1"


def test_windows_allowlist_ignores_case_and_keeps_what_windows_needs(backend):
    environment_module = sys.modules[type(backend).__module__.rsplit(".", 1)[0] + ".environment"]
    windows = {
        "Path": r"C:\Windows\System32", "SystemRoot": r"C:\Windows", "USERPROFILE": r"C:\Users\me",
        "TEMP": r"C:\Temp", "PATHEXT": ".EXE", "https_proxy": "http://proxy:3128",
        "OPENAI_API_KEY": "sk-secret", "ComSpec": r"C:\Windows\cmd.exe",
    }
    assert set(environment_module.inherited_env(windows, windows=True)) == {
        "Path", "SystemRoot", "USERPROFILE", "TEMP", "PATHEXT", "https_proxy"}
    # Elsewhere names are case-sensitive and the Windows additions do not apply.
    assert set(environment_module.inherited_env(windows, windows=False)) == {"https_proxy"}


def test_sandbox_plugin_installs_without_dependencies_and_passes_the_scan():
    from pm.plugin_declarations import read_python_declaration
    from tools.plugin_guard import scan_plugin

    assert not read_python_declaration(SANDBOX_DIR).installs_packages
    result = scan_plugin(SANDBOX_DIR, source="tinfoil-hermes-provider/sandbox")
    assert result.verdict == "safe", [f for f in result.findings if f.severity in ("critical", "high")]
