# Tinfoil provider installed

If you declined the "Enable now?" question, run
`hermes plugins enable tinfoil-provider` first: Hermes lists Tinfoil only
while the plugin is enabled.

1. **Install the local verifier proxy once.** Install `tinfoil-proxy`
   (https://docs.tinfoil.sh/local-proxy/cli). Hermes starts it whenever it
   needs Tinfoil and nothing is listening on `127.0.0.1:3301`, and leaves it
   running for later sessions. The Tinfoil menu-bar Proxy app serves the same
   endpoint. Set `TINFOIL_PROXY_AUTOSTART=0` to run the proxy yourself.
2. **Pick a model:** run `hermes model`, choose **Tinfoil**, paste your
   `TINFOIL_API_KEY`, accept the local Base URL, and select a model such as
   `kimi-k3-1-pro`.
3. **Check the proxy's attestation** (optional):
   `curl --fail --silent --show-error --max-time 10 http://127.0.0.1:3301/verification-document`.

To run the agent's terminal, file and code tools in an enclave too, add the
companion backend:
`hermes plugins install counterposition/tinfoil-hermes-provider/sandbox`.
