# Tinfoil provider installed

Hermes discovers this plugin automatically. **No `hermes plugins enable` step
is needed**: model-provider plugins load through the provider registry, not
the plugin manager, so ignore the "installed but not enabled" hint below if
it appears.

1. **Run the local verifier proxy.** Install `tinfoil-proxy`
   (https://docs.tinfoil.sh/) and keep it running on `127.0.0.1:3301`.
   macOS: copy `scripts/sh.tinfoil.proxy.plist` from this plugin directory to
   `~/Library/LaunchAgents/`, adjust the binary path if it is not
   `~/go/bin/tinfoil-proxy`, then
   `launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/sh.tinfoil.proxy.plist`.
   The Tinfoil menu-bar Proxy app serves the same endpoint and works too.
   Every machine that runs Hermes needs its own proxy; it binds loopback only.
2. **Configure and pick a model:** run `hermes model`, choose **Tinfoil**,
   enter `TINFOIL_API_KEY` when prompted, accept the local Base URL, and
   select a chat model such as `kimi-k3`, `glm-5-3`, or `gpt-oss-120b`.
   The picker saves your settings. Its unfiltered catalog also contains
   embeddings and audio models; these are not agent chat models.
3. **Verify:** `hermes doctor` checks whether the proxy returns a model list.
   This does not prove your API key works: the list is public. Check whether
   the proxy has finished checking the enclave with:
   `curl --fail --silent --show-error --max-time 10 http://127.0.0.1:3301/verification-document`.
   An open port alone does not prove those checks have finished.
