#!/usr/bin/env python3
"""
HOOK SERVER - Listens on port 9090, triggers rounds on POST /hook
"""
from http.server import HTTPServer, BaseHTTPRequestHandler
import json, subprocess, os

class HookHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path == "/hook":
            length = int(self.headers.get('Content-Length', 0))
            data = self.rfile.read(length)
            try:
                payload = json.loads(data)
                # Trigger a round with the payload as argument
                subprocess.Popen(["~/bin/round.sh", payload.get("round_id", "webhook")], shell=True)
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"status":"round_triggered"}')
            except Exception as e:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode())
        else:
            self.send_response(404)
            self.end_headers()
    def do_GET(self):
        if self.path == "/health":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"status":"hook_alive"}')
        else:
            self.send_response(404)

if __name__ == "__main__":
    port = 9090
    print(f"Hook server running on port {port}")
    HTTPServer(("0.0.0.0", port), HookHandler).serve_forever()
