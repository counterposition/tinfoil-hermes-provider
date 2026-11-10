# Tinfoil Container backend for Hermes Agent

A terminal backend plugin: Hermes' `terminal`, `execute_code` and file tools
run inside a [Tinfoil Container](https://tinfoil.sh/containers), your own
image in an attested enclave.

```bash
hermes plugins install counterposition/tinfoil-hermes-provider/sandbox
```

```bash
hermes plugins enable tinfoil-sandbox
```

```bash
hermes config set terminal.tinfoil_container my-box
```

```bash
hermes config set terminal.backend tinfoil
```

Requirements, settings and limits are in the
[repository README](../README.md#run-tools-in-a-tinfoil-container).

## How it works

- `provider.py` registers the backend name `tinfoil` and answers Hermes'
  questions about it: where commands run, whether it is set up, what
  `hermes doctor` should show.
- `environment.py` runs each command as
  `tinfoil ssh <container> -- <ssh options> bash -c <command>`. The Tinfoil
  CLI verifies the enclave's attestation and tunnels SSH over that verified
  TLS connection. An SSH control socket under the system temp directory keeps
  the connection open between commands.
- The plugin has no Python dependencies and makes no network calls of its
  own. It runs two programs you installed: `tinfoil` and `ssh`.
