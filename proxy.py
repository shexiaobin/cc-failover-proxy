#!/usr/bin/env python3
"""
cc-failover-proxy: a thin local proxy for Claude Code.

Claude Code points ANTHROPIC_BASE_URL at this proxy (once). The proxy forwards
to PRIMARY (the OAuth subscription via api.anthropic.com, using whatever
Authorization header Claude Code already sends). When PRIMARY returns 429
(quota/rate-limit exhausted), the proxy transparently falls back to the HUB
(a third-party Anthropic-compatible API) using HUB_TOKEN, rewriting the model
name to HUB_MODEL. After a 429, PRIMARY is put on cooldown so subsequent
requests go straight to the HUB until the window resets.

No restart of Claude Code is needed to switch upstreams: the switch happens
inside this proxy, live.
"""
import json
import os
import sys
import time
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import requests

# ---- config (env-overridable) ----
PORT          = int(os.environ.get("PROXY_PORT", "8788"))
PRIMARY_BASE  = os.environ.get("PRIMARY_BASE", "https://api.anthropic.com").rstrip("/")
HUB_BASE      = os.environ.get("HUB_BASE", "").rstrip("/")   # set in secret.env to enable fallback
HUB_TOKEN     = os.environ.get("HUB_TOKEN", "")
HUB_MODEL     = os.environ.get("HUB_MODEL", "")   # if set, rewrite request model on the fallback leg
COOLDOWN_SEC  = int(os.environ.get("COOLDOWN_SEC", "300"))   # how long to skip PRIMARY after a 429
FALLBACK_CODES = {429, 529}                                  # primary "exhausted/overloaded"

# hop-by-hop / headers we must not forward verbatim
_DROP = {"host", "content-length", "connection", "accept-encoding", "transfer-encoding",
         "expect", "proxy-connection", "keep-alive", "te", "trailer", "upgrade"}

_state = {"primary_down_until": 0.0, "last_upstream": "PRIMARY"}
_lock = threading.Lock()
_SWITCH_FILE = "/tmp/cc-proxy-switched.txt"


def _header_tokens(value):
    return {t.strip().lower() for t in (value or "").split(",") if t.strip()}


def _log(*a):
    print(f"[{time.strftime('%H:%M:%S')}]", *a, file=sys.stderr, flush=True)


def _primary_in_cooldown():
    with _lock:
        return time.time() < _state["primary_down_until"]


def _notify_switch(to):
    """Write a one-line file on first switch so the agent can detect and announce.
    File I/O is inside the lock to prevent concurrent overwrites from producing
    stale content (Codex review round 2, P2 fix)."""
    with _lock:
        prev = _state["last_upstream"]
        if prev == to:
            return
        _state["last_upstream"] = to
        msg = f"{prev} -> {to}"
        try:
            with open(_SWITCH_FILE, "w") as f:
                f.write(msg)
        except OSError:
            pass
    _log(f"SWITCH: {msg} (wrote {_SWITCH_FILE})")


