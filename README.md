# Tinfoil provider for Hermes Agent

[Tinfoil](https://tinfoil.sh/) runs open models inside attested confidential
enclaves: the provider cannot read your prompts or the model's outputs. That
property only holds when the **client** verifies the enclave's attestation on
every connection, so this plugin routes Hermes through the locally running
[`tinfoil-proxy`](https://docs.tinfoil.sh/), which performs that verification
and forwards plain OpenAI-compatible requests. Hermes itself never talks to
Tinfoil directly.

```bash
hermes plugins install counterposition/tinfoil-hermes-provider
```

After that, **Tinfoil** appears in `hermes model`, `/model`, `hermes setup`,
and `hermes doctor`. No file placement, no core patch, and no
`hermes plugins enable` step (see [Installing](#installing)).

With the proxy running, run **`hermes model`**, choose **Tinfoil**, enter your
API key when prompted, accept the local Base URL, and select a chat model.
The picker saves your settings. `hermes config` displays configuration;
it does not prompt for a key.

## Prerequisite: the local proxy is the provider

Install and run `tinfoil-proxy` on every machine that runs Hermes. The proxy
binds loopback only (`127.0.0.1:3301`), so a proxy on one machine does not
serve another.

- **CLI proxy:** follow [Tinfoil's installation guide](https://docs.tinfoil.sh/local-proxy/cli). On macOS,
  `scripts/sh.tinfoil.proxy.plist` is a LaunchAgent that keeps it running:

  ```bash
  mkdir -p ~/Library/LaunchAgents
  cp "${HERMES_HOME:-$HOME/.hermes}"/plugins/tinfoil-provider/scripts/sh.tinfoil.proxy.plist ~/Library/LaunchAgents/
  # edit the exec line if the binary is not at ~/go/bin/tinfoil-proxy
  launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/sh.tinfoil.proxy.plist
  ```

  (`$HERMES_HOME` defaults to `~/.hermes`.) The proxy binds its port first
  and then attests against the public transparency log, so **startup needs
  network**. On a laptop it can come up before the VPN or DHCP settles and
  exit; the plist's `KeepAlive` and 15 s `ThrottleInterval` handle the retry.
- **Menu-bar app:** Tinfoil's Proxy app serves the same endpoint and is
  interchangeable with the CLI proxy.

A bound port is not proof of attestation. This is:

```bash
curl --fail --silent --show-error --max-time 10 http://127.0.0.1:3301/verification-document
```

### The direct endpoint, and why this plugin does not use it

Tinfoil also documents a direct endpoint, `https://inference.tinfoil.sh/v1`,
reachable with a plain Bearer key. Tinfoil's own page on it says direct access
skips the attestation verification its SDKs perform, offers no protection
against man-in-the-middle attacks, and is meant for debugging and simple
testing only. Pointing `TINFOIL_BASE_URL` there works, but you get a provider
named Tinfoil with none of Tinfoil's value. This plugin will never default to
it.

## Configuration

| Variable | Required | Meaning |
| --- | --- | --- |
| `TINFOIL_API_KEY` | yes | Your Tinfoil API key. The proxy forwards it per request as Bearer auth; the proxy itself takes no key. |
| `TINFOIL_BASE_URL` | no | Override the proxy URL. Default `http://127.0.0.1:3301/v1`. Legitimate uses: a non-default proxy port, or a proxy on another host on a private network (the hop from Hermes to that proxy is then outside the attested path). |

`hermes model` prompts for the key and optional Base URL; the installer also
prompts for `TINFOIL_API_KEY` when run interactively. Use the picker to choose
both the provider and model. Setting only `model.provider: tinfoil` by hand
is not enough: Hermes does not yet choose a model automatically for plugins.

## Models

The in-chat `/model` picker combines the current model list with a short
backup list of stable chat models that support tools. To list current chat choices:

```bash
curl -fsS --max-time 10 http://127.0.0.1:3301/v1/models \
  | jq -r '.data[] | select(.type == "chat" and .tool_calling == true and .deprecated != true) | .id'
```

The unfiltered catalog includes embeddings, speech, and other non-chat
entries. The `hermes model` setup screen gets its list from Hermes' model
database or the proxy. This screen does not use the plugin's backup list.
Choose a chat model; if the proxy is offline, enter its ID manually.
The in-chat picker can still show the backup list offline.

Summaries and tool helpers default to `gpt-oss-120b`. Automatic image analysis
uses `gemma4-31b` through the same proxy, including when the main model is
text-only. If you set your own provider or model under `auxiliary`, Hermes
uses your choice instead.

The September 13 catalog marks `deepseek-v4-flash` deprecated with a September
15 retirement date. It is no longer in the backup list. Its replacement
`deepseek-v4-1-flash` and `glm-5-3-flash` are currently experimental; live
model lists from the proxy still show them, but the backup list does not.

## Reasoning effort

Tinfoil takes `reasoning_effort` as a top-level request field, and the
accepted values differ per model. The plugin uses Hermes' shared helpers to
map your setting to a value that [Tinfoil accepts](https://docs.tinfoil.sh/guides/reasoning):

| Model | Accepted by Tinfoil | Hermes `reasoning.effort` sent as |
| --- | --- | --- |
| `kimi-k3`, `glm-5-3`, `glm-5-3-flash` | `low`, `high`, `max` | `none`, `minimal`, `low` → `low`; `medium`, `high` → `high`; `xhigh`, `max`, `ultra` → `max`. |
| `gpt-oss-120b`, `gpt-oss-safeguard-120b` | `low`, `medium`, `high` | `none`, `minimal` → `low`; `xhigh`, `max`, `ultra` → `high`. |
| `deepseek-v4-flash`, `gemma4-31b` | `none` … `max` | Passed through; `ultra` → `max`. |
| `llama3-3-70b` and unknown/new models | not specified here | No effort setting is sent; the server chooses its default. |

Disabling reasoning sends `none` where supported, otherwise `low`: always-on
models cannot stop thinking, and omitting the field can select their most
expensive default. Unset or invalid effort settings omit the field. Unknown
model IDs do not inherit assumptions from similarly named models.

Thinking comes back in a separate `reasoning` response field, which Hermes
already handles. DeepSeek V4 Flash reasons only when effort is set.

## Installing

`hermes plugins install` clones this repository into
`$HERMES_HOME/plugins/tinfoil-provider/`. Hermes' provider registry imports
any directory there whose `plugin.yaml` declares `kind: model-provider`, so
the provider is live on the next `hermes` invocation. The generic installer
still ends with "Plugin installed but not enabled. Run `hermes plugins enable
tinfoil-provider` to activate." That hint applies to plugin-manager plugins,
not model providers; enabling is harmless but does nothing here.

**Per profile.** Each Hermes profile has its own `HERMES_HOME`, so install
once per profile that should see Tinfoil:

```bash
hermes -p <profile> plugins install counterposition/tinfoil-hermes-provider
```

Upstream issue [#88143](https://github.com/NousResearch/hermes-agent/issues/88143)
tracks profile creation not seeding `plugins/`.

`hermes plugins list` shows the plugin with status "not enabled" and source
"git". That status is the plugin manager's view and does not affect the
provider; Tinfoil is selectable regardless.

**Verify.** `hermes doctor` probes the proxy's `/v1/models` once
`TINFOIL_API_KEY` is set. With the proxy running it prints `✓ Tinfoil`; with
the proxy down it prints `⚠ Tinfoil ([Errno 111] Connection refused)`, which
indicates a connection failure. A passing catalog probe establishes
reachability, not API-key validity or successful inference: the proxy's
catalog is public. Check `/verification-document` to see whether the proxy
has finished checking the enclave.

## Removing

```bash
hermes plugins remove tinfoil-provider
```

Tinfoil disappears from the picker on the next invocation. If it was selected,
run `hermes model` to choose another provider. Remove its saved key with
`hermes config unset TINFOIL_API_KEY` if no longer needed.

## Development

Validated against Hermes **0.21.2**, upstream commit
[`205645ee4241`](https://github.com/NousResearch/hermes-agent/commit/205645ee424163c7b6cfc032c331c3557797497b)
on September 13, 2026. Use a current Hermes checkout: this plugin relies on
its provider hooks and `agent.reasoning_effort` helpers.

The tests load the plugin through Hermes' real discovery path (a temporary
`HERMES_HOME` with the repo copied to `plugins/tinfoil-provider/`), stub the
proxy, and make no external network calls. They need a hermes-agent checkout
with its dependencies installed:

```bash
HERMES_AGENT_SRC=~/c/vendor/hermes-agent
"$HERMES_AGENT_SRC/scripts/run_tests.sh" "$PWD/tests" -q
```

Hermes' runner runs each test file separately and removes real credentials
from the test environment. The tests check plugin loading, request settings,
user choices, model lists, connection checks, image analysis, and old Hermes imports.

`e2e/` holds a container harness (Docker or OrbStack) that installs the
plugin into an upstream checkout with the real `hermes plugins install` and
walks the install, list, picker, doctor, precedence, and remove steps. See
`e2e/README.md`.
