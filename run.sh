#!/usr/bin/env bash
# Start cc-failover-proxy. All deployment-specific config comes from ./secret.env
# (gitignored) or the environment — nothing private is hardcoded here.
set -euo pipefail
cd "$(dirname "$0")"

if [ -f ./secret.env ]; then
  set -a; source ./secret.env; set +a
fi

export PRIMARY_BASE="${PRIMARY_BASE:-https://api.anthropic.com}"
export HUB_BASE="${HUB_BASE:-}"
export HUB_TOKEN="${HUB_TOKEN:-}"
export HUB_MODEL="${HUB_MODEL:-}"
export PROXY_PORT="${PROXY_PORT:-8788}"
export COOLDOWN_SEC="${COOLDOWN_SEC:-300}"

if [ -z "$HUB_BASE" ] || [ -z "$HUB_TOKEN" ]; then
  echo "warning: HUB_BASE/HUB_TOKEN not set — fallback disabled (primary-only)." >&2
fi

# Free the port from a stale instance of THIS proxy (match by port, not a fuzzy
# pkill that could hit unrelated *proxy.py processes).
OLD=$(lsof -nP -tiTCP:"${PROXY_PORT}" -sTCP:LISTEN 2>/dev/null || true)
if [ -n "$OLD" ]; then
  echo "killing old listener PID(s): $OLD" >&2
  kill $OLD 2>/dev/null || true
  for _ in 1 2 3 4 5; do
    sleep 1
    lsof -nP -tiTCP:"${PROXY_PORT}" -sTCP:LISTEN >/dev/null 2>&1 || break
  done
fi

exec python3 proxy.py
