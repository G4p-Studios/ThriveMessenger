#!/usr/bin/env python3
import json
import os
import urllib.request


ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
README = os.path.join(ROOT, "README.md")
OUT = os.path.join(ROOT, "assets", "help", "help_docs.json")
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "llama3.2")

CONTEXTS = [
    "general",
    "login",
    "main",
    "chat",
    "directory",
    "admin",
    "settings",
    "server_info",
    "bot_rules",
]

FEATURE_NOTES = """
- Multi-server manager (add/edit/remove, primary server)
- Update feed URL + preferred/fallback repos
- File transfer actions and transfer history window
- Chat key actions: Enter send, Cmd+Enter newline, Ctrl+Enter send file
- Status presets with optional custom text
- Server info dialog (counts, uptime, file policy)
- Admin command console and restart scheduling
- Bot Rules Manager (load/save/reset)
- Per-admin bot rule overrides seeded from global agent ZIP rules
- Non-admin can view rules but cannot edit
""".strip()

FALLBACK = {
    "general": "<h1>Thrive Messenger Help</h1><p>Press F1 in each window for contextual help. Press Escape or Command+W to close help and return.</p>",
    "login": "<h1>Login Help</h1><p>Select a server, enter username/password, and sign in. Use Manage Servers to add, edit, and set a primary server.</p>",
    "main": "<h1>Contacts Help</h1><p>Use Contacts list, menus, and context menus to start chat, send files, block/unblock, and manage contacts.</p>",
    "chat": "<h1>Chat Help</h1><p>Enter sends message, Cmd+Enter inserts a new line, and Ctrl+Enter sends file. Use chat history and file transfer actions from menus.</p>",
    "directory": "<h1>User Directory Help</h1><p>Browse users, filter/sort lists, and add contacts. Multi-server entries display server labels where available.</p>",
    "admin": "<h1>Admin Commands Help</h1><p>Use Server Side Commands for admin operations like alerts, user management, and restart scheduling.</p><p>In the command text box, type ? or help (with or without a leading slash) to show command usage help.</p>",
    "settings": "<h1>Settings Help</h1><p>Configure audio, accessibility, chat behavior, server/update settings, and administration options.</p>",
    "server_info": "<h1>Server Info Help</h1><p>View host, port, encryption, online users/admins, uptime, and file policy limits.</p>",
    "bot_rules": "<h1>Bot Rules Help</h1><p>Admins can load/edit/save/reset bot rules. Rules can be seeded from agent ZIP rules. Non-admin users can view active rules read-only.</p>",
}


def read_text(path):
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        return f.read()


def generate_html(context, readme_excerpt):
    prompt = f"""
Write concise in-app help for Thrive Messenger context "{context}".
Return HTML fragment only. No markdown. Use <h1>, <p>, and optional <ul><li>.
Style: short, practical, accessible.
Mention relevant features from this list:
{FEATURE_NOTES}

README excerpt:
{readme_excerpt}
""".strip()
    payload = {
        "model": OLLAMA_MODEL,
        "stream": False,
        "prompt": prompt,
        "options": {"temperature": 0.2, "num_predict": 260},
    }
    req = urllib.request.Request(
        f"{OLLAMA_URL}/api/generate",
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=45) as resp:
        body = resp.read().decode("utf-8", errors="ignore")
    data = json.loads(body)
    text = str(data.get("response", "") or "").strip()
    if not text:
        raise RuntimeError("empty response")
    if "<h1" not in text.lower():
        text = f"<h1>{context.replace('_', ' ').title()} Help</h1><p>{text}</p>"
    return text


def main():
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    readme_excerpt = read_text(README)[:10000]
    docs = {}
    for ctx in CONTEXTS:
        try:
            docs[ctx] = generate_html(ctx, readme_excerpt)
            print(f"generated: {ctx}")
        except Exception as e:
            docs[ctx] = FALLBACK[ctx]
            print(f"fallback: {ctx} ({e})")
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(docs, f, ensure_ascii=True, indent=2)
    print(f"Wrote {OUT}")


if __name__ == "__main__":
    main()
