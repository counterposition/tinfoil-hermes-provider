#!/usr/bin/env bash
# Container side of the install E2E. Prints one PASS/FAIL/SKIP/OBSERVED line per check.
set -uo pipefail
PLUGIN_ID="${PLUGIN_ID:-counterposition/tinfoil-hermes-provider}"
export HERMES_HOME="${HERMES_HOME:-/e2e/hermes-home}"
mkdir -p "$HERMES_HOME"
# A real key is only for the live step; steps 1-4 must see a fresh install
# with no key, so the installer's prompt and the picker's key prompt are exercised.
LIVE_KEY="${TINFOIL_API_KEY:-}"
unset TINFOIL_API_KEY
CHECKS=/plugin/e2e/checks.py
fails=0
pass() { echo "PASS  $*"; }
fail() { echo "FAIL  $*"; fails=$((fails+1)); }
skip() { echo "SKIP  $*"; }
obs()  { echo "OBSERVED  $*"; }
section() { echo; echo "=== $* ==="; }

# Private-repo clone auth. The installer isolates git from global config and
# GIT_CONFIG_* overrides (hermes_cli/_subprocess_compat.py) but honors
# GIT_ASKPASS, so that is the only way a token can reach a non-interactive clone.
if [ -n "${GH_TOKEN:-}" ]; then
  cat > /e2e/askpass.sh <<'ASK'
#!/bin/sh
case "$1" in Username*) echo x-access-token ;; *) echo "$GH_TOKEN" ;; esac
ASK
  chmod +x /e2e/askpass.sh
  export GIT_ASKPASS=/e2e/askpass.sh
fi

# Make the host's proxy reachable at the plugin's default 127.0.0.1:3301.
# The proxy rejects any other Host header (DNS-rebinding guard), so a plain
# host alias would not do.
proxy_live=0
fwd_pid=""
start_forwarder() {
  python /plugin/e2e/forward.py 3301 "$PROXY_HOST" 3301 &
  fwd_pid=$!
  sleep 0.5
}
if [ -n "${PROXY_HOST:-}" ]; then
  start_forwarder
  if curl -sf -m 5 http://127.0.0.1:3301/v1/models >/dev/null; then
    proxy_live=1; obs "tinfoil-proxy reachable via ${PROXY_HOST}"
  else
    obs "tinfoil-proxy NOT reachable via ${PROXY_HOST}; live-proxy steps will be skipped"
  fi
fi

section "hermes version"
ver="$(hermes --version 2>&1)"; printf "%s\n" "$ver" | sed -n 1,2p; echo "upstream rev: $(cat /opt/hermes-agent/.upstream-rev)"

section "1. hermes plugins install ${PLUGIN_ID}"
install_out="$(hermes plugins install "$PLUGIN_ID" 2>&1 </dev/null)"; rc=$?
echo "$install_out"
[ $rc -eq 0 ] && pass "install exit code 0" || fail "install exit code $rc"
[ -f "$HERMES_HOME/plugins/tinfoil-provider/plugin.yaml" ] \
  && pass "clone landed at \$HERMES_HOME/plugins/tinfoil-provider/plugin.yaml" \
  || fail "no plugin.yaml at \$HERMES_HOME/plugins/tinfoil-provider/"
grep -q "Tinfoil provider installed" <<<"$install_out" \
  && pass "after-install.md was printed" || fail "after-install.md not printed"
grep -q "plugins enable tinfoil-provider" <<<"$install_out" \
  && obs "installer still prints the generic 'hermes plugins enable' hint" \
  || obs "installer did not print the 'hermes plugins enable' hint"
grep -q "TINFOIL_API_KEY" <<<"$install_out" && obs "installer mentioned TINFOIL_API_KEY (requires_env)"

section "2. hermes plugins list"
list_out="$(hermes plugins list --plain --no-bundled 2>&1)"; echo "$list_out"
grep -q "tinfoil-provider" <<<"$list_out" && pass "plugin listed" || fail "plugin not listed"

section "3. hermes model: picker contents"
python "$CHECKS" canonical && pass "CANONICAL_PROVIDERS contains slug 'tinfoil'" || fail "slug 'tinfoil' missing from picker list"
if [ $proxy_live -eq 1 ]; then
  python "$CHECKS" models && pass "model list = curated fallbacks first, then live catalog" || fail "model list merge"
  model="$(python "$CHECKS" default_model)" \
    && /opt/e2e-venv/bin/python /plugin/e2e/drive_model_picker.py "$model" \
    && python "$CHECKS" selected_model "$model" \
    && pass "interactive 'hermes model' selected Tinfoil, prompted for key and Base URL, listed live models" \
    || fail "interactive 'hermes model' drive (see transcript above)"
