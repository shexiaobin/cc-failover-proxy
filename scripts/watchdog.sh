#!/usr/bin/env bash
# Watchdog for cc-failover-proxy. Detects a hung/dead proxy that launchd's
# KeepAlive can't catch (process alive but not answering /_health), recovers it,
# and notifies. Run every 60s (e.g. via launchd StartInterval).
#
# Acts only on state TRANSITIONS (healthy<->unhealthy) so it never spams. On a
# transition it:
#   1) writes  $ALERT_FILE   (a co-located agent/tool can surface this)
#   2) appends $LOG          (history)
#   3) runs    $NOTIFY_CMD "<message>"  if NOTIFY_CMD is set (your push hook)
set -u

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
[ -f "$DIR/secret.env" ] && { set -a; . "$DIR/secret.env"; set +a; }

PORT="${PROXY_PORT:-8788}"
HEALTH="http://127.0.0.1:${PORT}/_health"
SELF_MATCH="$DIR/proxy.py"
NOTIFY_CMD="${NOTIFY_CMD:-}"

STATE_FILE="${TMPDIR:-/tmp}/cc-proxy-wd-state"
FAILS_FILE="${TMPDIR:-/tmp}/cc-proxy-wd-fails"
LAST_PUSH_FILE="${TMPDIR:-/tmp}/cc-proxy-wd-lastpush"
ALERT_FILE="${TMPDIR:-/tmp}/cc-proxy-watchdog-alert.txt"
LOG="${TMPDIR:-/tmp}/cc-proxy-watchdog.log"

FAIL_THRESHOLD=2      # consecutive failed checks before declaring UNHEALTHY
ESCALATE_SEC=300      # while still unhealthy, re-notify at most this often

now=$(date +%s)
ts=$(date '+%Y-%m-%d %H:%M:%S')
prev=$(cat "$STATE_FILE" 2>/dev/null || echo INIT)

emit() {
    local msg="$1"
    printf '%s | %s\n' "$ts" "$msg" > "$ALERT_FILE"
    printf '%s | %s\n' "$ts" "$msg" >> "$LOG"
    # NOTIFY_CMD must be a path to an executable/script; it receives the message
    # as a single argument ($1). It is exec'd directly (no `sh -c`), so values
    # like `a; rm -rf` are passed as a literal program name, not run as shell.
    if [ -n "$NOTIFY_CMD" ]; then
        "$NOTIFY_CMD" "[cc-failover-proxy] $msg" >/dev/null 2>&1 || true
    fi
}

if curl -s --max-time 5 "$HEALTH" 2>/dev/null | grep -q '"ok"'; then
    echo 0 > "$FAILS_FILE"
    [ "$prev" = "UNHEALTHY" ] && emit "proxy recovered; health check passing again."
    echo HEALTHY > "$STATE_FILE"
    exit 0
fi

fails=$(( $(cat "$FAILS_FILE" 2>/dev/null || echo 0) + 1 ))
echo "$fails" > "$FAILS_FILE"
[ "$fails" -lt "$FAIL_THRESHOLD" ] && exit 0

pid=$(lsof -nP -tiTCP:"${PORT}" -sTCP:LISTEN 2>/dev/null | head -1)
if [ -n "$pid" ]; then
    cmd=$(ps -o command= -p "$pid" 2>/dev/null)
    if echo "$cmd" | grep -q "proxy.py"; then
        kill "$pid" 2>/dev/null && action="proxy was hung (listening but not answering); killed PID $pid so the supervisor restarts it."
    else
        action="port ${PORT} is held by another process (PID $pid: ${cmd:0:60}). Manual fix needed."
    fi
else
    action="proxy is not listening on ${PORT}; waiting for the supervisor to relaunch it."
fi

if [ "$prev" != "UNHEALTHY" ]; then
    emit "$action"; echo "$now" > "$LAST_PUSH_FILE"
else
    last=$(cat "$LAST_PUSH_FILE" 2>/dev/null || echo 0)
    if [ $(( now - last )) -ge "$ESCALATE_SEC" ]; then
        emit "still down (>$((ESCALATE_SEC/60))m). $action"; echo "$now" > "$LAST_PUSH_FILE"
    fi
fi
echo UNHEALTHY > "$STATE_FILE"
exit 0
