# Install E2E

`run.sh` builds a throwaway container from a hermes-agent checkout (tracked
files only) and runs `inside.sh` in it: the real `hermes plugins install`,
`hermes plugins list`, the `hermes model` picker (driven through a pty),
`hermes doctor` with the proxy up and down, the installed-beats-bundled
precedence check, optional live conversation checks, and
`hermes plugins remove`. The container has its own `HERMES_HOME`; nothing on
the host is touched.

Use a Hermes Agent checkout that supports Python 3.14. The container uses
Hermes' lockfile for its runtime and this project's `e2e` dependency group
for the terminal driver.

```bash
HERMES_AGENT_SRC=~/c/vendor/hermes-agent \
GH_TOKEN="$(gh auth token)" \
FORK_TINFOIL_DIR=~/c/mine/hermes-agent/plugins/model-providers/tinfoil \
e2e/run.sh
```

- `GH_TOKEN` is only needed while the repository is private.
- The host's `tinfoil-proxy` is reached through `PROXY_HOST`
  (default `host.docker.internal`) and forwarded to `127.0.0.1:3301` inside
  the container, so the plugin's default `base_url` is exercised unchanged.
- Set `TINFOIL_API_KEY` to run the live conversation checks
  (`live_checks.py`); they are skipped otherwise.
- `PLUGIN_ID` can point at the mounted local clone (`file:///plugin`);
  Git installs its committed HEAD, not uncommitted changes. The installer
  warns about the `file://` scheme. The Python tests described in the main
  README load the current working files directly.
