#!/usr/bin/env python3
"""Mock PRIMARY upstream. Returns MOCK_STATUS for /v1/messages.
200 -> a canned non-stream Anthropic message so we can verify pass-through.
429 -> simulates quota exhaustion so the proxy should fall back."""
import json, os, time, sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

STATUS = int(os.environ.get("MOCK_STATUS", "200"))
PORT = int(os.environ.get("MOCK_PORT", "8799"))

class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def log_message(self,*a): pass
    def do_POST(self):
        n=int(self.headers.get("Content-Length",0) or 0); self.rfile.read(n)
        if STATUS==429:
            b=json.dumps({"type":"error","error":{"type":"rate_limit_error","message":"quota exhausted (mock)"}}).encode()
            self.send_response(429)
        else:
            b=json.dumps({"id":"mock","type":"message","role":"assistant",
                "model":"mock-primary","content":[{"type":"text","text":"PRIMARY_OK"}],
                "stop_reason":"end_turn","usage":{"input_tokens":1,"output_tokens":2}}).encode()
            self.send_response(200)
        self.send_header("content-type","application/json")
        self.send_header("content-length",str(len(b))); self.end_headers(); self.wfile.write(b)
    do_GET=do_POST

print(f"mock primary on :{PORT} status={STATUS}",file=sys.stderr,flush=True)
ThreadingHTTPServer(("127.0.0.1",PORT),H).serve_forever()
