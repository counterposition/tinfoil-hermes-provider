# Tinfoil Container backend installed

1. **Enable it:** `hermes plugins enable tinfoil-sandbox`.
2. **Install the Tinfoil CLI** (https://docs.tinfoil.sh/containers/cli) and
   run `tinfoil login` once so it can look your container up by name.
3. **Name the container:** `hermes config set terminal.tinfoil_container <name>`.
   The container must serve SSH and authorize your key
   (https://github.com/tinfoilsh/confidential-ubuntu is a ready workspace).
4. **Select the backend:** `hermes config set terminal.backend tinfoil`.

Hermes' terminal, file and code tools then run inside that enclave. Each
connection is verified by `tinfoil ssh`; `TINFOIL_API_KEY` is passed to the
enclave's tunnel. `hermes doctor` shows what is still missing.