else
  skip "live model list and interactive picker (no proxy)"
fi

section "4. hermes doctor"
# The proxy's /v1/models needs no auth, so a placeholder key exercises the probe.
python - <<'PY'
from hermes_cli.config import save_env_value
save_env_value("TINFOIL_API_KEY", "e2e-placeholder-key")
PY
if [ $proxy_live -eq 1 ]; then
  doc_out="$(hermes doctor 2>&1)"; rc=$?
  grep -i "tinfoil" <<<"$doc_out" || true
  grep -iq "tinfoil" <<<"$doc_out" && obs "doctor mentions Tinfoil (exit $rc)" || obs "doctor does not mention Tinfoil at all (exit $rc)"
  grep -iE "tinfoil.*(ok|✓|reachable|healthy|[0-9]+ models)|✓.*tinfoil" <<<"$doc_out" >/dev/null \
    && pass "doctor probe passes with the proxy running" \
    || fail "doctor did not report a passing Tinfoil probe with the proxy running"
  kill "$fwd_pid" 2>/dev/null; wait "$fwd_pid" 2>/dev/null; sleep 0.3
  doc_down="$(hermes doctor 2>&1)"; rc2=$?
  grep -i "tinfoil" <<<"$doc_down" || true
  grep -iE "tinfoil.*(fail|✗|❌|unreachable|refused|error|could not|not running)|(⚠|✗|❌|fail).*tinfoil" <<<"$doc_down" >/dev/null \
    && pass "doctor reports Tinfoil unreachable when the proxy is down" \
    || fail "doctor did not flag Tinfoil with the proxy down (exit $rc2)"
  start_forwarder
else
  skip "doctor probe (no proxy)"
fi

section "7. live conversation checks"
if [ -n "$LIVE_KEY" ] && [ $proxy_live -eq 1 ]; then
  LIVE_KEY="$LIVE_KEY" python - <<'PY'
import os
from hermes_cli.config import save_env_value
save_env_value("TINFOIL_API_KEY", os.environ["LIVE_KEY"])
PY
  python /plugin/e2e/live_checks.py && pass "live checks" || fail "live checks (see above)"
else
  skip "live conversation checks (need TINFOIL_API_KEY and a reachable proxy)"
fi

section "5. hermes plugins remove tinfoil-provider"
rm_out="$(hermes plugins remove tinfoil-provider 2>&1 </dev/null)"; rc=$?; echo "$rm_out"
[ $rc -eq 0 ] && pass "remove exit code 0" || fail "remove exit code $rc"
[ ! -e "$HERMES_HOME/plugins/tinfoil-provider" ] && pass "plugin directory gone" || fail "plugin directory still present"
python "$CHECKS" absent && pass "Tinfoil gone from registry and picker in a fresh process" || fail "Tinfoil still discoverable after remove"

section "6. precedence: installed plugin beats a bundled copy"
if [ -d /bundled-tinfoil ]; then
  hermes plugins install "$PLUGIN_ID" >/dev/null 2>&1 </dev/null || fail "reinstall for the precedence check"
  mkdir -p /opt/hermes-agent/plugins/model-providers/tinfoil
  cp /bundled-tinfoil/__init__.py /bundled-tinfoil/plugin.yaml /opt/hermes-agent/plugins/model-providers/tinfoil/
  sed -i 's/description="Tinfoil — confidential inference in verified enclaves (local proxy)"/description="Tinfoil (E2E-EDITED installed copy)"/' \
    "$HERMES_HOME/plugins/tinfoil-provider/__init__.py"
  python "$CHECKS" description "Tinfoil (E2E-EDITED installed copy)" \
    && pass "installed copy's description wins over the bundled copy" \
    || fail "bundled copy won (or edit not visible)"
else
  skip "precedence (set FORK_TINFOIL_DIR to a bundled tinfoil/ directory)"
fi


echo
[ $fails -eq 0 ] && echo "E2E: all checks passed" || echo "E2E: $fails check(s) failed"
exit $fails
