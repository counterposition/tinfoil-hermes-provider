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
2. **Set your API key** if the installer did not prompt for it:
   `hermes config` (or `hermes setup`) and fill in `TINFOIL_API_KEY`.
3. **Pick a model:** run `hermes model`, choose **Tinfoil**, choose a model.
   Do this rather than editing `model.provider` by hand: a plugin provider
   has no silent default model in upstream Hermes yet.
4. **Verify:** `hermes doctor` probes the proxy's `/v1/models` (a failing
   probe means the proxy is not running), and
   `curl -s http://127.0.0.1:3301/verification-document` proves the
   enclave attestation succeeded. A bound port alone does not.
