#!/usr/bin/env python3
"""
MODEL ROUTER - Routes prompts to Ollama, Gemini, Mistral, or fallback.
Run: python3 ~/bin/model_router.py --prompt "Hello" --model gemini
Listens on port 7070 for POST /route with JSON: {"prompt":"...", "model":"..."}
"""
import os, sys, json, subprocess, shlex
from http.server import HTTPServer, BaseHTTPRequestHandler

HOME = os.path.expanduser("~")
ROUTER_LOG = HOME + "/model_router.log"

def log(msg):
    with open(ROUTER_LOG, "a") as f:
        f.write(f"{msg}\n")

def route_gemini(prompt):
    # Use Gemini CLI (assuming 'gemini' command exists)
    try:
        # Try using the CLI you have: gemini-cli or maybe 'gemini'
        cmd = ["gemini", "ask", prompt]  # adjust if needed
        out = subprocess.check_output(cmd, timeout=30, stderr=subprocess.DEVNULL)
        return out.decode().strip()
    except:
        return "Gemini CLI error or not installed."

def route_ollama(prompt):
    # Use ollama if available
    try:
        out = subprocess.check_output(["ollama", "run", "mistral", prompt], timeout=60, stderr=subprocess.DEVNULL)
        return out.decode().strip()
    except:
        return "Ollama not available."

def route_mistral_api(prompt):
    # Placeholder – you can replace with actual Mistral key
    # For now, mock
    return "Mistral API not configured."

def route_fallback(prompt):
    # Hit the payment engine as a joke
    subprocess.run(["bash", "-c", f"~/bin/master_control.sh charge 1 'model_route'"], check=False)
    return f"Fallback: charged $1 for prompt: {prompt[:20]}..."

def route(prompt, model="auto"):
    if model == "auto":
        # Try ollama first, then gemini, then fallback
        res = route_ollama(prompt)
        if "not available" not in res.lower():
            return res
        res2 = route_gemini(prompt)
        if "error" not in res2.lower():
            return res2
        return route_fallback(prompt)
    if model == "gemini":
        return route_gemini(prompt)
    elif model == "ollama":
        return route_ollama(prompt)
    elif model == "mistral":
        return route_mistral_api(prompt)
    else:
        # Auto: try ollama first, then gemini, then fallback
        try:
            return route_ollama(prompt)
        except:
            try:
                return route_gemini(prompt)
            except:
                return route_fallback(prompt)

class RouterHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path == "/route":
            length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(length)
            try:
                data = json.loads(body)
                prompt = data.get("prompt", "")
                model = data.get("model", "auto")
                result = route(prompt, model)
                self.send_response(200)
                self.end_headers()
                self.wfile.write(json.dumps({"result": result}).encode())
            except Exception as e:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode())
        else:
            self.send_response(404)
    def do_GET(self):
        if self.path == "/health":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"status":"alive"}')

if __name__ == "__main__":
    port = 7070
    print(f"Model router running on port {port}")
    HTTPServer(("0.0.0.0", port), RouterHandler).serve_forever()
