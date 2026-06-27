#!/usr/bin/env bash
set -euo pipefail
PREFIX="${LABEL_PREFIX:-com.ccfailover}"
PORT="${PROXY_PORT:-8788}"
echo "launchd jobs:"; launchctl list | grep "$PREFIX" || echo "  (none)"
echo; echo "listener:"; lsof -nP -iTCP:"$PORT" -sTCP:LISTEN 2>/dev/null || echo "  (nothing on $PORT)"
echo; echo "health:"; curl -fsS "http://127.0.0.1:$PORT/_health" 2>/dev/null || echo "  (unhealthy)"
echo; echo; echo "watchdog log (tail):"; tail -10 "${TMPDIR:-/tmp}/cc-proxy-watchdog.log" 2>/dev/null || echo "  (no events)"