def _trip_primary():
    with _lock:
        _state["primary_down_until"] = time.time() + COOLDOWN_SEC
    _notify_switch("HUB")
    _log(f"PRIMARY tripped -> routing to HUB for {COOLDOWN_SEC}s")


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    timeout = 120  # don't let a half-open client hang a worker thread forever

    def log_message(self, *a):  # silence default logging
        pass

    def _client_headers(self):
        # dynamic hop-by-hop headers named in Connection must also be dropped
        dyn = _header_tokens(self.headers.get("Connection"))
        h = {}
        for k, v in self.headers.items():
            if k.lower() in _DROP or k.lower() in dyn:
                continue
            h[k] = v
        # force uncompressed upstream so we can stream raw bytes through safely
        h["Accept-Encoding"] = "identity"
        return h

    def _json_response(self, status, obj):
        """Well-formed JSON response: framed + closes the connection so the
        client never hangs waiting for a body under HTTP/1.1 keep-alive."""
        payload = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Connection", "close")
        self.end_headers()
        try:
            self.wfile.write(payload)
        except (BrokenPipeError, ConnectionResetError):
            pass
        self.close_connection = True

    def _error(self, status, obj):
        return self._json_response(status, obj)

    def _read_body(self):
        raw = self.headers.get("Content-Length", 0) or 0
        try:
            n = int(raw)
        except ValueError:
            raise ValueError("invalid Content-Length")
        if n < 0:
            raise ValueError("invalid Content-Length")
        return self.rfile.read(n) if n else b""

    def _send_upstream(self, base, headers, body, label):
        url = base + self.path
        return requests.request(
            self.command, url, headers=headers, data=body,
            stream=True, timeout=600,
        )

    def _relay_response(self, up):
        self.send_response(up.status_code)
        dyn = _header_tokens(up.headers.get("Connection"))
        for k, v in up.headers.items():
            # drop hop-by-hop + length/encoding (upstream is identity now; we re-chunk)
            if k.lower() in _DROP or k.lower() in dyn or k.lower() == "content-encoding":
                continue
            self.send_header(k, v)
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        try:
            for chunk in up.iter_content(chunk_size=8192):
                if not chunk:
                    continue
                self.wfile.write(b"%X\r\n%s\r\n" % (len(chunk), chunk))
                self.wfile.flush()
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            _log("client disconnected mid-stream")
        finally:
            up.close()

    def _to_hub_body(self, body):
        """Rewrite model -> HUB_MODEL for the fallback leg."""
        if not body:
            return body
        try:
            obj = json.loads(body)
            if HUB_MODEL and isinstance(obj, dict) and "model" in obj:
                obj["model"] = HUB_MODEL
                return json.dumps(obj, separators=(",", ":")).encode()
        except Exception:
            pass
        return body

    def _hub_headers(self, base_headers):
        h = dict(base_headers)
        # override auth to the hub token; support both header styles
        for k in list(h.keys()):
            if k.lower() in ("authorization", "x-api-key"):
                del h[k]
        h["Authorization"] = f"Bearer {HUB_TOKEN}"
        h["x-api-key"] = HUB_TOKEN
        h.setdefault("anthropic-version", "2023-06-01")
        h["Content-Type"] = "application/json"  # body was re-encoded to JSON
        return h

    def _do(self):
        if self.command == "GET" and self.path.split("?", 1)[0] == "/_health":
            return self._json_response(200, {
                "ok": True,
                "primary_in_cooldown": _primary_in_cooldown(),
                "cooldown_sec": COOLDOWN_SEC,
            })

        # we buffer the whole body (for failover retry); chunked request bodies
        # aren't decoded here, so reject them rather than forward an empty body
        # and corrupt the keep-alive stream.
        if "chunked" in _header_tokens(self.headers.get("Transfer-Encoding")):
            return self._error(411, {"error": "chunked request body not supported by proxy"})
        try:
            body = self._read_body()
        except ValueError as e:
            return self._error(400, {"error": str(e)})
        cli_headers = self._client_headers()

        # 1) try PRIMARY unless it's in cooldown
        if not _primary_in_cooldown():
            try:
                up = self._send_upstream(PRIMARY_BASE, cli_headers, body, "PRIMARY")
                if up.status_code in FALLBACK_CODES:
                    _log(f"PRIMARY {up.status_code} -> falling back to HUB")
                    up.close()
                    _trip_primary()
                else:
                    # only notify PRIMARY if cooldown wasn't tripped by another thread
                    if not _primary_in_cooldown():
                        _notify_switch("PRIMARY")
                    _log(f"PRIMARY {up.status_code} (ok) {self.path}")
                    return self._relay_response(up)
            except requests.RequestException as e:
                _log(f"PRIMARY error: {e} -> falling back to HUB")
                _trip_primary()
        else:
            _notify_switch("HUB")
            _log(f"PRIMARY in cooldown -> HUB {self.path}")

        # 2) HUB fallback
        hub_body = self._to_hub_body(body)
        hub_headers = self._hub_headers(cli_headers)
        try:
            up = self._send_upstream_to(HUB_BASE, hub_headers, hub_body)
            _log(f"HUB {up.status_code} {self.path}")
            return self._relay_response(up)
        except requests.RequestException as e:
            _log(f"HUB error: {e}")
            return self._error(502, {"error": f"both upstreams failed: {e}"})

    def _send_upstream_to(self, base, headers, body):
        url = base + self.path
        return requests.request(self.command, url, headers=headers, data=body,
                                stream=True, timeout=600)

    do_POST = _do
    do_GET = _do


def main():
    if not HUB_TOKEN:
        _log("WARNING: HUB_TOKEN not set; fallback will fail")
    _log(f"listening on :{PORT}  PRIMARY={PRIMARY_BASE}  HUB={HUB_BASE}  model->{HUB_MODEL}  cooldown={COOLDOWN_SEC}s")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
