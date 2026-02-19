import sqlite3, threading, socket, json, datetime, sys, configparser, ssl, os, uuid, base64, time, subprocess, tempfile, glob, zipfile
import smtplib, secrets
import urllib.request, urllib.parse
from email.mime.text import MIMEText

DB = 'thrive.db'
ADMIN_FILE = 'admins.txt'
clients = {}
client_statuses = {}
lock = threading.Lock()
smtp_config = {}
flexpbx_config = {}
file_config = {}
bot_runtime_config = {}
shutdown_timeout = 5
max_status_length = 50
pending_transfers = {}
transfer_lock = threading.Lock()
server_port = 0
use_ssl = False
server_started_at = time.time()
bot_usernames = set()
bot_status_map = {}
bot_purpose_map = {}
bot_service_map = {}
bot_voice_map = {}
bot_external_usernames = set()
allow_external_bot_contacts = True
docs_cache = {}
bot_rules_config = {}
bot_rules_text = {}
restart_lock = threading.Lock()
restart_scheduled_for = None
group_call_sessions = {}
group_call_lock = threading.Lock()
FEATURE_DEFAULTS = {
    "bots": {"enabled": True, "ui_visible": True, "scope": "all", "description": "Bot contacts and bot chat features."},
    "bot_rules": {"enabled": True, "ui_visible": True, "scope": "admin", "description": "Bot rules management features."},
    "group_chat": {"enabled": True, "ui_visible": True, "scope": "all", "description": "Group chat create/join/send features."},
    "group_call": {"enabled": True, "ui_visible": True, "scope": "all", "description": "Group call session and signaling features."},
    "group_policy": {"enabled": True, "ui_visible": True, "scope": "admin", "description": "Group policy management features."},
    "admin_console": {"enabled": True, "ui_visible": True, "scope": "admin", "description": "Server side admin command console."},
    "server_manager": {"enabled": True, "ui_visible": True, "scope": "all", "description": "Server manager and server tools UI."},
}

GROUP_POLICY_SCHEMA = {
    "allow_group_text": ("bool", True, "Allow users to send text messages in groups."),
    "allow_group_links": ("bool", True, "Allow links in group messages."),
    "allow_group_files": ("bool", True, "Allow file uploads/shares in groups."),
    "allow_group_voice": ("bool", True, "Allow users to join group voice calls."),
    "allow_group_video": ("bool", True, "Allow users to join group video calls."),
    "allow_group_screen_share": ("bool", False, "Allow screen sharing in group calls."),
    "allow_group_reactions": ("bool", True, "Allow reactions in group chats."),
    "allow_group_edit": ("bool", True, "Allow users to edit their group messages."),
    "allow_group_delete_own": ("bool", True, "Allow users to delete their own group messages."),
    "allow_group_delete_any": ("bool", False, "Allow moderators/admins to delete any group message."),
    "allow_group_invite_members": ("bool", True, "Allow non-admin members to invite users to groups."),
    "allow_group_pin_messages": ("bool", False, "Allow non-admin members to pin messages."),
    "allow_group_create_channels": ("bool", False, "Allow non-admin members to create sub-channels."),
    "allow_group_mention_everyone": ("bool", False, "Allow @everyone style mentions."),
    "allow_group_external_bots": ("bool", False, "Allow external bot accounts in groups."),
    "max_group_message_length": ("int", 4000, "Maximum group message length."),
    "max_group_attachments_per_message": ("int", 8, "Maximum attachments per group message."),
    "max_group_file_size_bytes": ("int", 52428800, "Maximum file size for group uploads."),
    "max_group_participants": ("int", 200, "Maximum number of participants per group."),
    "max_group_concurrent_voice": ("int", 40, "Maximum concurrent users in group voice calls."),
    "group_message_edit_window_seconds": ("int", 600, "Time window users can edit group messages."),
    "group_message_delete_undo_seconds": ("int", 20, "Undo window after deleting group messages."),
    "group_rate_limit_per_minute": ("int", 120, "Per-user group message rate limit per minute."),
    "group_slow_mode_seconds": ("int", 0, "Slow mode delay between messages (0 disables)."),
    "group_retention_days": ("int", 0, "Message retention days (0 keeps indefinitely)."),
    "group_require_verified_users": ("bool", False, "Require verified accounts for group participation."),
}

def _group_policy_defaults():
    return {k: GROUP_POLICY_SCHEMA[k][1] for k in GROUP_POLICY_SCHEMA}

def _coerce_group_policy_value(key, raw):
    value_type = GROUP_POLICY_SCHEMA[key][0]
    if value_type == "bool":
        if isinstance(raw, bool):
            return raw
        val = str(raw or "").strip().lower()
        if val in ("1", "true", "yes", "on", "enabled"):
            return True
        if val in ("0", "false", "no", "off", "disabled"):
            return False
        raise ValueError(f"{key} expects true/false")
    if value_type == "int":
        val = int(raw)
        if val < 0:
            raise ValueError(f"{key} must be >= 0")
        return val
    raise ValueError(f"Unsupported type for {key}")

def _normalize_group_name(group_name):
    g = str(group_name or "").strip()
    return g if g else "__global__"

def _fetch_group_policy(scope="global", group_name=None):
    scope = "group" if str(scope).lower() == "group" else "global"
    group_name = _normalize_group_name(group_name)
    defaults = _group_policy_defaults()
    try:
        con = sqlite3.connect(DB)
        row = con.execute(
            "SELECT policy_json FROM group_policies WHERE scope=? AND group_name=?",
            (scope, group_name),
        ).fetchone()
        con.close()
        if not row or not row[0]:
            return defaults
        parsed = json.loads(str(row[0]))
        if not isinstance(parsed, dict):
            return defaults
        out = defaults.copy()
        for key, val in parsed.items():
            if key in GROUP_POLICY_SCHEMA:
                try:
                    out[key] = _coerce_group_policy_value(key, val)
                except Exception:
                    pass
        return out
    except Exception:
        return defaults

def _upsert_group_policy(scope="global", group_name=None, updates=None, updated_by="admin"):
    scope = "group" if str(scope).lower() == "group" else "global"
    group_name = _normalize_group_name(group_name)
    updates = updates or {}
    current = _fetch_group_policy(scope, group_name)
    merged = current.copy()
    for key, raw in updates.items():
        if key not in GROUP_POLICY_SCHEMA:
            raise ValueError(f"Unknown policy key: {key}")
        merged[key] = _coerce_group_policy_value(key, raw)
    payload = json.dumps(merged, ensure_ascii=False)
    con = sqlite3.connect(DB)
    con.execute(
        """
        INSERT OR REPLACE INTO group_policies(scope, group_name, policy_json, updated_by, updated_at)
        VALUES(?,?,?,?,?)
        """,
        (scope, group_name, payload, str(updated_by or "admin"), datetime.datetime.utcnow().isoformat()),
    )
    con.commit()
    con.close()
    return merged

def _reset_group_policy(scope="global", group_name=None):
    scope = "group" if str(scope).lower() == "group" else "global"
    group_name = _normalize_group_name(group_name)
    con = sqlite3.connect(DB)
    con.execute("DELETE FROM group_policies WHERE scope=? AND group_name=?", (scope, group_name))
    con.commit()
    con.close()

def _policy_schema_payload():
    return {
        key: {
            "type": GROUP_POLICY_SCHEMA[key][0],
            "default": GROUP_POLICY_SCHEMA[key][1],
            "description": GROUP_POLICY_SCHEMA[key][2],
        }
        for key in sorted(GROUP_POLICY_SCHEMA.keys())
    }

def _group_call_snapshot(group_name):
    with group_call_lock:
        data = group_call_sessions.get(group_name) or {}
        participants = sorted(list(data.get("participants", set())))
        mode = data.get("mode", "voice")
    return {"group": group_name, "mode": mode, "participants": participants, "count": len(participants)}

def _is_valid_feature_scope(scope):
    return str(scope or "").strip().lower() in ("all", "admin", "allowlist")

def _seed_feature_defaults():
    con = sqlite3.connect(DB)
    for key, meta in FEATURE_DEFAULTS.items():
        row = con.execute("SELECT 1 FROM feature_policies WHERE feature_key=?", (key,)).fetchone()
        if row:
            continue
        con.execute(
            """
            INSERT INTO feature_policies(feature_key, enabled, ui_visible, scope, description, updated_by, updated_at)
            VALUES(?,?,?,?,?,?,?)
            """,
            (
                key,
                1 if meta.get("enabled", True) else 0,
                1 if meta.get("ui_visible", True) else 0,
                str(meta.get("scope", "all")),
                str(meta.get("description", "")),
                "system",
                datetime.datetime.utcnow().isoformat(),
            ),
        )
    con.commit()
    con.close()

def _feature_policy_row(feature_key):
    fk = str(feature_key or "").strip()
    if fk not in FEATURE_DEFAULTS:
        return None
    con = sqlite3.connect(DB)
    row = con.execute(
        "SELECT enabled, ui_visible, scope, description FROM feature_policies WHERE feature_key=?",
        (fk,),
    ).fetchone()
    con.close()
    if not row:
        meta = FEATURE_DEFAULTS[fk]
        return {
            "feature_key": fk,
            "enabled": bool(meta.get("enabled", True)),
            "ui_visible": bool(meta.get("ui_visible", True)),
            "scope": str(meta.get("scope", "all")),
            "description": str(meta.get("description", "")),
        }
    return {
        "feature_key": fk,
        "enabled": bool(int(row[0] or 0)),
        "ui_visible": bool(int(row[1] or 0)),
        "scope": str(row[2] or "all"),
        "description": str(row[3] or ""),
    }

def _feature_user_allowed(feature_key, username):
    con = sqlite3.connect(DB)
    row = con.execute(
        "SELECT 1 FROM feature_allow_users WHERE feature_key=? AND username=?",
        (feature_key, username),
    ).fetchone()
    con.close()
    return bool(row)

def _feature_group_allowed(feature_key, username):
    con = sqlite3.connect(DB)
    groups = [r[0] for r in con.execute("SELECT group_name FROM user_access_groups WHERE username=?", (username,)).fetchall()]
    if not groups:
        con.close()
        return False
    placeholders = ",".join(["?"] * len(groups))
    params = [feature_key] + groups
    row = con.execute(
        f"SELECT 1 FROM feature_allow_groups WHERE feature_key=? AND group_name IN ({placeholders}) LIMIT 1",
        params,
    ).fetchone()
    con.close()
    return bool(row)

def _can_user_use_feature(username, feature_key):
    policy = _feature_policy_row(feature_key)
    if not policy:
        return False
    if not policy.get("enabled", False):
        return False
    scope = str(policy.get("scope", "all")).lower()
    is_admin = username in get_admins()
    if scope == "all":
        return True
    if scope == "admin":
        return is_admin
    if scope == "allowlist":
        if is_admin:
            return True
        return _feature_user_allowed(feature_key, username) or _feature_group_allowed(feature_key, username)
    return False

def _feature_caps_for_user(username):
    caps = {}
    for fk in sorted(FEATURE_DEFAULTS.keys()):
        p = _feature_policy_row(fk) or {}
        caps[fk] = {
            "enabled": bool(p.get("enabled", False)),
            "ui_visible": bool(p.get("ui_visible", False)),
            "scope": str(p.get("scope", "all")),
            "can_use": bool(_can_user_use_feature(username, fk)),
        }
    return caps

def _send_feature_caps(sock, username):
    try:
        sock.sendall((json.dumps({"action": "feature_caps", "caps": _feature_caps_for_user(username)}) + "\n").encode())
    except Exception:
        pass

def _broadcast_feature_caps():
    with lock:
        targets = list(clients.items())
    for uname, sock in targets:
        _send_feature_caps(sock, uname)

def _group_call_broadcast(group_name, payload, exclude=None):
    targets = []
    with group_call_lock:
        members = list((group_call_sessions.get(group_name) or {}).get("participants", set()))
    with lock:
        for uname in members:
            if exclude and uname == exclude:
                continue
            s = clients.get(uname)
            if s:
                targets.append(s)
    wire = (json.dumps(payload) + "\n").encode()
    for s in targets:
        try:
            s.sendall(wire)
        except Exception:
            pass

