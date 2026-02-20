#!/usr/bin/env python3
import json
import os
import subprocess
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HOST = os.getenv("DEPLOY_API_HOST", "127.0.0.1")
PORT = int(os.getenv("DEPLOY_API_PORT", "18777"))
TOKEN = os.getenv("DEPLOY_API_TOKEN", "")
SCRIPT_PATH = os.getenv("DEPLOY_SCRIPT_PATH", os.path.join(os.path.dirname(__file__), "deploy_and_restart.sh"))
MAX_BODY_BYTES = int(os.getenv("DEPLOY_API_MAX_BODY_BYTES", "32768"))
DEPLOY_TIMEOUT = int(os.getenv("DEPLOY_API_TIMEOUT_SEC", "900"))


def _json(handler, status, payload):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _authorized(handler):
    if not TOKEN:
        return False
    auth = (handler.headers.get("Authorization", "") or "").strip()
    bearer = f"Bearer {TOKEN}"
    if auth == bearer:
        return True
    alt = (handler.headers.get("X-Deploy-Token", "") or "").strip()
    return alt == TOKEN


class Handler(BaseHTTPRequestHandler):
    server_version = "ThriveDeployAPI/1.0"

    def log_message(self, fmt, *args):
        # Keep logging predictable for PM2/system logs.
        print("[deploy-api] " + (fmt % args))

    def do_GET(self):
        if self.path == "/health":
            return _json(self, 200, {"ok": True, "ts": int(time.time())})
        return _json(self, 404, {"ok": False, "reason": "Not found"})

    def do_POST(self):
        if self.path != "/deploy":
            return _json(self, 404, {"ok": False, "reason": "Not found"})
        if not _authorized(self):
            return _json(self, 401, {"ok": False, "reason": "Unauthorized"})

        length = int(self.headers.get("Content-Length", "0") or 0)
        if length > MAX_BODY_BYTES:
            return _json(self, 413, {"ok": False, "reason": "Payload too large"})

        payload = {}
        if length > 0:
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
            except Exception:
                return _json(self, 400, {"ok": False, "reason": "Invalid JSON"})

        force_restart = bool(payload.get("force_restart", False))

        env = os.environ.copy()
        env["FORCE_RESTART"] = "1" if force_restart else "0"

        started = time.time()
        try:
            result = subprocess.run(
                ["/usr/bin/env", "bash", SCRIPT_PATH],
                env=env,
                capture_output=True,
                text=True,
                timeout=DEPLOY_TIMEOUT,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return _json(self, 504, {"ok": False, "reason": "Deploy timed out"})
        except Exception as exc:
            return _json(self, 500, {"ok": False, "reason": f"Deploy launch failed: {exc}"})

        elapsed = round(time.time() - started, 3)
        ok = result.returncode == 0
        status = 200 if ok else 500
        return _json(
            self,
            status,
            {
                "ok": ok,
                "code": result.returncode,
                "elapsed_sec": elapsed,
                "stdout": result.stdout[-12000:],
                "stderr": result.stderr[-12000:],
            },
        )


if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("DEPLOY_API_TOKEN is required")
    if not os.path.exists(SCRIPT_PATH):
        raise SystemExit(f"Deploy script not found: {SCRIPT_PATH}")
    print(f"[deploy-api] listening on http://{HOST}:{PORT}")
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
