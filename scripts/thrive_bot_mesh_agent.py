#!/usr/bin/env python3
import argparse
import base64
import json
import os
import platform
import queue
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class ThriveBotAgent:
    def __init__(self, args):
        self.args = args
        self.sock = None
        self.sockfile = None
        self.send_lock = threading.Lock()
        self.stop_event = threading.Event()
        self.pending_delegations = {}
        self.pending_lock = threading.Lock()
        self.outbound_reports = queue.Queue()
        self.temp_dir = args.temp_dir or tempfile.mkdtemp(prefix="thrive-bot-agent-")

    def connect(self):
        raw = socket.create_connection((self.args.host, self.args.port), timeout=15)
        if self.args.ssl:
            if self.args.no_verify:
                ctx = ssl._create_unverified_context()
            else:
                ctx = ssl.create_default_context(cafile=self.args.cafile or None)
            self.sock = ctx.wrap_socket(raw, server_hostname=self.args.host)
        else:
            self.sock = raw
        self.sockfile = self.sock.makefile("r", encoding="utf-8", errors="ignore")

    def send(self, payload):
        wire = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
        with self.send_lock:
            self.sock.sendall(wire)

    def login(self):
        payload = {"action": "login", "user": self.args.user, "pass": self.args.password}
        if self.args.passkey_token:
            payload = {"action": "login_passkey", "user": self.args.user, "passkey_token": self.args.passkey_token}
        self.send(payload)
        line = self.sockfile.readline()
        if not line:
            raise RuntimeError("No login response from server.")
        msg = json.loads(line)
        if msg.get("status") != "ok":
            raise RuntimeError(msg.get("reason", "Login failed."))

    def register(self):
        moderation = {
            "enabled": bool(self.args.moderation),
            "kinds": [k.strip().lower() for k in self.args.moderation_kinds.split(",") if k.strip()] or ["guest_login", "direct_message", "file_offer"],
            "auto_report": not self.args.no_auto_report,
            "notify_user": self.args.notify_user or "",
        }
        self.send({
            "action": "register_bot_session",
            "auth_type": self.args.auth_type,
            "runtime": self.args.runtime,
            "host_label": self.args.host_label or platform.node(),
            "platform": platform.platform(),
            "capabilities": self.args.capability,
            "transports": self.args.transport,
            "temp_dir": self.temp_dir,
            "accepts_files": bool(self.args.accept_files),
            "supports_delegation": not self.args.no_delegation,
            "background": bool(self.args.background),
            "moderation": moderation,
        })

    def run(self):
        self.connect()
        self.login()
        self.register()
        self._start_report_worker()
        print(f"[{_now()}] Connected as {self.args.user} to {self.args.host}:{self.args.port}")
        while not self.stop_event.is_set():
            line = self.sockfile.readline()
            if not line:
                raise RuntimeError("Server closed connection.")
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except Exception:
                continue
            self.handle(msg)

    def _start_report_worker(self):
        t = threading.Thread(target=self._report_worker, daemon=True)
        t.start()

    def _report_worker(self):
        while not self.stop_event.is_set():
            try:
                item = self.outbound_reports.get(timeout=0.5)
            except queue.Empty:
                continue
            if not isinstance(item, dict):
                continue
            try:
                self.send(item)
            except Exception as exc:
                print(f"[{_now()}] Failed to send queued payload: {exc}", file=sys.stderr)

    def handle(self, msg):
        action = str(msg.get("action", "") or "")
        if action == "bot_session_registered":
            print(f"[{_now()}] Bot session registered: ok={msg.get('ok')}")
        elif action == "feature_caps":
            return
        elif action == "contact_list":
            return
        elif action == "contact_status":
            return
        elif action == "bot_mesh_request":
            self._handle_bot_mesh_request(msg)
        elif action == "bot_mesh_result":
            self._handle_bot_mesh_result(msg)
        elif action == "bot_mesh_status":
            self._handle_bot_mesh_status(msg)
        elif action == "bot_mesh_file_available":
            self._handle_bot_mesh_file_available(msg)
        elif action == "bot_mesh_file_data":
            self._handle_bot_mesh_file_data(msg)
        elif action == "bot_moderation_event":
            self._handle_moderation_event(msg)
        elif action == "msg":
            self._handle_direct_message(msg)
        elif action == "msg_failed":
            print(f"[{_now()}] Message failed: {msg.get('reason', '')}", file=sys.stderr)
        elif action in ("bot_mesh_directory", "bot_mesh_file_stored"):
            print(f"[{_now()}] {action}: {msg}")
        else:
            print(f"[{_now()}] Received {action}: {msg}")

    def _handle_direct_message(self, msg):
        sender = str(msg.get("from", "") or "").strip()
        text = str(msg.get("msg", "") or "").strip()
        if not sender or not text:
            return
        try:
            reply = self._generate_reply(
                prompt=text,
                system_prompt=self.args.system_prompt or "",
                context={"kind": "direct_message", "from": sender},
            )
        except Exception as exc:
            reply = f"{self.args.user} could not complete that locally: {exc}"
        self.send({
            "action": "msg",
            "from": self.args.user,
            "to": sender,
            "msg": reply,
        })

    def _handle_bot_mesh_request(self, msg):
        sender = str(msg.get("from", "") or "").strip()
        request_id = str(msg.get("request_id", "") or str(uuid.uuid4()))
        task = str(msg.get("task", "") or "").strip()
        metadata = msg.get("metadata", {}) if isinstance(msg.get("metadata"), dict) else {}
        if not sender or not task:
            return
        try:
            result = self._generate_reply(
                prompt=task,
                system_prompt=self.args.system_prompt or "",
                context={"kind": "bot_mesh_request", "from": sender, "metadata": metadata},
            )
            self.send({
                "action": "bot_mesh_result",
                "from": self.args.user,
                "to": sender,
                "request_id": request_id,
                "result": {
                    "ok": True,
                    "text": result,
                    "handled_by": self.args.user,
                    "backend": self.args.backend,
                },
            })
        except Exception as exc:
            if self.args.delegate_to and not self.args.no_delegation:
                delegated_id = str(uuid.uuid4())
                with self.pending_lock:
                    self.pending_delegations[delegated_id] = {"upstream_from": sender, "upstream_request_id": request_id}
                self.send({
                    "action": "bot_mesh_status",
                    "from": self.args.user,
                    "to": sender,
                    "request_id": request_id,
                    "status": f"Delegating to {self.args.delegate_to} after local failure.",
                    "metadata": {"local_error": str(exc)},
                })
                self.send({
                    "action": "bot_mesh_request",
                    "from": self.args.user,
                    "to": self.args.delegate_to,
                    "request_id": delegated_id,
                    "task": task,
                    "metadata": {"delegated_for": sender, "upstream_request_id": request_id, "local_error": str(exc)},
                })
                return
            self.send({
                "action": "bot_mesh_result",
                "from": self.args.user,
                "to": sender,
                "request_id": request_id,
                "result": {"ok": False, "error": str(exc), "handled_by": self.args.user},
            })

    def _handle_bot_mesh_result(self, msg):
        request_id = str(msg.get("request_id", "") or "").strip()
        with self.pending_lock:
            pending = self.pending_delegations.pop(request_id, None)
        if not pending:
            print(f"[{_now()}] Bot mesh result: {msg}")
            return
        self.send({
            "action": "bot_mesh_result",
            "from": self.args.user,
            "to": pending["upstream_from"],
            "request_id": pending["upstream_request_id"],
            "result": msg.get("result"),
        })

    def _handle_bot_mesh_status(self, msg):
        request_id = str(msg.get("request_id", "") or "").strip()
        with self.pending_lock:
            pending = self.pending_delegations.get(request_id)
        if pending:
            self.send({
                "action": "bot_mesh_status",
                "from": self.args.user,
                "to": pending["upstream_from"],
                "request_id": pending["upstream_request_id"],
                "status": msg.get("status", ""),
                "metadata": msg.get("metadata", {}),
            })
            return
        print(f"[{_now()}] Bot mesh status: {msg}")

    def _handle_bot_mesh_file_available(self, msg):
        file_id = str(msg.get("file_id", "") or "").strip()
        if not file_id:
            return
        self.send({"action": "bot_mesh_fetch_file", "file_id": file_id, "consume": False})

    def _handle_bot_mesh_file_data(self, msg):
        if not msg.get("ok"):
            print(f"[{_now()}] File fetch failed: {msg.get('reason', '')}", file=sys.stderr)
            return
        filename = os.path.basename(str(msg.get("filename", "") or "").strip()) or f"{msg.get('file_id', 'file')}.bin"
        dest = os.path.join(self.temp_dir, filename)
        data = base64.b64decode(str(msg.get("data", "") or "").encode("ascii"))
        with open(dest, "wb") as handle:
            handle.write(data)
        print(f"[{_now()}] Saved relayed file to {dest}")

    def _handle_moderation_event(self, msg):
        payload = msg.get("payload", {}) if isinstance(msg.get("payload"), dict) else {}
        event_type = str(msg.get("event_type", "") or "").strip()
        summary = self._build_moderation_summary(event_type, payload)
        print(f"[{_now()}] Moderation event {event_type}: {summary}")
        if not self.args.moderation:
            return
        if self.args.no_auto_report:
            return
        notify_user = self.args.notify_user or ""
        if not notify_user:
            return
        try:
            analysis = self._generate_reply(
                prompt=(
                    "Review this moderation event and answer with a concise moderation assessment, "
                    "spam likelihood, and recommended action.\n\n"
                    f"{json.dumps({'event_type': event_type, 'payload': payload}, ensure_ascii=False)}"
                ),
                system_prompt=self.args.moderation_prompt or self.args.system_prompt or "",
                context={"kind": "moderation", "event_type": event_type},
            )
        except Exception as exc:
            analysis = f"Moderation analysis failed locally: {exc}"
        self.outbound_reports.put({
            "action": "msg",
            "from": self.args.user,
            "to": notify_user,
            "msg": f"[Moderation:{event_type}] {summary}\n\n{analysis}",
        })

    def _build_moderation_summary(self, event_type, payload):
        if event_type == "guest_login":
            return f"guest login by {payload.get('user', 'unknown')} from {payload.get('ip', 'unknown ip')}"
        if event_type == "direct_message":
            return (
                f"{payload.get('from', 'unknown')} -> {payload.get('to', 'unknown')} "
                f"score={payload.get('spam_score', 0)} excerpt={payload.get('message_excerpt', '')}"
            )
        if event_type == "file_offer":
            return f"{payload.get('from', 'unknown')} offered {payload.get('file_count', 0)} file(s) to {payload.get('to', 'unknown')}"
        return json.dumps(payload, ensure_ascii=False)

    def _generate_reply(self, prompt, system_prompt="", context=None):
        backend = self.args.backend.strip().lower()
        if backend == "echo":
            return f"{self.args.user}: {prompt}"
        if backend == "command":
            return self._run_command_backend(prompt, system_prompt, context or {})
        if backend == "ollama":
            return self._run_ollama_backend(prompt, system_prompt, context or {})
        if backend == "auto":
            if self.args.ollama_url:
                try:
                    return self._run_ollama_backend(prompt, system_prompt, context or {})
                except Exception:
                    pass
            if self.args.command:
                return self._run_command_backend(prompt, system_prompt, context or {})
            return f"{self.args.user} received: {prompt}"
        raise RuntimeError(f"Unsupported backend: {backend}")

    def _run_command_backend(self, prompt, system_prompt, context):
        if not self.args.command:
            raise RuntimeError("No command configured for command backend.")
        env = os.environ.copy()
        env["THRIVE_BOT_PROMPT"] = prompt
        env["THRIVE_BOT_SYSTEM_PROMPT"] = system_prompt or ""
        env["THRIVE_BOT_CONTEXT_JSON"] = json.dumps(context or {}, ensure_ascii=False)
        proc = subprocess.run(
            self.args.command,
            input=prompt,
            text=True,
            capture_output=True,
            timeout=self.args.command_timeout,
            env=env,
            shell=True,
        )
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip() or f"Command backend exited with {proc.returncode}")
        return (proc.stdout or "").strip() or "(no output)"

    def _run_ollama_backend(self, prompt, system_prompt, context):
        body = {
            "model": self.args.ollama_model,
            "prompt": prompt,
            "system": system_prompt or "",
            "stream": False,
            "options": {"num_predict": self.args.max_tokens},
            "context": [],
        }
        if context:
            body["prompt"] = (
                f"Context:\n{json.dumps(context, ensure_ascii=False)}\n\n"
                f"User request:\n{prompt}"
            )
        req = urllib.request.Request(
            self.args.ollama_url.rstrip("/") + "/api/generate",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.args.ollama_timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8", errors="ignore"))
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Ollama request failed: {exc}") from exc
        reply = str(payload.get("response", "") or "").strip()
        if not reply:
            raise RuntimeError("Ollama returned no response text.")
        return reply


def build_parser():
    parser = argparse.ArgumentParser(description="Thrive Messenger bot mesh / moderation agent")
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", type=int, default=5005)
    parser.add_argument("--ssl", action="store_true", default=False)
    parser.add_argument("--no-verify", action="store_true")
    parser.add_argument("--cafile", default="")
    parser.add_argument("--user", required=True)
    parser.add_argument("--password", default="")
    parser.add_argument("--passkey-token", default="")
    parser.add_argument("--backend", default="auto", choices=["auto", "ollama", "command", "echo"])
    parser.add_argument("--auth-type", default="codex")
    parser.add_argument("--runtime", default="cli")
    parser.add_argument("--host-label", default="")
    parser.add_argument("--temp-dir", default="")
    parser.add_argument("--background", action="store_true")
    parser.add_argument("--accept-files", action="store_true")
    parser.add_argument("--no-delegation", action="store_true")
    parser.add_argument("--delegate-to", default="")
    parser.add_argument("--capability", action="append", default=["chat", "bot_mesh"])
    parser.add_argument("--transport", action="append", default=["socket"])
    parser.add_argument("--system-prompt", default="")
    parser.add_argument("--moderation-prompt", default="")
    parser.add_argument("--moderation", action="store_true")
    parser.add_argument("--moderation-kinds", default="guest_login,direct_message,file_offer")
    parser.add_argument("--notify-user", default="")
    parser.add_argument("--no-auto-report", action="store_true")
    parser.add_argument("--ollama-url", default="http://127.0.0.1:11434")
    parser.add_argument("--ollama-model", default="llama3.2")
    parser.add_argument("--ollama-timeout", type=int, default=60)
    parser.add_argument("--max-tokens", type=int, default=400)
    parser.add_argument("--command", default="")
    parser.add_argument("--command-timeout", type=int, default=180)
    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()
    if not args.password and not args.passkey_token:
        parser.error("--password or --passkey-token is required")
    agent = ThriveBotAgent(args)
    try:
        agent.run()
    except KeyboardInterrupt:
        return 0
    except Exception as exc:
        print(f"[{_now()}] Agent failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
