# Tinfoil Container backend installed

If you declined the "Enable now?" question, run
`hermes plugins enable tinfoil-sandbox` first.

1. **Install the Tinfoil CLI** (https://docs.tinfoil.sh/containers/cli) and
   sign it in to your organization once with `tinfoil login`.
2. **Create a workspace container.** The template at
   https://github.com/counterposition/hermes-tinfoil-workspace sets one up:
   copy it, put your SSH public key in its config, publish a release, then
   `tinfoil container create`. Its README has the commands.
3. **Point Hermes at it:**
   `hermes config set terminal.tinfoil_container <name>`, then
   `hermes config set terminal.backend tinfoil`.

Hermes' terminal, file and code tools then run inside that enclave. Each
connection is verified by `tinfoil ssh`, using your `TINFOIL_API_KEY` for
the enclave's tunnel. `hermes doctor` shows what is still missing.
