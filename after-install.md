# Tinfoil provider installed

Hermes discovers this plugin automatically. **No `hermes plugins enable` step
is needed**: model-provider plugins load through the provider registry, not
the plugin manager, so ignore the "installed but not enabled" hint below if
it appears.

1. **Install the local verifier proxy once.** Install `tinfoil-proxy`
   (https://docs.tinfoil.sh/local-proxy/cli) on this machine. Hermes starts it
   when it needs Tinfoil and nothing is listening on `127.0.0.1:3301`, and
   leaves it running for later sessions. The Tinfoil menu-bar Proxy app serves
   the same endpoint and works too. Set `TINFOIL_PROXY_AUTOSTART=0` if you
   would rather run the proxy yourself.
2. **Configure and pick a model:** run `hermes model`, choose **Tinfoil**,
   enter `TINFOIL_API_KEY` when prompted, accept the local Base URL, and
   select a model such as `kimi-k3-1-pro`, `glm-5-3`, or `gpt-oss-120b`.
   The picker saves your settings.
3. **Verify:** check that the proxy has finished checking the enclave with
   `curl --fail --silent --show-error --max-time 10 http://127.0.0.1:3301/verification-document`.
   An open port alone does not prove those checks have finished, and
   `hermes doctor` only shows whether the proxy answers.

To run the agent's commands in an enclave as well, install the companion
terminal backend:
`hermes plugins install counterposition/tinfoil-hermes-provider/sandbox`.
