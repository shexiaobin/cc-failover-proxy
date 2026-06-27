#!/usr/bin/env bash
# Install cc-failover-proxy + watchdog as macOS LaunchAgents.
# Plists are GENERATED here (via Python plistlib, so paths with spaces/&/< are
# handled correctly and there is no shell-string injection) with absolute paths
# for wherever you cloned the repo. Nothing is hardcoded to a machine/user.
#
# Linux users: this script is macOS (launchd) only. Run `./run.sh` under your
# own supervisor (systemd unit, supervisord, etc.) and a 60s cron for watchdog.sh.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PREFIX="${LABEL_PREFIX:-com.ccfailover}"
PROXY_LABEL="$PREFIX.proxy"
WD_LABEL="$PREFIX.watchdog"
DOMAIN="gui/$(id -u)"
AGENTS="$HOME/Library/LaunchAgents"
PORT="${PROXY_PORT:-8788}"
mkdir -p "$AGENTS"

# gen_plist <label> <script-to-exec> <proxy|watchdog>
# Values are passed through the environment (never interpolated into code or
# XML), and plistlib does all XML escaping — so any path is safe.
gen_plist() {
  PL_LABEL="$1" PL_SCRIPT="$2" PL_MODE="$3" PL_ROOT="$ROOT" \
  PL_OUT="${TMPDIR:-/tmp}/$1.out.log" PL_ERR="${TMPDIR:-/tmp}/$1.err.log" \
  PL_DST="$AGENTS/$1.plist" python3 - <<'PY'
import os, plistlib
# zsh -lc 'exec "$1"' <label> <script>: runs the script with a login-shell PATH
# while passing its path as a separate argv element (no shell interpolation).
d = {
    "Label": os.environ["PL_LABEL"],
    "ProgramArguments": ["/bin/zsh", "-lc", 'exec "$1"',
                         os.environ["PL_LABEL"], os.environ["PL_SCRIPT"]],
    "WorkingDirectory": os.environ["PL_ROOT"],
    "StandardOutPath": os.environ["PL_OUT"],
    "StandardErrorPath": os.environ["PL_ERR"],
    "RunAtLoad": True,
}
if os.environ["PL_MODE"] == "proxy":
    d["KeepAlive"] = True
else:
    d["StartInterval"] = 60
with open(os.environ["PL_DST"], "wb") as f:
    plistlib.dump(d, f)
PY
}

gen_plist "$PROXY_LABEL" "$ROOT/run.sh"            proxy
gen_plist "$WD_LABEL"    "$ROOT/scripts/watchdog.sh" watchdog

for L in "$PROXY_LABEL" "$WD_LABEL"; do
  launchctl bootout "$DOMAIN/$L" 2>/dev/null || true
  launchctl bootstrap "$DOMAIN" "$AGENTS/$L.plist"
done
launchctl kickstart -k "$DOMAIN/$PROXY_LABEL" 2>/dev/null || true

sleep 2
echo "installed: $PROXY_LABEL, $WD_LABEL"
launchctl list | grep "$PREFIX" || true
echo; echo "health:"; curl -fsS "http://127.0.0.1:$PORT/_health" || echo "(not healthy yet)"
echo
