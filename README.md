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

Requires a hermes-agent that discovers provider plugins from the flat install
directory (upstream commit `0cbc6e37ac`, merged 2026-09-02; v0.21.0 and
later).

## Prerequisite: the local proxy is the provider

Install and run `tinfoil-proxy` on every machine that runs Hermes. The proxy
binds loopback only (`127.0.0.1:3301`), so a proxy on one machine does not
serve another.

- **CLI proxy:** follow [Tinfoil's docs](https://docs.tinfoil.sh/). On macOS,
  `scripts/sh.tinfoil.proxy.plist` is a LaunchAgent that keeps it running:

  ```bash
  cp "$HERMES_HOME"/plugins/tinfoil-provider/scripts/sh.tinfoil.proxy.plist ~/Library/LaunchAgents/
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
curl -s http://127.0.0.1:3301/verification-document | head -c 300
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

Set them with `hermes config` or `hermes setup`; the installer also prompts
for `TINFOIL_API_KEY` when run interactively. Then run `hermes model`, pick
**Tinfoil**, and pick a model. Prefer the picker over hand-editing
`model.provider`: upstream Hermes does not yet supply a silent default model
for plugin providers, so a config with `provider: tinfoil` and no `model`
resolves to an empty model.

## Models

The picker fetches the live catalog from the proxy and merges it with a short
curated list (chat-capable, tool-calling models, most capable first). The
live catalog is the truth:

```bash
curl -s http://127.0.0.1:3301/v1/models | jq -r '.data[].id'
```

It also lists non-chat entries (embeddings, speech, web search, document
upload). The in-chat `/model` picker puts the curated chat models at the top;
the `hermes model` setup flow currently shows the catalog in the proxy's own
order, so scroll past the non-chat entries there. The auxiliary model for
summaries and tool helpers defaults to `gpt-oss-120b`.

## Reasoning effort

Tinfoil takes `reasoning_effort` as a top-level request field, and the
accepted values differ per model family. Unsupported values are HTTP 400, so
the plugin translates Hermes' effort setting per family:

| Family | Accepted by Tinfoil | Hermes `reasoning.effort` sent as |
| --- | --- | --- |
| `kimi-*` | `low`, `high`, `max` | `low` → `low`; `medium`, `high` → `high`; `xhigh`, `max`, `ultra` → `max`. Disabling reasoning omits the field (always reasons). |
| `gpt-oss-*` | `low`, `medium`, `high` | `xhigh`, `max`, `ultra` → `high`; anything else outside the range → `medium`. Disabling omits the field. |
| `deepseek-*`, `glm-*`, `gemma*` | `none` … `max` (full range) | Passed through; `ultra` → `max`. Disabling sends `none`. |
| `llama*` and unknown models | varies | Field omitted; the server default applies. |

Thinking comes back in a separate `reasoning` response field. DeepSeek only
reasons when the field is sent, so leave Hermes' reasoning enabled for it.

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
is the "proxy is not running" signal.

## Removing

```bash
hermes plugins remove tinfoil-provider
```

Tinfoil disappears from the picker on the next session. Your
`TINFOIL_API_KEY` stays in `$HERMES_HOME/.env` until you remove it with
`hermes config`.

## Development

The tests load the plugin through Hermes' real discovery path (a temporary
`HERMES_HOME` with the repo copied to `plugins/tinfoil-provider/`), stub the
proxy, and make no network calls. They need a hermes-agent checkout with its
dependencies installed:

```bash
export HERMES_AGENT_SRC=~/c/vendor/hermes-agent   # a checkout containing 0cbc6e37ac
uv run --project "$HERMES_AGENT_SRC" --frozen pytest -q tests
```

Any interpreter that has hermes-agent's dependencies works, for example
`"$HERMES_AGENT_SRC"/.venv/bin/python -m pytest -q tests`. `HERMES_AGENT_SRC`
is only needed when the checkout is not already importable.

`e2e/` holds a container harness (Docker or OrbStack) that installs the
plugin into an upstream checkout with the real `hermes plugins install` and
walks the install, list, picker, doctor, precedence, and remove steps. See
`e2e/README.md`.

## License

MIT, matching hermes-agent.