def _remove_user_from_all_group_calls(username):
    events = []
    with group_call_lock:
        for g, data in list(group_call_sessions.items()):
            participants = data.get("participants", set())
            if username in participants:
                participants.discard(username)
                snapshot = {"action": "group_call_event", "event": "leave", "by": username}
                snapshot.update(_group_call_snapshot(g))
                events.append((g, snapshot))
            if not participants:
                group_call_sessions.pop(g, None)
    for g, payload in events:
        _group_call_broadcast(g, payload, exclude=username)
def _is_admin(username):
    return str(username or "").strip() in get_admins()

def _is_virtual_bot(username):
    uname = str(username or "").strip()
    return uname in bot_usernames or uname.lower() == "openclaw-bot"

def _is_registered_bot(username):
    uname = str(username or "").strip()
    if not uname:
        return False
    if _is_virtual_bot(uname):
        return True
    if uname in bot_external_usernames:
        return True
    if allow_external_bot_contacts and uname.lower().endswith("-bot"):
        return True
    return False

def _parse_bot_map(raw):
    out = {}
    for item in str(raw or "").split(","):
        if ":" not in item:
            continue
        name, value = item.split(":", 1)
        name = name.strip()
        value = value.strip()
        if name and value:
            out[name] = value
    return out

def _safe_read_text(path, limit=120000):
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read(limit)
    except Exception:
        return ""

def _select_agent_zip(pattern_or_path):
    raw = str(pattern_or_path or "").strip()
    if not raw:
        return ""
    if "*" in raw or "?" in raw or "[" in raw:
        matches = sorted(glob.glob(raw))
        if not matches:
            return ""
        return matches[-1]
    return raw if os.path.isfile(raw) else ""

def _load_rules_from_zip(zip_path, max_chars=60000):
    if not zip_path or not os.path.isfile(zip_path):
        return ""
    preferred = ("AGENTS.md", "RULES.md", "RULES.txt", "BOT_RULES.md", "BOT_RULES.txt", "README.md")
    chunks = []
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            names = zf.namelist()
            ordered = []
            for p in preferred:
                ordered.extend([n for n in names if n.lower().endswith(p.lower())])
            ordered.extend([
                n for n in names
                if n not in ordered and (
                    "rule" in n.lower() or n.lower().endswith(".md") or n.lower().endswith(".txt")
                )
            ])
            for name in ordered:
                try:
                    data = zf.read(name)
                    text = data.decode("utf-8", errors="ignore").strip()
                    if text:
                        chunks.append(f"# Source: {name}\n{text}")
                    if sum(len(c) for c in chunks) >= max_chars:
                        break
                except Exception:
                    continue
    except Exception:
        return ""
    out = "\n\n".join(chunks).strip()
    return out[:max_chars]

def _refresh_bot_rules():
    global bot_rules_text
    bot_rules_text = {}
    zip_path = _select_agent_zip(bot_rules_config.get("agent_rules_zip_path", ""))
    local_rules_path = str(bot_rules_config.get("agent_rules_file_path", "") or "").strip()
    common_rules = ""
    if zip_path:
        common_rules = _load_rules_from_zip(zip_path)
    if not common_rules and local_rules_path:
        common_rules = _safe_read_text(local_rules_path, limit=60000)
    if common_rules:
        for bot in bot_usernames | {"openclaw-bot"} | bot_external_usernames:
            bot_rules_text[bot] = common_rules

def _rules_for_bot(bot_name):
    return str(bot_rules_text.get(bot_name, "") or "").strip()

def _get_admin_bot_rules(owner, bot_name):
    owner = str(owner or "").strip()
    bot_name = str(bot_name or "").strip()
    if not owner or not bot_name:
        return ""
    try:
        con = sqlite3.connect(DB)
        row = con.execute(
            "SELECT rules FROM bot_rule_overrides WHERE owner=? AND bot=?",
            (owner, bot_name),
        ).fetchone()
        con.close()
        return str(row[0] or "").strip() if row else ""
    except Exception:
        return ""

def _set_admin_bot_rules(owner, bot_name, rules):
    owner = str(owner or "").strip()
    bot_name = str(bot_name or "").strip()
    rules = str(rules or "").strip()
    if not owner or not bot_name:
        return False
    try:
        con = sqlite3.connect(DB)
        con.execute(
            """
            INSERT OR REPLACE INTO bot_rule_overrides(owner, bot, rules, updated_at)
            VALUES(?,?,?,?)
            """,
            (owner, bot_name, rules, datetime.datetime.utcnow().isoformat()),
        )
        con.commit()
        con.close()
        return True
    except Exception:
        return False

def _clear_admin_bot_rules(owner, bot_name):
    owner = str(owner or "").strip()
    bot_name = str(bot_name or "").strip()
    if not owner or not bot_name:
        return False
    try:
        con = sqlite3.connect(DB)
        con.execute("DELETE FROM bot_rule_overrides WHERE owner=? AND bot=?", (owner, bot_name))
        con.commit()
        con.close()
        return True
    except Exception:
        return False

def _effective_rules_for_bot(bot_name, owner=None):
    base_rules = _rules_for_bot(bot_name)
    owner = str(owner or "").strip()
    if owner and _is_admin(owner):
        admin_rules = _get_admin_bot_rules(owner, bot_name)
        if admin_rules:
            return admin_rules
    return base_rules

def _ensure_admin_bot_rules_seed(owner, bot_name):
    owner = str(owner or "").strip()
    bot_name = str(bot_name or "").strip()
    if not owner or not bot_name or not _is_admin(owner):
        return
    if _get_admin_bot_rules(owner, bot_name):
        return
    base_rules = _rules_for_bot(bot_name)
    if base_rules:
        _set_admin_bot_rules(owner, bot_name, base_rules)

def _load_docs_text():
    key = "docs_text"
    if key in docs_cache:
        return docs_cache[key]
    roots = [
        os.path.abspath(os.path.join(os.path.dirname(__file__), "..")),
        os.getcwd(),
    ]
    candidates = []
    for root in roots:
        candidates.extend([
            os.path.join(root, "README.md"),
            os.path.join(root, "F1_HELP.md"),
            os.path.join(root, "HELP.md"),
            os.path.join(root, "docs", "README.md"),
        ])
    chunks = []
    for path in candidates:
        if os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8", errors="ignore") as f:
                    chunks.append(f"# Source: {os.path.basename(path)}\n{f.read()}")
            except Exception:
                pass
    docs_text = "\n\n".join(chunks)
    docs_cache[key] = docs_text
    return docs_text

def _documentation_context_for_query(query, max_chars=2500):
    docs_text = _load_docs_text()
    if not docs_text:
        return ""
    q = str(query or "").lower()
    words = [w for w in q.replace("\n", " ").split(" ") if len(w) >= 4]
    words = words[:8]
    if not words:
        return docs_text[:max_chars]
    lines = docs_text.splitlines()
    matched = []
    for i, line in enumerate(lines):
        ll = line.lower()
        if any(w in ll for w in words):
            start = max(0, i - 1)
            end = min(len(lines), i + 2)
            matched.extend(lines[start:end])
            if len("\n".join(matched)) >= max_chars:
                break
    snippet = "\n".join(matched).strip()
    if not snippet:
        snippet = docs_text[:max_chars]
    return snippet[:max_chars]

def _active_usernames():
    with lock:
        extra_bots = {"openclaw-bot"}
        return set(clients.keys()) | set(bot_usernames) | set(bot_external_usernames) | extra_bots

def _is_online_user(username):
    return username in _active_usernames()

def _status_for_user(username):
    if _is_registered_bot(username):
        status = bot_status_map.get(username, "online")
        if str(username).lower() == "openclaw-bot" and username not in bot_purpose_map:
            purpose = "automation and assistant bot"
        else:
            purpose = bot_purpose_map.get(username, "")
        return f"{status} - {purpose}" if purpose else status
    with lock:
        return client_statuses.get(username, "online" if username in clients else "offline")

def _maybe_send_bot_reply(sender_sock, sender_user, to_user, text):
    if not _is_virtual_bot(to_user):
        return False
    reply = _ollama_bot_reply(sender_user, to_user, text)
    if not reply:
        lower = (text or "").strip().lower()
        if not lower:
            reply = "I'm online and ready. Ask me for help, commands, or server status."
        elif any(w in lower for w in ("hi", "hello", "hey")):
            reply = f"Hi {sender_user}. I'm {to_user}. How can I help?"
        elif "help" in lower:
            reply = "You can ask me about status, contacts, file transfers, or admin features."
        elif "status" in lower:
            reply = "I can report server presence and room/user status where available."
        elif "file" in lower:
            reply = "File transfers are available from chat and user menus. Check File Transfers for history."
        elif "admin" in lower:
            reply = "Admin actions are available from Server Side Commands and admin menus, based on your role."
        else:
            reply = "I couldn't reach the model right now. Ask again in a moment."
    tts_payload = _build_bot_tts_payload(to_user, reply, text)
    payload = {
        "action": "msg",
        "from": to_user,
        "to": sender_user,
        "time": datetime.datetime.now().isoformat(),
        "msg": reply,
    }
    if tts_payload:
        payload.update(tts_payload)
    try:
        sender_sock.sendall((json.dumps(payload) + "\n").encode())
    except Exception:
        pass
    return True

