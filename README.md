# cc-failover-proxy

**Hit Claude Code's 5-hour usage limit? Keep working.**

Claude Code's subscription enforces a rolling 5-hour usage cap. Hit it, and you're normally stuck waiting for the quota to reset. This proxy sits in front of Claude Code and, the moment you're rate-limited, **transparently fails over to a backup API** so you keep working. Once your quota refreshes, it **switches back automatically**. No restart, no manual switching, no interruption.

```
Claude Code ──> cc-failover-proxy ──┬─ PRIMARY  (your subscription, pass-through)
 (ANTHROPIC_BASE_URL=                └─ FALLBACK (a backup API, on 429/529)
  http://127.0.0.1:8788)
```

## Why

Since `ANTHROPIC_BASE_URL` is read once at process startup, you can't hot-swap a running Claude Code session. This proxy moves the switch **out** of Claude Code: it always talks to a fixed local address (`127.0.0.1:8788`), and the upstream switch happens inside the proxy, live. You configure it once and never think about it again.

## Features

- **Zero-restart failover** on `429`/`529` from the primary, with a cooldown so
  it retries the primary later and **switches back automatically**.
- **Transparent pass-through** of the client's own auth header to the primary —
  the proxy stores no primary credentials.
- **Streaming (SSE) safe**, gzip handled, hop-by-hop headers filtered, chunked
  request bodies rejected cleanly, framed error responses (no client hangs).
- **Health endpoint** `GET /_health`.
- **Watchdog** that catches the case a process supervisor can't (process alive
  but not answering) and restarts the proxy, with a pluggable notification hook.

## Quick start

```bash
git clone <your-fork-url> cc-failover-proxy && cd cc-failover-proxy
cp secret.env.example secret.env      # then edit it (see Config)
./run.sh                              # foreground; Ctrl-C to stop
```

Point Claude Code at it (persistent, all sessions) via `~/.claude/settings.json`:

```json
{ "env": { "ANTHROPIC_BASE_URL": "http://127.0.0.1:8788" } }
```

> Do **not** also set `ANTHROPIC_AUTH_TOKEN` / `ANTHROPIC_API_KEY` — that forces
> API-key mode and bypasses the OAuth pass-through. Set only `ANTHROPIC_BASE_URL`.

Restart Claude Code once so it picks up the new base URL. After that, all
failover is restart-free.

## Config (`secret.env`)

| Key | Required | Default | Meaning |
|-----|----------|---------|---------|
| `HUB_BASE` | for fallback | — | Backup endpoint base (Anthropic-Messages compatible). Empty = primary-only. |
| `HUB_TOKEN` | for fallback | — | Auth token for the backup endpoint. |
| `HUB_MODEL` | optional | — | If set, rewrite the request `model` on the fallback leg. |
| `PRIMARY_BASE` | no | `https://api.anthropic.com` | Primary upstream. |
| `PROXY_PORT` | no | `8788` | Listen port (127.0.0.1 only). |
| `COOLDOWN_SEC` | no | `300` | After a primary 429, go straight to fallback for this long, then retry primary. |
| `NOTIFY_CMD` | no | — | Watchdog runs `NOTIFY_CMD "<message>"` on state change (your push hook). |

`secret.env` is gitignored. Never commit real tokens.

## Run as a service (macOS launchd)

```bash
./scripts/install.sh     # generates + loads launchd jobs (proxy + watchdog)
./scripts/status.sh
./scripts/uninstall.sh
```

`install.sh` generates the plists with absolute paths for wherever you cloned
the repo, so nothing is machine-specific. Override the label prefix with
`LABEL_PREFIX=com.you ./scripts/install.sh`.

Linux: run `./run.sh` under systemd/supervisord and `scripts/watchdog.sh` on a
60-second cron.

## Reliability notes

This proxy becomes a single point of failure in front of a path that was
previously direct. Mitigations:

- The supervisor (launchd `KeepAlive`) restarts it on crash.
- The **watchdog** restarts it on hang (alive but not answering) and notifies.
- **Escape hatch:** if the proxy is ever wedged and you're locked out, delete
  the `ANTHROPIC_BASE_URL` line from `~/.claude/settings.json` to revert Claude
  Code to talking to Anthropic directly.

## Tests

```bash
./scripts/test_proxy_protocol.py     # protocol/regression tests (no real model needed)
./scripts/test_claude_e2e.sh         # end-to-end with the real claude CLI
```

## ⚠️ Terms of service

Routing a subscription through a proxy, and especially rotating/pooling multiple
subscription accounts to dodge limits, may violate your provider's terms. A
single subscription with a **paid API** fallback is the most defensible setup.
You are responsible for how you configure and use this. No warranty.

## License

MIT — see [LICENSE](LICENSE).
