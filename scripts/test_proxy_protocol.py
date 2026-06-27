#!/usr/bin/env python3
import argparse
import http.client
import json
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import proxy  # noqa: E402

captures = {}


class EchoPrimary(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length", "0") or 0)
        captures["primary_body"] = self.rfile.read(n)
        captures["primary_headers"] = {k.lower(): v for k, v in self.headers.items()}
        body = json.dumps({"ok": True, "headers": captures["primary_headers"]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class RateLimitPrimary(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length", "0") or 0)
        self.rfile.read(n)
        body = b'{"error":"quota"}'
        self.send_response(429)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class CaptureHub(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length", "0") or 0)
        captures["hub_body"] = self.rfile.read(n)
        captures["hub_headers"] = {k.lower(): v for k, v in self.headers.items()}
        body = b'{"hub":true}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class BadResponsePrimary(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length", "0") or 0)
        self.rfile.read(n)
        body = b'{"bad_headers":true}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "Keep-Alive, X-Hop-Resp")
        self.send_header("Keep-Alive", "timeout=5")
        self.send_header("X-Hop-Resp", "must-not-leak")
        self.send_header("Upgrade", "h2c")
        self.end_headers()
        self.wfile.write(body)


def serve(handler):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def serve_proxy(primary, hub=None):
    proxy.PRIMARY_BASE = f"http://127.0.0.1:{primary.server_port}"
    proxy.HUB_BASE = f"http://127.0.0.1:{hub.server_port}" if hub else "http://127.0.0.1:1"
    proxy.HUB_TOKEN = "hub-token"
    proxy.HUB_MODEL = "hub-model"
    proxy._state["primary_down_until"] = 0.0
    srv = ThreadingHTTPServer(("127.0.0.1", 0), proxy.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def post(port, body=b'{"model":"orig"}', headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    req_headers = {"Content-Type": "application/json"}
    if headers:
        req_headers.update(headers)
    conn.request("POST", "/v1/messages", body=body, headers=req_headers)
    resp = conn.getresponse()
    data = resp.read()
    out = (resp.status, {k.lower(): v for k, v in resp.getheaders()}, data)
    conn.close()
    return out


def raw(port, request_bytes):
    s = socket.create_connection(("127.0.0.1", port), timeout=5)
    s.sendall(request_bytes)
    s.settimeout(5)
    data = s.recv(4096).decode("iso-8859-1", "replace")
    s.close()
    return data


def assert_framed(resp, status):
    assert f" {status} " in resp, resp
    assert "Content-Length:" in resp, resp
    assert "Connection: close" in resp, resp


def test_protocol():
    echo = serve(EchoPrimary)
    p = serve_proxy(echo)
    status, _, _ = post(
        p.server_port,
        headers={
            "Connection": "X-Test-Hop, keep-alive",
            "X-Test-Hop": "leak",
            "Upgrade": "websocket",
            "TE": "trailers",
            "X-Keep": "yes",
        },
    )
    assert status == 200
    ph = captures["primary_headers"]
    assert "x-test-hop" not in ph and "upgrade" not in ph and "te" not in ph, ph
    assert ph.get("x-keep") == "yes" and ph.get("accept-encoding") == "identity", ph
    print("PASS request hop-by-hop filtering")

    primary429 = serve(RateLimitPrimary)
    hub = serve(CaptureHub)
    p = serve_proxy(primary429, hub)
    status, _, _ = post(p.server_port, headers={"Authorization": "Bearer primary", "X-Api-Key": "primary-key"})
    assert status == 200
    assert json.loads(captures["hub_body"])["model"] == "hub-model"
    assert captures["hub_headers"].get("authorization") == "Bearer hub-token"
    assert captures["hub_headers"].get("x-api-key") == "hub-token"
    print("PASS fallback rewrite/auth")

    p = serve_proxy(echo)
    resp = raw(
        p.server_port,
        b"POST /v1/messages HTTP/1.1\r\nHost: x\r\nTransfer-Encoding: gzip, Chunked\r\n"
        b"Content-Type: application/json\r\n\r\n10\r\n{\"model\":\"orig\"}\r\n0\r\n\r\n",
    )
    assert_framed(resp, 411)
    print("PASS chunked rejection framing")

    bad = serve(BadResponsePrimary)
    p = serve_proxy(bad)
    status, headers, _ = post(p.server_port)
    assert status == 200
    leaks = {k: headers.get(k) for k in ["keep-alive", "x-hop-resp", "upgrade"] if k in headers}
    assert not leaks, leaks
    assert headers.get("transfer-encoding") == "chunked", headers
    print("PASS response hop-by-hop filtering")

    p = serve_proxy(echo)
    for label, req in [
        ("invalid_cl", b"POST /v1/messages HTTP/1.1\r\nHost:x\r\nContent-Length: nope\r\n\r\n"),
        ("negative_cl", b"POST /v1/messages HTTP/1.1\r\nHost:x\r\nContent-Length: -1\r\n\r\n"),
    ]:
        assert_framed(raw(p.server_port, req), 400)
        print(f"PASS {label} rejection")


def test_live(port):
    for label, req, status in [
        (
            "chunked_list",
            b"POST /v1/messages HTTP/1.1\r\nHost: 127.0.0.1\r\nTransfer-Encoding: gzip, Chunked\r\n"
            b"Content-Type: application/json\r\n\r\n10\r\n{\"model\":\"orig\"}\r\n0\r\n\r\n",
            411,
        ),
        ("invalid_cl", b"POST /v1/messages HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Length: nope\r\n\r\n", 400),
        ("negative_cl", b"POST /v1/messages HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Length: -1\r\n\r\n", 400),
    ]:
        assert_framed(raw(port, req), status)
        print(f"PASS live {label}")

    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request("GET", "/_health")
    resp = conn.getresponse()
    data = resp.read()
    conn.close()
    assert resp.status == 200, data
    assert json.loads(data)["ok"] is True
    print("PASS live health")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--live-port", type=int)
    args = parser.parse_args()

    test_protocol()
    if args.live_port:
        test_live(args.live_port)
    print("ALL_PROXY_PROTOCOL_TESTS_PASSED")


if __name__ == "__main__":
    main()
