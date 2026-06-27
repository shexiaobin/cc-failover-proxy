#!/usr/bin/env bash
set -euo pipefail
PREFIX="${LABEL_PREFIX:-com.ccfailover}"
DOMAIN="gui/$(id -u)"
AGENTS="$HOME/Library/LaunchAgents"
for L in "$PREFIX.watchdog" "$PREFIX.proxy"; do
  launchctl bootout "$DOMAIN/$L" 2>/dev/null || true
  rm -f "$AGENTS/$L.plist"
  echo "removed $L"
done
