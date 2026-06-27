#!/usr/bin/env bash
# Install cc-failover-proxy + watchdog as macOS LaunchAgents.
# Plists are GENERATED here with the correct absolute paths for wherever you
# cloned the repo, so nothing is hardcoded to a particular machine/user.
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

gen_plist() {  # label  program-args-xml  extra-keys-xml
  local label="$1" args="$2" extra="$3"
  cat > "$AGENTS/$label.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$label</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/zsh</string><string>-lc</string>
    <string>$args</string>
  </array>
  <key>WorkingDirectory</key><string>$ROOT</string>
$extra
  <key>StandardOutPath</key><string>${TMPDIR:-/tmp}/$label.out.log</string>
  <key>StandardErrorPath</key><string>${TMPDIR:-/tmp}/$label.err.log</string>
</dict>
</plist>
PLIST
}

# Proxy: start at load, keep alive on crash.
gen_plist "$PROXY_LABEL" "cd '$ROOT' && exec ./run.sh" \
  "  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>"

# Watchdog: run every 60s.
gen_plist "$WD_LABEL" "exec '$ROOT/scripts/watchdog.sh'" \
  "  <key>RunAtLoad</key><true/>
  <key>StartInterval</key><integer>60</integer>"

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