def _ollama_bot_reply(sender_user, bot_name, text):
    if not bot_runtime_config.get('ollama_enabled', False):
        return None
    base_url = str(bot_runtime_config.get('ollama_url', 'http://127.0.0.1:11434')).rstrip('/')
    model = str(bot_runtime_config.get('ollama_model', 'llama3.2')).strip() or 'llama3.2'
    timeout = int(bot_runtime_config.get('ollama_timeout', 20) or 20)
    purpose = bot_purpose_map.get(bot_name, "").strip()
    service_scope = bot_service_map.get(bot_name, "").strip()
    if str(bot_name).lower() == "openclaw-bot":
        if not purpose:
            purpose = "automation and assistant bot for app and server tasks"
        if not service_scope:
            service_scope = "chat contacts settings admin tools server management integrations"
    system_prompt = str(bot_runtime_config.get('ollama_system_prompt', '') or '').strip()
    if not system_prompt:
        system_prompt = (
            "You are the Thrive Messenger assistant bot. "
            "You help users with any app-related task and you know the Thrive Messenger client and server features. "
            "Give practical step-by-step instructions for chat, contacts, file transfer, server manager, settings, "
            "admin tools, and troubleshooting. Be concise, clear, and action-oriented. "
            "You can also handle normal friendly chat, but prioritize helping users use the app when they ask app questions. "
            "Use a natural conversational style, not an instruction-manual tone. "
            "If the user asks a direct question, answer directly first in one sentence, then add brief context if needed. "
            "For status-style questions like 'who is online', provide the direct answer immediately. "
            "Avoid repeating the user's message. If a feature is unsupported, say that clearly and suggest alternatives."
        )
    if purpose:
        system_prompt += f" Your role on this server: {purpose}."
    if service_scope:
        system_prompt += (
            f" You are trained for these services/features: {service_scope}. "
            "When users ask about these services, provide concrete usage steps and troubleshooting."
        )
    user_text = (text or "").strip()
    if not user_text:
        user_text = "Introduce yourself and explain how you can help in one short message."
    docs_context = _documentation_context_for_query(user_text)
    rules_context = _effective_rules_for_bot(bot_name, sender_user)
    if docs_context:
        system_prompt += (
            " Always verify feature and usage answers against the documentation context provided. "
            "If docs do not confirm a detail, say it is not documented/uncertain instead of guessing."
        )
    if rules_context:
        system_prompt += (
            " Follow the bot ruleset provided below. If a user asks what rules you follow, summarize these rules."
        )

    payload = {
        "model": model,
        "stream": False,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "system", "content": f"Documentation context:\n{docs_context}" if docs_context else "Documentation context unavailable."},
            {"role": "system", "content": f"Agent rules context:\n{rules_context[:5000]}" if rules_context else "Agent rules context unavailable."},
            {"role": "user", "content": f"User '{sender_user}' says: {user_text}"}
        ]
    }
    req = urllib.request.Request(
        f"{base_url}/api/chat",
        data=json.dumps(payload).encode('utf-8'),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode('utf-8', errors='replace')
        data = json.loads(raw)
        message = data.get("message", {}) if isinstance(data, dict) else {}
        content = message.get("content", "") if isinstance(message, dict) else ""
        content = str(content or "").strip()
        if not content:
            return None
        return content[:700]
    except Exception as e:
        print(f"Ollama bot reply failed for {bot_name}: {e}")
        return None

def _build_bot_tts_payload(bot_name, reply_text, request_text):
    if not bot_runtime_config.get('piper_enabled', False):
        return None
    reply = str(reply_text or "").strip()
    if not reply:
        return None
    # If users ask how they sound, provide a clear voice-preview response.
    asked_preview = any(
        k in str(request_text or "").lower()
        for k in ("how i sound", "how do i sound", "hear my voice", "my voice")
    )
    if asked_preview:
        reply += " I can preview my configured voice. To hear your own real voice, send a recording and I can play it back."
    audio = _synthesize_bot_tts(bot_name, reply)
    if not audio:
        return None
    return {
        "tts_audio_b64": audio,
        "tts_mime": "audio/wav",
        "tts_voice": _bot_voice_name(bot_name),
        "tts_engine": "piper",
    }

def _bot_voice_name(bot_name):
    voice = str(bot_voice_map.get(bot_name, "") or "").strip()
    if not voice:
        voice = str(bot_runtime_config.get('piper_default_voice', '') or '').strip()
    return voice or "default"

def _resolve_piper_model(bot_name):
    voice_model = _bot_voice_name(bot_name)
    models_dir = str(bot_runtime_config.get('piper_models_dir', './voices') or './voices').strip()
    if voice_model.endswith(".onnx"):
        if os.path.isabs(voice_model):
            return voice_model
        return os.path.join(models_dir, voice_model)
    if os.path.isabs(voice_model):
        return voice_model
    return os.path.join(models_dir, f"{voice_model}.onnx")

def _synthesize_bot_tts(bot_name, text):
    piper_bin = str(bot_runtime_config.get('piper_bin', '/usr/local/bin/piper') or '/usr/local/bin/piper').strip()
    model_path = _resolve_piper_model(bot_name)
    if not os.path.isfile(model_path):
        print(f"Piper model missing for {bot_name}: {model_path}")
        return None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as tmp:
            out_path = tmp.name
        cmd = [piper_bin, "--model", model_path, "--output_file", out_path]
        proc = subprocess.run(
            cmd,
            input=str(text).encode("utf-8"),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=max(3, int(bot_runtime_config.get('piper_timeout', 12) or 12)),
            check=False,
        )
        if proc.returncode != 0:
            print(f"Piper synthesis failed for {bot_name}: {proc.stderr.decode('utf-8', errors='ignore')[:300]}")
            return None
        with open(out_path, "rb") as f:
            audio = base64.b64encode(f.read()).decode("ascii")
        return audio
    except Exception as e:
        print(f"Piper synthesis error for {bot_name}: {e}")
        return None
    finally:
        try:
            if 'out_path' in locals() and os.path.exists(out_path):
                os.remove(out_path)
        except Exception:
            pass

def _schedule_restart(delay_seconds, requested_by="admin"):
    global restart_scheduled_for
    delay_seconds = max(1, int(delay_seconds))
    with restart_lock:
        restart_scheduled_for = time.time() + delay_seconds

    def _worker():
        global restart_scheduled_for
        print(f"Restart scheduled by {requested_by} in {delay_seconds} seconds.")
        broadcast_alert(f"The server is restarting in {delay_seconds} seconds.")
        time.sleep(delay_seconds)
        with restart_lock:
            restart_scheduled_for = None
        os.execv(sys.executable, [sys.executable] + sys.argv)

    threading.Thread(target=_worker, daemon=True).start()

def _upsert_bot_token(owner, bot_name):
    token = secrets.token_urlsafe(24)
    created = datetime.datetime.utcnow().isoformat()
    con = sqlite3.connect(DB)
    con.execute(
        "INSERT OR REPLACE INTO bot_tokens(owner, bot, token, created_at) VALUES(?,?,?,?)",
        (owner, bot_name, token, created),
    )
    con.commit()
    con.close()
    return token

def _revoke_bot_token(owner, bot_name):
    con = sqlite3.connect(DB)
    con.execute("DELETE FROM bot_tokens WHERE owner=? AND bot=?", (owner, bot_name))
    con.commit()
    con.close()

class EmailManager:
    @staticmethod
    def send_email(to_email, subject, body):
        if not smtp_config.get('enabled', False): return False
        try:
            msg = MIMEText(body)
            msg['Subject'] = subject
            msg['From'] = smtp_config['email']
            msg['To'] = to_email
            
            with smtplib.SMTP(smtp_config['server'], smtp_config['port']) as server:
                server.starttls()
                server.login(smtp_config['email'], smtp_config['password'])
                server.send_message(msg)
            return True
        except Exception as e:
            print(f"Failed to send email to {to_email}: {e}")
            return False

    @staticmethod
    def generate_code(length=6):
        if length <= 6:
            # Preserve a short user-facing code option while using CSPRNG.
            return ''.join(secrets.choice('0123456789') for _ in range(length))
        return secrets.token_hex(max(1, length // 2))

class FlexPBXManager:
    @staticmethod
    def send_sms(to_number, message):
        if not flexpbx_config.get('enabled', False):
            return False, "SMS module is not enabled."
        api_url = flexpbx_config.get('api_url', '').strip()
        api_token = flexpbx_config.get('api_token', '').strip()
        from_number = flexpbx_config.get('from_number', '').strip()
        if not api_url or not api_token:
            return False, "FlexPBX API is not configured."
        payload = urllib.parse.urlencode({
            "to": to_number,
            "from": from_number,
            "message": message,
        }).encode()
        req = urllib.request.Request(
            api_url,
            data=payload,
            method="POST",
            headers={
                "Authorization": f"Bearer {api_token}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                body = resp.read().decode(errors='ignore')
                if resp.status >= 200 and resp.status < 300:
                    return True, body
                return False, body or f"HTTP {resp.status}"
        except Exception as e:
            return False, str(e)

def get_admins():
    try:
        with open(ADMIN_FILE, 'r') as f: return {line.strip() for line in f if line.strip()}
    except FileNotFoundError: return set()

def broadcast_admin_status_change(username, is_admin):
    print(f"Broadcasting admin status change for {username}: {is_admin}")
    msg = json.dumps({"action": "admin_status_change", "user": username, "is_admin": is_admin}) + "\n"
    with lock:
        for sock in list(clients.values()):
            try: sock.sendall(msg.encode())
            except: pass

def add_admin(username):
    admins = get_admins()
    if username in admins: return
    admins.add(username)
    with open(ADMIN_FILE, 'w') as f:
        for admin in sorted(list(admins)): f.write(admin + '\n')
    print(f"User '{username}' added to admin list.")
    broadcast_admin_status_change(username, True)

def remove_admin(username):
    admins = get_admins()
    if username not in admins: return
    admins.discard(username)
    with open(ADMIN_FILE, 'w') as f:
        for admin in sorted(list(admins)): f.write(admin + '\n')
    print(f"User '{username}' removed from admin list.")
    broadcast_admin_status_change(username, False)

def broadcast_alert(message):
    print(f"Broadcasting alert: {message}")
    msg = json.dumps({"action": "server_alert", "message": message}) + "\n"
    with lock:
        for sock in list(clients.values()):
            try: sock.sendall(msg.encode())
            except: pass

def load_config():
    # Fix: interpolation=None prevents % characters in password from breaking the parser
    config = configparser.ConfigParser(interpolation=None)
    config.read('srv.conf')
    global smtp_config
    smtp_config = {
        'enabled': config.getboolean('smtp', 'enabled', fallback=False),
        'server': config.get('smtp', 'server', fallback=''),
        'port': config.getint('smtp', 'port', fallback=587),
        'email': config.get('smtp', 'email', fallback=''),
        'password': config.get('smtp', 'password', fallback='')
    }
    global flexpbx_config
    flexpbx_config = {
        'enabled': config.getboolean('flexpbx', 'enabled', fallback=False),
        'api_url': config.get('flexpbx', 'api_url', fallback=''),
        'api_token': config.get('flexpbx', 'api_token', fallback=''),
        'from_number': config.get('flexpbx', 'from_number', fallback=''),
    }
    enforce_blackfiles = config.getboolean('server', 'enforce_blackfile_list', fallback=False)
    global file_config
    file_config = {
        'size_limit': config.getint('server', 'size_limit', fallback=0),
        'blackfiles': [ext.strip().lower() for ext in config.get('server', 'blackfiles', fallback='').split(',') if ext.strip()] if enforce_blackfiles else [],
    }
    global shutdown_timeout
    shutdown_timeout = config.getint('server', 'shutdown_timeout', fallback=5)
    global max_status_length
    max_status_length = config.getint('server', 'max_status_length', fallback=50)
    global server_identity
    server_identity = config.get('server', 'name', fallback=config.get('server', 'host', fallback='Server'))
    global welcome_config
    welcome_config = {
        'enabled': config.getboolean('welcome', 'enabled', fallback=False),
        'pre_login': config.get('welcome', 'pre_login', fallback=''),
        'post_login': config.get('welcome', 'post_login', fallback=''),
    }
    global bot_usernames
    raw_bots = config.get('bots', 'names', fallback='assistant-bot,helper-bot')
    bot_usernames = {name.strip() for name in raw_bots.split(',') if name.strip()}
    if not bot_usernames:
        bot_usernames = {"assistant-bot", "helper-bot"}
    global bot_status_map
    bot_status_map = _parse_bot_map(config.get('bots', 'status_map', fallback=''))
    global bot_purpose_map
    bot_purpose_map = _parse_bot_map(config.get('bots', 'purpose_map', fallback=''))
    global bot_service_map
    bot_service_map = _parse_bot_map(config.get('bots', 'service_map', fallback=''))
    global bot_external_usernames
    raw_external = config.get('bots', 'external_names', fallback='')
    bot_external_usernames = {name.strip() for name in raw_external.split(',') if name.strip()}
    global allow_external_bot_contacts
    allow_external_bot_contacts = config.getboolean('bots', 'allow_external_bot_contacts', fallback=True)
    global bot_voice_map
    bot_voice_map = _parse_bot_map(config.get('bots', 'voice_map', fallback=''))
    global bot_rules_config
    bot_rules_config = {
        'agent_rules_zip_path': config.get('bots', 'agent_rules_zip_path', fallback='/home/devinecr/downloads/*.zip'),
        'agent_rules_file_path': config.get('bots', 'agent_rules_file_path', fallback=''),
    }
    _refresh_bot_rules()
    global bot_runtime_config
    bot_runtime_config = {
        'ollama_enabled': config.getboolean('bots', 'ollama_enabled', fallback=True),
        'ollama_url': config.get('bots', 'ollama_url', fallback='http://127.0.0.1:11434'),
        'ollama_model': config.get('bots', 'ollama_model', fallback='llama3.2'),
        'ollama_timeout': config.getint('bots', 'ollama_timeout', fallback=20),
        'ollama_system_prompt': config.get('bots', 'ollama_system_prompt', fallback=''),
        'piper_enabled': config.getboolean('bots', 'piper_enabled', fallback=False),
        'piper_bin': config.get('bots', 'piper_bin', fallback='/usr/local/bin/piper'),
        'piper_models_dir': config.get('bots', 'piper_models_dir', fallback='./voices'),
        'piper_default_voice': config.get('bots', 'piper_default_voice', fallback='en_US-lessac-medium'),
        'piper_timeout': config.getint('bots', 'piper_timeout', fallback=12),
    }
    return {
        'port': config.getint('server', 'port', fallback=5005),
        'certfile': config.get('server', 'certfile', fallback='server.crt'),
        'keyfile': config.get('server', 'keyfile', fallback='server.key'),
    }

def init_db():
    conn = sqlite3.connect(DB)
    cur = conn.cursor()
    # Check for columns and add if missing (Migration)
    cur.execute('''CREATE TABLE IF NOT EXISTS users (username TEXT PRIMARY KEY, password TEXT, banned_until TEXT, ban_reason TEXT)''')
    
    # Add new columns for email features if they don't exist
    existing_cols = [row[1] for row in cur.execute("PRAGMA table_info(users)")]
    if 'email' not in existing_cols: cur.execute("ALTER TABLE users ADD COLUMN email TEXT")
    if 'verification_code' not in existing_cols: cur.execute("ALTER TABLE users ADD COLUMN verification_code TEXT")
    if 'is_verified' not in existing_cols: cur.execute("ALTER TABLE users ADD COLUMN is_verified INTEGER DEFAULT 1") # Default 1 for old users
    if 'reset_code' not in existing_cols: cur.execute("ALTER TABLE users ADD COLUMN reset_code TEXT")

    cur.execute('''CREATE TABLE IF NOT EXISTS contacts (owner TEXT, contact TEXT, blocked INTEGER DEFAULT 0, PRIMARY KEY(owner, contact))''')
    cur.execute('''CREATE TABLE IF NOT EXISTS bot_tokens (owner TEXT, bot TEXT, token TEXT, created_at TEXT, PRIMARY KEY(owner, bot))''')
    cur.execute('''CREATE TABLE IF NOT EXISTS bot_rule_overrides (owner TEXT, bot TEXT, rules TEXT, updated_at TEXT, PRIMARY KEY(owner, bot))''')
    cur.execute('''CREATE TABLE IF NOT EXISTS group_policies (scope TEXT, group_name TEXT, policy_json TEXT, updated_by TEXT, updated_at TEXT, PRIMARY KEY(scope, group_name))''')
    cur.execute('''CREATE TABLE IF NOT EXISTS feature_policies (feature_key TEXT PRIMARY KEY, enabled INTEGER DEFAULT 1, ui_visible INTEGER DEFAULT 1, scope TEXT DEFAULT 'all', description TEXT, updated_by TEXT, updated_at TEXT)''')
    cur.execute('''CREATE TABLE IF NOT EXISTS feature_allow_users (feature_key TEXT, username TEXT, PRIMARY KEY(feature_key, username))''')
    cur.execute('''CREATE TABLE IF NOT EXISTS user_access_groups (group_name TEXT, username TEXT, PRIMARY KEY(group_name, username))''')
    cur.execute('''CREATE TABLE IF NOT EXISTS feature_allow_groups (feature_key TEXT, group_name TEXT, PRIMARY KEY(feature_key, group_name))''')
    cur.execute('''CREATE TABLE IF NOT EXISTS file_bans (username TEXT, file_type TEXT, until_date TEXT, reason TEXT, PRIMARY KEY(username, file_type))''')
    # Add file_type column if table was created with an older schema
    fb_cols = [row[1] for row in cur.execute("PRAGMA table_info(file_bans)")]
    if 'file_type' not in fb_cols: cur.execute("ALTER TABLE file_bans ADD COLUMN file_type TEXT")
    if 'until_date' not in fb_cols: cur.execute("ALTER TABLE file_bans ADD COLUMN until_date TEXT")
    if 'reason' not in fb_cols: cur.execute("ALTER TABLE file_bans ADD COLUMN reason TEXT")
    conn.commit()
    _seed_feature_defaults()
    conn.close()

def broadcast_contact_status(user, online):
    with lock:
        status_text = client_statuses.get(user, "offline") if online else "offline"
    msg = json.dumps({"action":"contact_status","user":user,"online":online,"status_text":status_text}) + "\n"
    with lock:
        for owner, sock in clients.items():
            db = sqlite3.connect(DB)
            r = db.execute("SELECT blocked FROM contacts WHERE owner=? AND contact=?", (owner, user)).fetchone()
            db.close()
            if r and r[0] == 0:
                try: sock.sendall(msg.encode())
                except: pass

def kick_if_banned(user):
    with lock: s = clients.get(user)
    if s:
        try: s.sendall(json.dumps({"action":"banned_kick"}).encode() + b"\n")
        except: pass
        s.close()
        with lock:
            clients.pop(user, None)
            client_statuses.pop(user, None)
        broadcast_contact_status(user, False)

def handle_client(cs, addr):
    sock = cs
    f = sock.makefile("r")
    user = None
    try:
        try:
            line = f.readline()
            if not line: return 
            req = json.loads(line)
        except (UnicodeDecodeError, json.JSONDecodeError): return

        action = req.get("action")

        # --- Welcome Message (pre-login safe endpoint) ---
        if action == "get_welcome":
            sock.sendall((json.dumps({
                "action": "welcome_info",
                "enabled": bool(welcome_config.get('enabled', False)),
                "pre_login": welcome_config.get('pre_login', '') if welcome_config.get('enabled', False) else '',
                "post_login": welcome_config.get('post_login', '') if welcome_config.get('enabled', False) else '',
            }) + "\n").encode())
            return
        
        # --- Create Account ---
        if action == "create_account":
            new_user = req.get("user")
            new_pass = req.get("pass")
            email = req.get("email", "")
            if not new_user or not new_pass: 
                sock.sendall((json.dumps({"action": "create_account_failed", "reason": "Missing fields."}) + "\n").encode())
                return
            
            con = sqlite3.connect(DB)
            row = con.execute("SELECT is_verified FROM users WHERE username=?", (new_user,)).fetchone()
            
            # Allow overwriting unverified users
            if row and (row[0] == 1 or not smtp_config['enabled']):
                sock.sendall((json.dumps({"action": "create_account_failed", "reason": "Username is already taken."}) + "\n").encode())
                con.close(); return
            
            # Logic: If SMTP is on, set verified=0, gen code, send email. Else verified=1.
            verified = 1 if not smtp_config['enabled'] else 0
            code = EmailManager.generate_code() if not verified else None
            
            if row: # Overwriting unverified
                con.execute("UPDATE users SET password=?, email=?, verification_code=?, is_verified=? WHERE username=?", (new_pass, email, code, verified, new_user))
            else:
                con.execute("INSERT INTO users(username, password, email, verification_code, is_verified) VALUES(?,?,?,?,?)", (new_user, new_pass, email, code, verified))
            con.commit()
            con.close()

            if not verified:
                if EmailManager.send_email(email, "Thrive Messenger - Verify Account", f"Your verification code is: {code}"):
                    sock.sendall((json.dumps({"action": "verify_pending"}) + "\n").encode())
                else:
                    # Fallback if email fails? For now just say success but maybe log it.
                    print("Failed to send verification email.")
                    sock.sendall((json.dumps({"action": "create_account_failed", "reason": "Could not send verification email."}) + "\n").encode())
            else:
                sock.sendall((json.dumps({"action": "create_account_success"}) + "\n").encode())
                if email:
                    EmailManager.send_email(
                        email,
                        "Welcome to Thrive Messenger",
                        f"Hi {new_user}, your account is ready to use on {server_identity}."
                    )
            return

        # --- Verify Account ---
        if action == "verify_account":
            u_ver = req.get("user")
            code_ver = req.get("code")
            con = sqlite3.connect(DB)
            row = con.execute("SELECT verification_code FROM users WHERE username=?", (u_ver,)).fetchone()
            if row and row[0] == code_ver:
                con.execute("UPDATE users SET is_verified=1, verification_code=NULL WHERE username=?", (u_ver,))
                con.commit(); con.close()
                sock.sendall(json.dumps({"status": "ok"}).encode() + b"\n")
            else:
                con.close()
                sock.sendall(json.dumps({"status": "error", "reason": "Invalid code"}).encode() + b"\n")
            return

        # --- Request Password Reset ---
        if action == "request_reset":
            ident = req.get("identifier")
            con = sqlite3.connect(DB)
            # Find user by email or username
            row = con.execute("SELECT username, email FROM users WHERE username=? OR email=?", (ident, ident)).fetchone()
            if row:
                t_user, t_email = row
                if t_email:
                    code = EmailManager.generate_code()
                    con.execute("UPDATE users SET reset_code=? WHERE username=?", (code, t_user))
                    con.commit()
                    EmailManager.send_email(t_email, "Thrive Messenger - Password Reset", f"Your password reset code is: {code}")
                    # Return OK even if email fails to prevent enumeration, mostly.
                    sock.sendall(json.dumps({"status": "ok", "user": t_user}).encode() + b"\n")
                else:
                    sock.sendall(json.dumps({"status": "error", "reason": "No email on file."}).encode() + b"\n")
            else:
                # Security: Don't reveal user existence? For this app, we'll just say ok to pretend.
                sock.sendall(json.dumps({"status": "ok"}).encode() + b"\n")
            con.close()
            return

        # --- Perform Password Reset ---
        if action == "reset_password":
            t_user = req.get("user")
            t_code = req.get("code")
            new_p = req.get("new_pass")
            con = sqlite3.connect(DB)
            row = con.execute("SELECT reset_code FROM users WHERE username=?", (t_user,)).fetchone()
            if row and row[0] == t_code and t_code:
                con.execute("UPDATE users SET password=?, reset_code=NULL WHERE username=?", (new_p, t_user))
                con.commit(); con.close()
                sock.sendall(json.dumps({"status": "ok"}).encode() + b"\n")
            else:
                con.close()
                sock.sendall(json.dumps({"status": "error", "reason": "Invalid code"}).encode() + b"\n")
            return

        if action != "login": 
            sock.sendall(b'{"status":"error","reason":"Expected login"}\n')
            return

        db = sqlite3.connect(DB)
        cur = db.cursor()

        input_user = str(req.get("user", "")).strip()
        if not input_user:
            sock.sendall(b'{"status":"error","reason":"Invalid credentials"}\n')
            db.close()
            return

        # Case-insensitive username login with canonical identity from DB.
        # If multiple usernames differ only by case, reject to avoid ambiguous auth.
        cur.execute(
            """
            SELECT username, password, banned_until, ban_reason, is_verified
            FROM users
            WHERE username = ? COLLATE NOCASE
            ORDER BY CASE WHEN username = ? THEN 0 ELSE 1 END, username
            LIMIT 2
            """,
            (input_user, input_user),
        )
        rows = cur.fetchall()
        if len(rows) > 1:
            sock.sendall(b'{"status":"error","reason":"Ambiguous username. Contact admin."}\n')
            db.close()
            return
        row = rows[0] if rows else None

        if not row or row[1] != req["pass"]:
            sock.sendall(b'{"status":"error","reason":"Invalid credentials"}\n')
            db.close()
            return

        user = row[0]
        bi, br, verified = row[2], row[3], row[4]
        
        if smtp_config['enabled'] and verified == 0:
            sock.sendall(b'{"status":"error","reason":"Account not verified. Please recreate account to verify."}\n')
            db.close()
            return

        if bi:
            until = datetime.datetime.strptime(bi, "%Y-%m-%d")
            if until > datetime.datetime.now(): 
                sock.sendall(json.dumps({"status":"banned","until":bi,"reason":br}).encode() + b"\n")
                db.close()
                return

        sock.sendall(b'{"status":"ok"}\n')
        with lock:
            clients[user] = sock
            client_statuses[user] = "online"

        admins = get_admins()
        rows = db.execute("SELECT contact,blocked FROM contacts WHERE owner=?", (user,)).fetchall()
        contacts = [{"user":c, "blocked":b, "online": _is_online_user(c), "is_admin": (c in admins), "status_text": _status_for_user(c)} for c,b in rows]
        sock.sendall((json.dumps({"action":"contact_list","contacts":contacts})+"\n").encode())
        _send_feature_caps(sock, user)
        db.close()
        
        broadcast_contact_status(user, True)
        
        for line in f:
            msg = json.loads(line)
            action = msg.get("action")
            def _deny_feature(feature_key, action_name=None):
                try:
                    sock.sendall((json.dumps({
                        "action": action_name or "feature_denied",
                        "ok": False,
                        "reason": f"Feature '{feature_key}' is not enabled for your account.",
                        "feature": feature_key
                    }) + "\n").encode())
                except Exception:
                    pass
            
            if action == "get_feature_caps":
                _send_feature_caps(sock, user)

            elif action == "get_feature_policies":
                if not _is_admin(user):
                    _deny_feature("admin_console", "feature_policy_result")
                    continue
                rows = []
                for fk in sorted(FEATURE_DEFAULTS.keys()):
                    p = _feature_policy_row(fk) or {}
                    rows.append(p)
                try:
                    sock.sendall((json.dumps({"action": "feature_policies", "ok": True, "policies": rows}) + "\n").encode())
                except Exception:
                    pass

            elif action == "set_feature_policy":
                if not _is_admin(user):
                    _deny_feature("admin_console", "feature_policy_result")
                    continue
                fk = str(msg.get("feature_key", "")).strip()
                if fk not in FEATURE_DEFAULTS:
                    try:
                        sock.sendall((json.dumps({"action": "feature_policy_result", "ok": False, "reason": "Unknown feature key."}) + "\n").encode())
                    except Exception:
                        pass
                    continue
                enabled = 1 if bool(msg.get("enabled", True)) else 0
                ui_visible = 1 if bool(msg.get("ui_visible", True)) else 0
                scope = str(msg.get("scope", "all") or "all").strip().lower()
                if not _is_valid_feature_scope(scope):
                    scope = "all"
                desc = str(msg.get("description", FEATURE_DEFAULTS[fk].get("description", "")) or "").strip()
                con = sqlite3.connect(DB)
                con.execute(
                    """
                    INSERT OR REPLACE INTO feature_policies(feature_key, enabled, ui_visible, scope, description, updated_by, updated_at)
                    VALUES(?,?,?,?,?,?,?)
                    """,
                    (fk, enabled, ui_visible, scope, desc, user, datetime.datetime.utcnow().isoformat()),
                )
                con.commit()
                con.close()
                _broadcast_feature_caps()
                try:
                    sock.sendall((json.dumps({"action": "feature_policy_result", "ok": True, "policy": _feature_policy_row(fk)}) + "\n").encode())
                except Exception:
                    pass

            elif action == "feature_allow_user_add":
                if not _is_admin(user):
                    _deny_feature("admin_console", "feature_allow_result")
                    continue
                fk = str(msg.get("feature_key", "")).strip()
                target_user = str(msg.get("username", "")).strip()
                if fk not in FEATURE_DEFAULTS or not target_user:
                    sock.sendall((json.dumps({"action": "feature_allow_result", "ok": False, "reason": "feature_key and username are required."}) + "\n").encode())
                    continue
                con = sqlite3.connect(DB)
                con.execute("INSERT OR IGNORE INTO feature_allow_users(feature_key, username) VALUES(?,?)", (fk, target_user))
                con.commit()
                con.close()
                _broadcast_feature_caps()
                sock.sendall((json.dumps({"action": "feature_allow_result", "ok": True, "feature_key": fk, "username": target_user}) + "\n").encode())

            elif action == "feature_allow_user_remove":
                if not _is_admin(user):
                    _deny_feature("admin_console", "feature_allow_result")
                    continue
                fk = str(msg.get("feature_key", "")).strip()
                target_user = str(msg.get("username", "")).strip()
                if fk not in FEATURE_DEFAULTS or not target_user:
                    sock.sendall((json.dumps({"action": "feature_allow_result", "ok": False, "reason": "feature_key and username are required."}) + "\n").encode())
                    continue
                con = sqlite3.connect(DB)
                con.execute("DELETE FROM feature_allow_users WHERE feature_key=? AND username=?", (fk, target_user))
                con.commit()
                con.close()
                _broadcast_feature_caps()
                sock.sendall((json.dumps({"action": "feature_allow_result", "ok": True, "feature_key": fk, "username": target_user}) + "\n").encode())

            elif action == "feature_access_group_add":
                if not _is_admin(user):
                    _deny_feature("admin_console", "feature_group_result")
                    continue
                gname = str(msg.get("group_name", "")).strip()
                target_user = str(msg.get("username", "")).strip()
                if not gname or not target_user:
                    sock.sendall((json.dumps({"action": "feature_group_result", "ok": False, "reason": "group_name and username are required."}) + "\n").encode())
                    continue
                con = sqlite3.connect(DB)
                con.execute("INSERT OR IGNORE INTO user_access_groups(group_name, username) VALUES(?,?)", (gname, target_user))
                con.commit()
                con.close()
                _broadcast_feature_caps()
                sock.sendall((json.dumps({"action": "feature_group_result", "ok": True, "group_name": gname, "username": target_user}) + "\n").encode())

            elif action == "feature_access_group_remove":
                if not _is_admin(user):
                    _deny_feature("admin_console", "feature_group_result")
                    continue
                gname = str(msg.get("group_name", "")).strip()
                target_user = str(msg.get("username", "")).strip()
                if not gname or not target_user:
                    sock.sendall((json.dumps({"action": "feature_group_result", "ok": False, "reason": "group_name and username are required."}) + "\n").encode())
                    continue
                con = sqlite3.connect(DB)
                con.execute("DELETE FROM user_access_groups WHERE group_name=? AND username=?", (gname, target_user))
                con.commit()
                con.close()
                _broadcast_feature_caps()
                sock.sendall((json.dumps({"action": "feature_group_result", "ok": True, "group_name": gname, "username": target_user}) + "\n").encode())

            elif action == "feature_allow_group_add":
                if not _is_admin(user):
                    _deny_feature("admin_console", "feature_allow_group_result")
                    continue
                fk = str(msg.get("feature_key", "")).strip()
                gname = str(msg.get("group_name", "")).strip()
                if fk not in FEATURE_DEFAULTS or not gname:
                    sock.sendall((json.dumps({"action": "feature_allow_group_result", "ok": False, "reason": "feature_key and group_name are required."}) + "\n").encode())
                    continue
                con = sqlite3.connect(DB)
                con.execute("INSERT OR IGNORE INTO feature_allow_groups(feature_key, group_name) VALUES(?,?)", (fk, gname))
                con.commit()
                con.close()
                _broadcast_feature_caps()
                sock.sendall((json.dumps({"action": "feature_allow_group_result", "ok": True, "feature_key": fk, "group_name": gname}) + "\n").encode())

            elif action == "feature_allow_group_remove":
                if not _is_admin(user):
                    _deny_feature("admin_console", "feature_allow_group_result")
                    continue
                fk = str(msg.get("feature_key", "")).strip()
                gname = str(msg.get("group_name", "")).strip()
                if fk not in FEATURE_DEFAULTS or not gname:
                    sock.sendall((json.dumps({"action": "feature_allow_group_result", "ok": False, "reason": "feature_key and group_name are required."}) + "\n").encode())
                    continue
                con = sqlite3.connect(DB)
                con.execute("DELETE FROM feature_allow_groups WHERE feature_key=? AND group_name=?", (fk, gname))
                con.commit()
                con.close()
                _broadcast_feature_caps()
                sock.sendall((json.dumps({"action": "feature_allow_group_result", "ok": True, "feature_key": fk, "group_name": gname}) + "\n").encode())

            elif action == "feature_access_groups_list":
                if not _is_admin(user):
                    _deny_feature("admin_console", "feature_group_list")
                    continue
                target_user = str(msg.get("username", "")).strip()
                if not target_user:
                    sock.sendall((json.dumps({"action": "feature_group_list", "ok": False, "reason": "username is required."}) + "\n").encode())
                    continue
                con = sqlite3.connect(DB)
                groups = [r[0] for r in con.execute("SELECT group_name FROM user_access_groups WHERE username=? ORDER BY group_name", (target_user,)).fetchall()]
                con.close()
                sock.sendall((json.dumps({"action": "feature_group_list", "ok": True, "username": target_user, "groups": groups}) + "\n").encode())

            elif action == "add_contact":
                contact_to_add = msg["to"]
                if contact_to_add == user: 
                    reason = "You cannot add yourself as a contact."
                    sock.sendall((json.dumps({"action": "add_contact_failed", "reason": reason}) + "\n").encode())
                    continue
                con = sqlite3.connect(DB)
                exists = con.execute("SELECT 1 FROM users WHERE username=?", (contact_to_add,)).fetchone()
                is_bot = _is_registered_bot(contact_to_add)
                if is_bot and not _can_user_use_feature(user, "bots"):
                    reason = "Bot contacts are disabled for your account."
                    sock.sendall((json.dumps({"action": "add_contact_failed", "reason": reason}) + "\n").encode())
                    con.close()
                    continue
                if not exists and not is_bot:
                    reason = f"User '{contact_to_add}' does not exist."
                    sock.sendall((json.dumps({
                        "action": "add_contact_failed",
                        "reason": reason,
                        "suggest_invite": True,
                        "invite_methods": [
                            m for m, ok in [("email", smtp_config.get("enabled", False)), ("sms", flexpbx_config.get("enabled", False))] if ok
                        ],
                    }) + "\n").encode())
                else:
                    con.execute("INSERT OR IGNORE INTO contacts(owner,contact) VALUES(?,?)", (user, contact_to_add))
                    con.commit()
                    is_online = _is_online_user(contact_to_add)
                    contact_status_text = _status_for_user(contact_to_add)
                    admins = get_admins()
                    if is_bot:
                        _ensure_admin_bot_rules_seed(user, contact_to_add)
                    rules_text = _effective_rules_for_bot(contact_to_add, user) if is_bot else ""
                    contact_data = {
                        "user": contact_to_add,
                        "blocked": 0,
                        "online": is_online,
                        "is_admin": contact_to_add in admins,
                        "status_text": contact_status_text,
                        "is_bot": bool(is_bot),
                        "bot_origin": "local" if _is_virtual_bot(contact_to_add) else ("external" if is_bot else "user"),
                        "bot_rules_available": bool(rules_text),
                        "bot_rules_preview": rules_text[:1000] if rules_text else "",
                        "bot_rules_editable": bool(is_bot and _is_admin(user)),
                    }
                    if _is_virtual_bot(contact_to_add) and str(contact_to_add).lower() == "openclaw-bot":
                        token = _upsert_bot_token(user, contact_to_add)
                        contact_data["bot_auth_token"] = token
                        contact_data["bot_auth_type"] = "openclaw"
                    sock.sendall((json.dumps({"action": "add_contact_success", "contact": contact_data}) + "\n").encode())
                con.close()

            elif action == "invite_user":
                target_user = str(msg.get("username", "")).strip()
                method = str(msg.get("method", "email")).strip().lower()
                target = str(msg.get("target", "")).strip()
                include_link = bool(msg.get("include_link", True))
                if not target_user or not target:
                    sock.sendall((json.dumps({
                        "action": "invite_result",
                        "ok": False,
                        "method": method,
                        "target": target,
                        "reason": "Invite target username and destination are required."
                    }) + "\n").encode())
                    continue
                if method not in ("email", "sms"):
                    method = "email" if "@" in target else "sms"
                invite_text = f"{user} invited you to join Thrive Messenger on {server_identity}."
                if include_link:
                    invite_text += " Visit https://im.tappedin.fm/ for setup and sign-in."
                ok = False
                reason = "Unsupported invite method."
                if method == "email":
                    ok = EmailManager.send_email(target, "You're invited to Thrive Messenger", invite_text)
                    reason = "Invite email sent." if ok else "Email delivery is unavailable or failed."
                elif method == "sms":
                    ok, sms_reason = FlexPBXManager.send_sms(target, invite_text)
                    reason = "Invite SMS sent." if ok else sms_reason
                sock.sendall((json.dumps({
                    "action": "invite_result",
                    "ok": ok,
                    "method": method,
                    "target": target,
                    "reason": reason
                }) + "\n").encode())
                
            elif action in ("block_contact","unblock_contact"):
                flag = 1 if action=="block_contact" else 0
                con = sqlite3.connect(DB)
                con.execute("UPDATE contacts SET blocked=? WHERE owner=? AND contact=?", (flag,user,msg["to"]))
                con.commit()
                con.close()
                
            elif action == "delete_contact":
                deleted_name = msg["to"]
                con = sqlite3.connect(DB)
                con.execute("DELETE FROM contacts WHERE owner=? AND contact=?", (user,deleted_name))
                con.commit()
                con.close()
                if _is_virtual_bot(deleted_name):
                    _revoke_bot_token(user, deleted_name)
                    try:
                        sock.sendall((json.dumps({
                            "action": "bot_token_revoked",
                            "bot": deleted_name
                        }) + "\n").encode())
                    except Exception:
                        pass
                
            elif action == "admin_cmd":
                if not _can_user_use_feature(user, "admin_console"):
                    response = "Error: Admin console is disabled for your account."
                elif user not in get_admins(): 
                    response = "Error: You are not authorized to use admin commands."
                else:
                    cmd_parts = msg.get("cmd", "").split()
                    command = cmd_parts[0].lower() if cmd_parts else ""
                    if command == "exit" and len(cmd_parts) == 1:
                        print(f"Shutdown initiated by admin: {user}")
                        broadcast_alert(f"The server is shutting down in {shutdown_timeout} seconds.")
                        time.sleep(shutdown_timeout)
                        os._exit(0)
                    elif command == "restart" and len(cmd_parts) == 1:
                        response = f"Server is restarting in {shutdown_timeout} seconds..."
                        _schedule_restart(shutdown_timeout, requested_by=user)
                    elif command == "alert" and len(cmd_parts) >= 2:
                        alert_message = " ".join(cmd_parts[1:])
                        broadcast_alert(alert_message)
                        response = "Alert sent to all online users."
                    elif command == "create" and len(cmd_parts) in (3, 4):
                        email = cmd_parts[3] if len(cmd_parts) == 4 else ""
                        if handle_create(cmd_parts[1], cmd_parts[2], email):
                            response = f"User '{cmd_parts[1]}' created."
                        else:
                            response = f"Error: Username '{cmd_parts[1]}' is already taken."
                    elif command == "ban" and len(cmd_parts) >= 4: 
                        handle_ban(cmd_parts[1], cmd_parts[2], " ".join(cmd_parts[3:]))
                        response = f"User '{cmd_parts[1]}' banned."
                    elif command == "unban" and len(cmd_parts) == 2: 
                        handle_unban(cmd_parts[1])
                        response = f"User '{cmd_parts[1]}' unbanned."
                    elif command == "del" and len(cmd_parts) == 2: 
                        handle_delete(cmd_parts[1])
                        response = f"User '{cmd_parts[1]}' deleted."
                    elif command == "admin" and len(cmd_parts) == 2: 
                        add_admin(cmd_parts[1])
                        response = f"User '{cmd_parts[1]}' is now an admin."
                    elif command == "unadmin" and len(cmd_parts) == 2:
                        remove_admin(cmd_parts[1])
                        response = f"User '{cmd_parts[1]}' is no longer an admin."
                    elif command == "banfile" and len(cmd_parts) >= 4:
                        date_str = None
                        try:
                            datetime.datetime.strptime(cmd_parts[3], "%m/%d/%Y")
                            date_str = cmd_parts[3]
                            reason = " ".join(cmd_parts[4:]) if len(cmd_parts) >= 5 else "No reason given"
                        except (ValueError, IndexError):
                            reason = " ".join(cmd_parts[3:])
                        handle_banfile(cmd_parts[1], cmd_parts[2], date_str, reason)
                        if date_str:
                            response = f"User '{cmd_parts[1]}' banned from sending '{cmd_parts[2]}' files until {date_str}."
                        else:
                            response = f"User '{cmd_parts[1]}' permanently banned from sending '{cmd_parts[2]}' files."
                    elif command == "unbanfile" and len(cmd_parts) >= 2:
                        file_type = cmd_parts[2] if len(cmd_parts) >= 3 else None
                        handle_unbanfile(cmd_parts[1], file_type)
                        if file_type:
                            response = f"User '{cmd_parts[1]}' file ban for '{file_type}' removed."
                        else:
                            response = f"All file bans for user '{cmd_parts[1]}' removed."
                    elif command == "gpolicy" and len(cmd_parts) >= 2:
                        sub = cmd_parts[1].lower()
                        if sub == "show":
                            # /gpolicy show [group_name]
                            target_group = cmd_parts[2] if len(cmd_parts) >= 3 else "__global__"
                            scope = "group" if target_group != "__global__" else "global"
                            policy = _fetch_group_policy(scope=scope, group_name=target_group)
                            response = json.dumps({
                                "scope": scope,
                                "group": target_group,
                                "policy": policy
                            }, ensure_ascii=False)
                        elif sub == "set" and len(cmd_parts) >= 4:
                            # /gpolicy set key value [group_name]
                            key = cmd_parts[2]
                            value = cmd_parts[3]
                            target_group = cmd_parts[4] if len(cmd_parts) >= 5 else "__global__"
                            scope = "group" if target_group != "__global__" else "global"
                            merged = _upsert_group_policy(scope=scope, group_name=target_group, updates={key: value}, updated_by=user)
                            response = f"Group policy updated for {scope}:{target_group}. {key}={merged.get(key)}"
                        elif sub == "reset":
                            # /gpolicy reset [group_name]
                            target_group = cmd_parts[2] if len(cmd_parts) >= 3 else "__global__"
                            scope = "group" if target_group != "__global__" else "global"
                            _reset_group_policy(scope=scope, group_name=target_group)
                            response = f"Group policy reset for {scope}:{target_group}."
                        elif sub == "keys":
                            response = json.dumps(_policy_schema_payload(), ensure_ascii=False)
                        else:
                            response = "Error: gpolicy syntax: /gpolicy show [group], /gpolicy set <key> <value> [group], /gpolicy reset [group], /gpolicy keys"
                    else:
                        response = "Error: Unknown command or incorrect syntax."
                try: sock.sendall((json.dumps({"action":"admin_response", "response": response})+"\n").encode())
                except: pass

            elif action == "schedule_restart":
                if not _can_user_use_feature(user, "admin_console"):
                    try:
                        sock.sendall((json.dumps({"action": "admin_response", "response": "Error: Admin console is disabled for your account."}) + "\n").encode())
                    except Exception:
                        pass
                    continue
                if user not in get_admins():
                    try:
                        sock.sendall((json.dumps({"action": "admin_response", "response": "Error: You are not authorized to schedule restarts."}) + "\n").encode())
                    except Exception:
                        pass
                    continue
                try:
                    delay = int(msg.get("seconds", shutdown_timeout))
                except Exception:
                    delay = shutdown_timeout
                _schedule_restart(delay, requested_by=user)
                try:
                    sock.sendall((json.dumps({"action": "admin_response", "response": f"Server restart scheduled in {max(1, delay)} seconds."}) + "\n").encode())
                except Exception:
                    pass
                
            elif action == "server_info":
                con = sqlite3.connect(DB)
                total_users = con.execute("SELECT COUNT(*) FROM users").fetchone()[0]
                con.close()
                with lock:
                    online_count = len(clients)
                    online_admins = sum(1 for uname in clients.keys() if uname in get_admins())
                uptime_seconds = int(max(0, time.time() - server_started_at))
                info = {
                    "action": "server_info_response",
                    "port": server_port,
                    "ssl": use_ssl,
                    "total_users": total_users,
                    "online_users": online_count,
                    "online_admin_users": online_admins,
                    "uptime_seconds": uptime_seconds,
                    "size_limit": file_config.get('size_limit', 0),
                    "blackfiles": file_config.get('blackfiles', []),
                    "max_status_length": max_status_length
                }
                try: sock.sendall((json.dumps(info) + "\n").encode())
                except: pass

            elif action == "user_directory":
                con = sqlite3.connect(DB)
                all_users = con.execute("SELECT username FROM users WHERE is_verified=1").fetchall()
                user_contacts = {row[0]: row[1] for row in con.execute("SELECT contact, blocked FROM contacts WHERE owner=?", (user,)).fetchall()}
                con.close()
                admins = get_admins()
                directory = []
                include_bots = _can_user_use_feature(user, "bots")
                known = {uname for (uname,) in all_users}
                extra = set()
                if include_bots:
                    extra = set(bot_usernames) | set(bot_external_usernames)
                for uname in sorted(known | extra):
                    directory.append({
                        "user": uname,
                        "online": _is_online_user(uname),
                        "status_text": _status_for_user(uname),
                        "is_admin": uname in admins,
                        "is_contact": uname in user_contacts,
                        "is_blocked": user_contacts.get(uname, 0) == 1,
                        "server": server_identity,
                        "is_bot": _is_registered_bot(uname),
                        "bot_origin": "local" if _is_virtual_bot(uname) else ("external" if _is_registered_bot(uname) else "user")
                    })
                try: sock.sendall((json.dumps({"action": "user_directory_response", "users": directory}) + "\n").encode())
                except: pass

            elif action == "get_bot_rules":
                if not _can_user_use_feature(user, "bot_rules"):
                    try:
                        sock.sendall((json.dumps({"action": "bot_rules", "ok": False, "reason": "Bot rules are disabled for your account."}) + "\n").encode())
                    except Exception:
                        pass
                    continue
                bot_name = str(msg.get("bot", "")).strip()
                if not bot_name or not _is_registered_bot(bot_name):
                    try:
                        sock.sendall((json.dumps({"action": "bot_rules", "ok": False, "reason": "Unknown bot."}) + "\n").encode())
                    except Exception:
                        pass
                    continue
                if _is_admin(user):
                    _ensure_admin_bot_rules_seed(user, bot_name)
                rules_text = _effective_rules_for_bot(bot_name, user)
                try:
                    sock.sendall((json.dumps({
                        "action": "bot_rules",
                        "ok": True,
                        "bot": bot_name,
                        "rules": rules_text,
                        "rules_available": bool(rules_text),
                        "editable": bool(_is_admin(user)),
                        "scope": "admin_override" if (_is_admin(user) and bool(_get_admin_bot_rules(user, bot_name))) else "global",
                    }) + "\n").encode())
                except Exception:
                    pass

            elif action == "get_group_policy":
                if not _can_user_use_feature(user, "group_policy"):
                    try:
                        sock.sendall((json.dumps({"action": "group_policy", "ok": False, "reason": "Group policy is disabled for your account."}) + "\n").encode())
                    except Exception:
                        pass
                    continue
                group_name = str(msg.get("group", "") or "").strip()
                scope = "group" if group_name else "global"
                policy = _fetch_group_policy(scope=scope, group_name=group_name or "__global__")
                payload = {
                    "action": "group_policy",
                    "ok": True,
                    "scope": scope,
                    "group": group_name or "__global__",
                    "policy": policy,
                    "schema": _policy_schema_payload(),
                    "editable": bool(user in get_admins()),
                }
                try:
                    sock.sendall((json.dumps(payload) + "\n").encode())
                except Exception:
                    pass

            elif action == "set_group_policy":
                if not _can_user_use_feature(user, "group_policy"):
                    try:
                        sock.sendall((json.dumps({"action": "group_policy_update", "ok": False, "reason": "Group policy is disabled for your account."}) + "\n").encode())
                    except Exception:
                        pass
                    continue
                if user not in get_admins():
                    try:
                        sock.sendall((json.dumps({"action": "group_policy_update", "ok": False, "reason": "Admin only."}) + "\n").encode())
                    except Exception:
                        pass
                    continue
                group_name = str(msg.get("group", "") or "").strip()
                scope = "group" if group_name else "global"
                updates = msg.get("updates", {})
                if not isinstance(updates, dict):
                    try:
                        sock.sendall((json.dumps({"action": "group_policy_update", "ok": False, "reason": "Invalid updates payload."}) + "\n").encode())
                    except Exception:
                        pass
                    continue
                try:
                    merged = _upsert_group_policy(scope=scope, group_name=group_name or "__global__", updates=updates, updated_by=user)
                    sock.sendall((json.dumps({
                        "action": "group_policy_update",
                        "ok": True,
                        "scope": scope,
                        "group": group_name or "__global__",
                        "policy": merged
                    }) + "\n").encode())
                except Exception as e:
                    try:
                        sock.sendall((json.dumps({"action": "group_policy_update", "ok": False, "reason": str(e)}) + "\n").encode())
                    except Exception:
                        pass

            elif action == "reset_group_policy":
                if not _can_user_use_feature(user, "group_policy"):
                    try:
                        sock.sendall((json.dumps({"action": "group_policy_update", "ok": False, "reason": "Group policy is disabled for your account."}) + "\n").encode())
                    except Exception:
                        pass
                    continue
                if user not in get_admins():
                    try:
                        sock.sendall((json.dumps({"action": "group_policy_update", "ok": False, "reason": "Admin only."}) + "\n").encode())
                    except Exception:
                        pass
                    continue
                group_name = str(msg.get("group", "") or "").strip()
                scope = "group" if group_name else "global"
                _reset_group_policy(scope=scope, group_name=group_name or "__global__")
                policy = _fetch_group_policy(scope=scope, group_name=group_name or "__global__")
                try:
                    sock.sendall((json.dumps({
                        "action": "group_policy_update",
                        "ok": True,
                        "scope": scope,
                        "group": group_name or "__global__",
                        "policy": policy
                    }) + "\n").encode())
                except Exception:
                    pass

            elif action == "set_bot_rules":
                if not _can_user_use_feature(user, "bot_rules"):
                    try:
                        sock.sendall((json.dumps({"action": "bot_rules_update", "ok": False, "reason": "Bot rules are disabled for your account."}) + "\n").encode())
                    except Exception:
                        pass
                    continue
                if not _is_admin(user):
                    try:
                        sock.sendall((json.dumps({"action": "bot_rules_update", "ok": False, "reason": "Admin only."}) + "\n").encode())
                    except Exception:
                        pass
                    continue
                bot_name = str(msg.get("bot", "")).strip()
                rules_text = str(msg.get("rules", "") or "").strip()
                if not bot_name or not _is_registered_bot(bot_name):
                    try:
                        sock.sendall((json.dumps({"action": "bot_rules_update", "ok": False, "reason": "Unknown bot."}) + "\n").encode())
                    except Exception:
                        pass
                    continue
                if len(rules_text) > 60000:
                    rules_text = rules_text[:60000]
                ok = _set_admin_bot_rules(user, bot_name, rules_text)
                try:
                    sock.sendall((json.dumps({
                        "action": "bot_rules_update",
                        "ok": bool(ok),
                        "bot": bot_name,
                        "scope": "admin_override",
                        "rules_available": bool(rules_text),
                    }) + "\n").encode())
                except Exception:
                    pass

            elif action == "reset_bot_rules":
                if not _can_user_use_feature(user, "bot_rules"):
                    try:
                        sock.sendall((json.dumps({"action": "bot_rules_update", "ok": False, "reason": "Bot rules are disabled for your account."}) + "\n").encode())
                    except Exception:
                        pass
                    continue
                if not _is_admin(user):
                    try:
                        sock.sendall((json.dumps({"action": "bot_rules_update", "ok": False, "reason": "Admin only."}) + "\n").encode())
                    except Exception:
                        pass
                    continue
                bot_name = str(msg.get("bot", "")).strip()
                if not bot_name or not _is_registered_bot(bot_name):
                    try:
                        sock.sendall((json.dumps({"action": "bot_rules_update", "ok": False, "reason": "Unknown bot."}) + "\n").encode())
                    except Exception:
                        pass
                    continue
                _clear_admin_bot_rules(user, bot_name)
                _ensure_admin_bot_rules_seed(user, bot_name)
                try:
                    sock.sendall((json.dumps({
                        "action": "bot_rules_update",
                        "ok": True,
                        "bot": bot_name,
                        "scope": "global_seeded",
                        "rules_available": bool(_effective_rules_for_bot(bot_name, user)),
                    }) + "\n").encode())
                except Exception:
                    pass

            elif action == "group_call_list":
                if not _can_user_use_feature(user, "group_call"):
                    _deny_feature("group_call", "group_call_list_response")
                    continue
                rows = []
                with group_call_lock:
                    for g in sorted(group_call_sessions.keys()):
                        snap = _group_call_snapshot(g)
                        rows.append(snap)
                try:
                    sock.sendall((json.dumps({"action": "group_call_list_response", "calls": rows}) + "\n").encode())
                except Exception:
                    pass

            elif action == "group_call_join":
                if not _can_user_use_feature(user, "group_call"):
                    _deny_feature("group_call", "group_call_result")
                    continue
                group = str(msg.get("group", "")).strip()
                mode = str(msg.get("mode", "voice") or "voice").strip().lower()
                if mode not in ("voice", "video"):
                    mode = "voice"
                if not group:
                    try:
                        sock.sendall((json.dumps({"action": "group_call_result", "ok": False, "reason": "Missing group name."}) + "\n").encode())
                    except Exception:
                        pass
                    continue
                # Enforce global/group call policy when configured.
                policy = _fetch_group_policy(scope="global", group_name="__global__")
                if mode == "voice" and not policy.get("allow_group_voice", True):
                    sock.sendall((json.dumps({"action": "group_call_result", "ok": False, "group": group, "reason": "Group voice calls are disabled."}) + "\n").encode())
                    continue
                if mode == "video" and not policy.get("allow_group_video", True):
                    sock.sendall((json.dumps({"action": "group_call_result", "ok": False, "group": group, "reason": "Group video calls are disabled."}) + "\n").encode())
                    continue
                with group_call_lock:
                    data = group_call_sessions.setdefault(group, {"mode": mode, "participants": set()})
                    if data.get("mode") != mode and data.get("participants"):
                        mode = data.get("mode", "voice")
                    data["mode"] = mode
                    max_voice = int(policy.get("max_group_concurrent_voice", 40) or 40)
                    if len(data["participants"]) >= max_voice and user not in data["participants"]:
                        sock.sendall((json.dumps({"action": "group_call_result", "ok": False, "group": group, "reason": "Group call participant limit reached."}) + "\n").encode())
                        continue
                    data["participants"].add(user)
                payload = {"action": "group_call_event", "event": "join", "by": user}
                payload.update(_group_call_snapshot(group))
                _group_call_broadcast(group, payload)
                try:
                    sock.sendall((json.dumps({"action": "group_call_result", "ok": True, "group": group}) + "\n").encode())
                except Exception:
                    pass

            elif action == "group_call_leave":
                if not _can_user_use_feature(user, "group_call"):
                    _deny_feature("group_call", "group_call_result")
                    continue
                group = str(msg.get("group", "")).strip()
                if not group:
                    continue
                with group_call_lock:
                    data = group_call_sessions.get(group)
                    if not data:
                        pass
                    else:
                        data.get("participants", set()).discard(user)
                        if not data.get("participants"):
                            group_call_sessions.pop(group, None)
                payload = {"action": "group_call_event", "event": "leave", "by": user}
                payload.update(_group_call_snapshot(group))
                _group_call_broadcast(group, payload, exclude=user)
                try:
                    sock.sendall((json.dumps({"action": "group_call_result", "ok": True, "group": group}) + "\n").encode())
                except Exception:
                    pass

            elif action == "group_call_signal":
                if not _can_user_use_feature(user, "group_call"):
                    _deny_feature("group_call", "group_call_signal_result")
                    continue
                group = str(msg.get("group", "")).strip()
                target = str(msg.get("to", "")).strip()
                signal_type = str(msg.get("signal_type", "")).strip()
                signal_data = msg.get("data", {})
                if not group or not target:
                    continue
                with group_call_lock:
                    data = group_call_sessions.get(group) or {}
                    participants = set(data.get("participants", set()))
                if user not in participants or target not in participants:
                    try:
                        sock.sendall((json.dumps({"action": "group_call_signal_result", "ok": False, "reason": "Call participant not found."}) + "\n").encode())
                    except Exception:
                        pass
                    continue
                with lock:
                    target_sock = clients.get(target)
                if not target_sock:
                    sock.sendall((json.dumps({"action": "group_call_signal_result", "ok": False, "reason": f"{target} is offline."}) + "\n").encode())
                    continue
                try:
                    target_sock.sendall((json.dumps({
                        "action": "group_call_signal",
                        "group": group,
                        "from": user,
                        "signal_type": signal_type,
                        "data": signal_data
                    }) + "\n").encode())
                    sock.sendall((json.dumps({"action": "group_call_signal_result", "ok": True, "group": group, "to": target}) + "\n").encode())
                except Exception:
                    sock.sendall((json.dumps({"action": "group_call_signal_result", "ok": False, "reason": "Signal relay failed."}) + "\n").encode())

            elif action == "msg":
                to, frm = msg["to"], msg["from"]
                if _is_registered_bot(to) and not _can_user_use_feature(user, "bots"):
                    sock.sendall(json.dumps({"action": "msg_failed", "to": to, "reason": "Bot messaging is disabled for your account."}).encode() + b"\n")
                    continue
                con = sqlite3.connect(DB)
                recipient_has_blocked = con.execute("SELECT blocked FROM contacts WHERE owner=? AND contact=?", (to, frm)).fetchone()
                sender_has_blocked = con.execute("SELECT blocked FROM contacts WHERE owner=? AND contact=?", (frm, to)).fetchone()
                con.close()
                
                with lock: sock_to = clients.get(to)
                reason = None
                if recipient_has_blocked and recipient_has_blocked[0] == 1:
                    reason = f"Message couldn't be sent because {to} has you blocked."
                elif sender_has_blocked and sender_has_blocked[0] == 1: 
                    reason = "You have blocked this contact."
                elif _maybe_send_bot_reply(sock, frm, to, msg.get("msg", "")):
                    reason = None
                elif not sock_to: 
                    reason = f"{to} is offline."
                else:
                    try: 
                        sock_to.sendall((json.dumps(msg)+"\n").encode())
                        reason = None
                    except: pass
                if reason: 
                    sock.sendall(json.dumps({"action": "msg_failed", "to": to, "reason": reason}).encode() + b"\n")

            elif action == "typing":
                to = msg.get("to")
                typing = bool(msg.get("typing", False))
                if not to:
                    continue
                with lock:
                    sock_to = clients.get(to)
                if sock_to:
                    try:
                        sock_to.sendall((json.dumps({"action": "typing", "from": user, "typing": typing}) + "\n").encode())
                    except Exception:
                        pass
                    
            elif action == "file_offer":
                to = msg["to"]
                files = msg.get("files", [])
                # Reject any filename containing a path separator (OS-independent check)
                bad = next((f["filename"] for f in files if '/' in f["filename"] or '\\' in f["filename"]), None)
                if bad:
                    sock.sendall((json.dumps({"action": "file_offer_failed", "to": to, "reason": f"Invalid filename: '{bad}'"}) + "\n").encode())
                    continue

                # Check if recipient is online
                with lock: sock_to = clients.get(to)
                if not sock_to:
                    sock.sendall((json.dumps({"action": "file_offer_failed", "to": to, "reason": f"{to} is offline."}) + "\n").encode())
                    continue

                # Check if recipient has blocked sender
                con = sqlite3.connect(DB)
                recipient_has_blocked = con.execute("SELECT blocked FROM contacts WHERE owner=? AND contact=?", (to, user)).fetchone()
                con.close()
                if recipient_has_blocked and recipient_has_blocked[0] == 1:
                    sock.sendall((json.dumps({"action": "file_offer_failed", "to": to, "reason": f"{to} has you blocked."}) + "\n").encode())
                    continue

                # Check each file against server rules
                limit = file_config.get('size_limit', 0)
                blackfiles = file_config.get('blackfiles', [])
                blocked = False
                for finfo in files:
                    fname = finfo["filename"]
                    fsize = finfo.get("size", 0)
                    file_ext = fname.rsplit('.', 1)[-1].lower() if '.' in fname else ''
                    if file_ext in blackfiles:
                        sock.sendall((json.dumps({"action": "file_offer_failed", "to": to, "reason": f"File type '.{file_ext}' is not allowed by the server."}) + "\n").encode())
                        blocked = True; break
                    if limit > 0 and fsize > limit:
                        sock.sendall((json.dumps({"action": "file_offer_failed", "to": to, "reason": f"File '{fname}' exceeds server size limit of {limit} bytes."}) + "\n").encode())
                        blocked = True; break
                    ban_reason = check_file_ban(user, file_ext)
                    if ban_reason is None and file_ext:
                        ban_reason = check_file_ban(user, '*')
                    if ban_reason:
                        sock.sendall((json.dumps({"action": "file_offer_failed", "to": to, "reason": f"You are banned from sending '{fname}': {ban_reason}"}) + "\n").encode())
                        blocked = True; break
                if blocked: continue

                # All checks passed, create transfer and forward offer
                client_transfer_id = msg.get("transfer_id", "")  # echo back so sender can locate its pending files
                transfer_id = str(uuid.uuid4())  # always server-generated; never trust client-supplied ID
                with transfer_lock:
                    pending_transfers[transfer_id] = {"from": user, "to": to, "files": files, "client_transfer_id": client_transfer_id}

                try:
                    sock_to.sendall((json.dumps({"action": "file_offer", "from": user, "files": files, "transfer_id": transfer_id}) + "\n").encode())
                except:
                    sock.sendall((json.dumps({"action": "file_offer_failed", "to": to, "reason": f"Failed to send offer to {to}."}) + "\n").encode())
                    with transfer_lock: pending_transfers.pop(transfer_id, None)

            elif action == "file_accept":
                transfer_id = msg["transfer_id"]
                with transfer_lock: transfer = pending_transfers.get(transfer_id)
                if not transfer: continue
                sender = transfer["from"]
                with lock: sock_sender = clients.get(sender)
                if sock_sender:
                    try: sock_sender.sendall((json.dumps({"action": "file_accepted", "transfer_id": transfer_id, "client_transfer_id": transfer.get("client_transfer_id", ""), "to": transfer["to"], "files": transfer["files"]}) + "\n").encode())
                    except: pass

            elif action == "file_decline":
                transfer_id = msg["transfer_id"]
                with transfer_lock: transfer = pending_transfers.pop(transfer_id, None)
                if not transfer: continue
                sender = transfer["from"]
                with lock: sock_sender = clients.get(sender)
                if sock_sender:
                    try: sock_sender.sendall((json.dumps({"action": "file_declined", "transfer_id": transfer_id, "client_transfer_id": transfer.get("client_transfer_id", ""), "to": transfer["to"], "files": transfer["files"]}) + "\n").encode())
                    except: pass

            elif action == "file_data":
                transfer_id = msg["transfer_id"]
                with transfer_lock: transfer = pending_transfers.pop(transfer_id, None)
                if not transfer: continue
                recipient = transfer["to"]
                with lock: sock_to = clients.get(recipient)
                if sock_to:
                    # Use the filenames stored at offer time (already validated); ignore client-supplied names in data packet
                    name_map = {f["filename"]: f["filename"] for f in transfer["files"]}
                    safe_files = [dict(fd, filename=name_map.get(fd["filename"], fd["filename"])) for fd in msg["files"]
                                  if '/' not in fd["filename"] and '\\' not in fd["filename"]]
                    try: sock_to.sendall((json.dumps({"action": "file_data", "from": transfer["from"], "files": safe_files}) + "\n").encode())
                    except: pass

            elif action == "set_status":
                status_text = msg.get("status_text", "online")[:max_status_length]
                with lock: client_statuses[user] = status_text
                broadcast_contact_status(user, True)

            elif action == "change_password":
                cur_pass = msg.get("current_pass", "")
                new_pass = msg.get("new_pass", "")
                if not cur_pass or not new_pass:
                    sock.sendall((json.dumps({"action": "change_password_result", "ok": False, "reason": "Missing fields."}) + "\n").encode())
                else:
                    con = sqlite3.connect(DB)
                    row = con.execute("SELECT password FROM users WHERE username=?", (user,)).fetchone()
                    stored = row[0] if row else None
                    ok = False
                    if stored:
                        if stored.startswith("$argon2"):
                            try: _ph.verify(stored, cur_pass); ok = True
                            except (VerifyMismatchError, VerificationError, InvalidHashError): pass
                        else:
                            ok = (stored == cur_pass)
                    if ok:
                        con.execute("UPDATE users SET password=? WHERE username=?", (_ph.hash(new_pass), user))
                        con.commit(); con.close()
                        sock.sendall((json.dumps({"action": "change_password_result", "ok": True}) + "\n").encode())
                    else:
                        con.close()
                        sock.sendall((json.dumps({"action": "change_password_result", "ok": False, "reason": "Current password is incorrect."}) + "\n").encode())

            elif action == "logout": break
    except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError, OSError):
        pass
    finally:
        try: cs.close()
        except: pass
        with lock:
            if user in clients: del clients[user]
            client_statuses.pop(user, None)
        if user:
            _remove_user_from_all_group_calls(user)
            broadcast_contact_status(user, False)

def check_file_ban(username, file_ext):
    con = sqlite3.connect(DB)
    row = con.execute("SELECT reason FROM file_bans WHERE username=? AND (file_type=? OR file_type='*') AND (until_date IS NULL OR until_date >= ?)",
                       (username, file_ext.lower(), datetime.datetime.now().strftime("%Y-%m-%d"))).fetchone()
    con.close()
    return row[0] if row else None

def handle_banfile(username, file_type, date_str, reason):
    try:
        until_date = None
        if date_str:
            until_date = datetime.datetime.strptime(date_str, "%m/%d/%Y").strftime("%Y-%m-%d")
        con = sqlite3.connect(DB)
        con.execute("INSERT OR REPLACE INTO file_bans(username, file_type, until_date, reason) VALUES(?,?,?,?)",
                     (username, file_type.lower(), until_date, reason))
        con.commit()
        con.close()
        if until_date:
            print(f"User '{username}' banned from sending '{file_type}' files until {until_date}: {reason}")
        else:
            print(f"User '{username}' permanently banned from sending '{file_type}' files: {reason}")
    except ValueError: print("Error: Date format must be mm/dd/yyyy")
    except Exception as e: print(f"An error occurred: {e}")

def handle_unbanfile(username, file_type=None):
    con = sqlite3.connect(DB)
    if file_type:
        con.execute("DELETE FROM file_bans WHERE username=? AND file_type=?", (username, file_type.lower()))
    else:
        con.execute("DELETE FROM file_bans WHERE username=?", (username,))
    con.commit()
    con.close()
    if file_type:
        print(f"User '{username}' file ban for '{file_type}' removed.")
    else:
        print(f"All file bans for user '{username}' removed.")

def serve_loop(config):
    global use_ssl
    context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    use_ssl = False
    
    print(f"Server Current Working Directory: {os.getcwd()}")
    try:
        context.load_cert_chain(certfile=config['certfile'], keyfile=config['keyfile'])
        use_ssl = True
        print(f"Secure (SSL) server listening on port {config['port']}...")
    except (FileNotFoundError, ssl.SSLError) as e:
        print(f"WARNING: Certificate or key file not found or invalid ({e}).")
        print(f"Looking for Cert: {os.path.abspath(config['certfile'])}")
        print(f"Looking for Key:  {os.path.abspath(config['keyfile'])}")
        print(f"Server running in INSECURE (UNENCRYPTED) mode on port {config['port']}...")

    bindsocket = socket.socket()
    bindsocket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    bindsocket.bind(("0.0.0.0", config['port']))
    bindsocket.listen(5)
    
    while True:
        try:
            newsocket, fromaddr = bindsocket.accept()
            try:
                if use_ssl:
                    connstream = context.wrap_socket(newsocket, server_side=True)
                else:
                    connstream = newsocket
                
                threading.Thread(target=handle_client, args=(connstream, fromaddr), daemon=True).start()
            except ssl.SSLError as e: 
                print(f"SSL Error from {fromaddr}: {e}. Probably a port scan. Ignoring.")
                newsocket.close()
            except Exception as e: 
                print(f"Error accepting connection from {fromaddr}: {e}")
                newsocket.close()
        except Exception as e: 
            print(f"Critical error in main serve_loop: {e}")
            import time
            time.sleep(1)

def handle_create(user, password, email=""):
    con = sqlite3.connect(DB)
    existing = con.execute("SELECT 1 FROM users WHERE LOWER(username)=LOWER(?)", (user,)).fetchone()
    if not existing:
        con.execute("INSERT INTO users(username,password,email,is_verified) VALUES(?,?,?,1)", (user, password, email))
        con.commit(); con.close()
        print(f"User '{user}' created.")
        return True
    con.close()
    print(f"User '{user}' already exists (case-insensitive match).")
    return False

def handle_ban(user, date_str, reason):
    try: 
        until_date = datetime.datetime.strptime(date_str,"%m/%d/%Y").strftime("%Y-%m-%d")
        con = sqlite3.connect(DB)
        con.execute("UPDATE users SET banned_until=?,ban_reason=? WHERE username=?",(until_date, reason, user))
        con.commit()
        con.close()
        print(f"User '{user}' banned until {until_date} for: {reason}")
        kick_if_banned(user)
    except ValueError: print("Error: Date format must be mm/dd/yyyy")
    except Exception as e: print(f"An error occurred: {e}")

def handle_unban(user):
    con = sqlite3.connect(DB)
    con.execute("UPDATE users SET banned_until=NULL,ban_reason=NULL WHERE username=?",(user,))
    con.commit()
    con.close()
    print(f"User '{user}' unbanned.")

def handle_delete(user):
    con = sqlite3.connect(DB)
    con.execute("DELETE FROM users WHERE username=?", (user,))
    con.execute("DELETE FROM contacts WHERE owner=? OR contact=?", (user, user))
    con.commit()
    con.close()
    print(f"User '{user}' and all associated contact data deleted.")
    kick_if_banned(user)

def run_cli():
    print("Thrive Server Admin Console")
    print("Available commands: help, create, ban, unban, del, admin, unadmin, alert, banfile, unbanfile, restart, exit")
    while True:
        try:
            cmd_line = input("> ").strip()
            parts = cmd_line.split()
            if not parts: continue
            command = parts[0].lower()
            if command == "help":
                print("Available commands: help, create, ban, unban, del, admin, unadmin, alert, banfile, unbanfile, restart, exit")
            if command == "exit":
                broadcast_alert(f"The server is shutting down in {shutdown_timeout} seconds.")
                print(f"Server shutting down in {shutdown_timeout} seconds...")
                time.sleep(shutdown_timeout)
                os._exit(0)
            elif command == "restart":
                broadcast_alert(f"The server is restarting in {shutdown_timeout} seconds.")
                print(f"Server restarting in {shutdown_timeout} seconds...")
                time.sleep(shutdown_timeout)
                os.execv(sys.executable, [sys.executable] + sys.argv)
            elif command == "create" and len(parts)==3: handle_create(parts[1], parts[2])
            elif command == "ban" and len(parts)>=4: handle_ban(parts[1], parts[2], " ".join(parts[3:]))
            elif command == "unban" and len(parts)==2: handle_unban(parts[1])
            elif command == "del" and len(parts)==2: handle_delete(parts[1])
            elif command == "admin" and len(parts)==2: add_admin(parts[1])
            elif command == "unadmin" and len(parts)==2: remove_admin(parts[1])
            elif command == "alert" and len(parts)>=2:
                broadcast_alert(" ".join(parts[1:]))
                print("Alert sent.")
            elif command == "banfile" and len(parts)>=4:
                date_str = None
                try:
                    datetime.datetime.strptime(parts[3], "%m/%d/%Y")
                    date_str = parts[3]
                    reason = " ".join(parts[4:]) if len(parts) >= 5 else "No reason given"
                except (ValueError, IndexError):
                    reason = " ".join(parts[3:])
                handle_banfile(parts[1], parts[2], date_str, reason)
            elif command == "unbanfile" and len(parts)>=2: handle_unbanfile(parts[1], parts[2] if len(parts)>=3 else None)
            else: print(f"Unknown command or wrong number of arguments for: '{command}'")
        except (KeyboardInterrupt, EOFError): 
            print("\nExiting.")
            os._exit(0)

def main():
    global server_port
    config = load_config()
    server_port = config['port']
    init_db()
    threading.Thread(target=serve_loop, args=(config,), daemon=True).start()
    run_cli()

if __name__=="__main__": main()
