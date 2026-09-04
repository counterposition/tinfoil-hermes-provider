#!/usr/bin/env bash
# Run the install E2E in an isolated container (Docker or OrbStack).
#
#   e2e/run.sh [hermes-agent checkout]      default: $HERMES_AGENT_SRC
#
# Environment:
#   PLUGIN_ID         what to `hermes plugins install` (default: counterposition/tinfoil-hermes-provider)
#   GH_TOKEN          GitHub token so the container can clone a private repo (optional)
#   PROXY_HOST        host that runs tinfoil-proxy, as seen from the container
#                     (default: host.docker.internal; set empty to skip live-proxy steps)
#   TINFOIL_API_KEY   real key; enables the live conversation step (optional)
#   FORK_TINFOIL_DIR  a bundled plugins/model-providers/tinfoil/ copy for the precedence step (optional)
#
# The container never sees your real $HERMES_HOME.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(dirname "$here")"
src="${1:-${HERMES_AGENT_SRC:-}}"
[ -n "$src" ] && [ -d "$src" ] || { echo "usage: $0 <hermes-agent checkout> (or set HERMES_AGENT_SRC)" >&2; exit 2; }

ctx="$(mktemp -d)"
trap 'rm -rf "$ctx"' EXIT
git -C "$src" archive --format=tar HEAD | tar -x -C "$ctx"
git -C "$src" rev-parse HEAD > "$ctx/.upstream-rev"
echo "hermes-agent source: $src @ $(cat "$ctx/.upstream-rev")"

docker build -q -f "$here/Dockerfile" --build-context "hermes=$ctx" -t tinfoil-hermes-e2e "$ctx"

args=(--rm -e PLUGIN_ID -e GH_TOKEN -e PROXY_HOST -e TINFOIL_API_KEY
      -v "$repo:/plugin:ro")
if [ -n "${FORK_TINFOIL_DIR:-}" ]; then
  args+=(-v "$FORK_TINFOIL_DIR:/bundled-tinfoil:ro")
fi
export PLUGIN_ID="${PLUGIN_ID:-counterposition/tinfoil-hermes-provider}"
export PROXY_HOST="${PROXY_HOST-host.docker.internal}"
exec docker run "${args[@]}" tinfoil-hermes-e2e bash /plugin/e2e/inside.sh
