#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

LIVE_PORT="${PROXY_PORT:-8788}"

if ! command -v claude >/dev/null 2>&1; then
  echo "claude CLI not found" >&2
  exit 1
fi

set -a
source ./secret.env
set +a

pick_port() {
  python3 - <<'PY'
import socket
s = socket.socket()
s.bind(("127.0.0.1", 0))
print(s.getsockname()[1])
s.close()
PY
}

echo "real claude via live proxy:"
ANTHROPIC_BASE_URL="http://127.0.0.1:$LIVE_PORT" claude -p 'Reply exactly: PROXY_E2E_OK' | grep -F 'PROXY_E2E_OK'

MOCK_PORT="$(pick_port)"
TMP_PROXY_PORT="$(pick_port)"
MOCK_LOG="/tmp/ccfailover-mock-primary.log"
TMP_PROXY_LOG="/tmp/ccfailover-fallback-proxy.log"

MOCK_PORT="$MOCK_PORT" MOCK_STATUS=429 python3 mock_primary.py >"$MOCK_LOG" 2>&1 &
PRIMARY_PID=$!

PRIMARY_BASE="http://127.0.0.1:$MOCK_PORT" PROXY_PORT="$TMP_PROXY_PORT" COOLDOWN_SEC=5 python3 proxy.py >"$TMP_PROXY_LOG" 2>&1 &
PROXY_PID=$!

cleanup() {
  kill "$PROXY_PID" "$PRIMARY_PID" 2>/dev/null || true
}
trap cleanup EXIT

sleep 2

echo "real claude via forced fallback proxy:"
ANTHROPIC_BASE_URL="http://127.0.0.1:$TMP_PROXY_PORT" claude -p 'Reply exactly: PROXY_FALLBACK_OK' | grep -F 'PROXY_FALLBACK_OK'

grep -F 'PRIMARY 429 -> falling back to HUB' "$TMP_PROXY_LOG" >/dev/null
grep -F 'HUB 200' "$TMP_PROXY_LOG" >/dev/null

echo "ALL_CLAUDE_E2E_TESTS_PASSED"
