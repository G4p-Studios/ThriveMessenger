import sqlite3, threading, socket, json, datetime, sys, configparser, ssl, os, uuid, base64, time, subprocess, tempfile, glob, zipfile, hashlib
import smtplib, secrets
import urllib.request, urllib.parse
from email.mime.text import MIMEText
try:
    from . import feature_modules, group_rooms, module_installer
except ImportError:
    import feature_modules, group_rooms, module_installer

DB = 'thrive.db'
ADMIN_FILE = 'admins.txt'
clients = {}
client_statuses = {}
session_preferences = {}
lock = threading.Lock()
smtp_config = {}
code_expires = 5 * 60
code_expires_label = "5m"
flexpbx_config = {}
file_config = {}
bot_runtime_config = {}
module_runtime_config = {}
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
bot_auth_map = {}
bot_external_usernames = set()
allow_external_bot_contacts = True
docs_cache = {}
bot_rules_config = {}
bot_rules_text = {}
bot_session_registry = {}
bot_session_lock = threading.Lock()
bot_temp_file_registry = {}
bot_temp_file_lock = threading.Lock()
bot_moderation_registry = {}
bot_moderation_lock = threading.Lock()
restart_lock = threading.Lock()
restart_scheduled_for = None
group_call_sessions = {}
# Snapshot and cleanup helpers can be called while the registry is already
# locked. A re-entrant lock keeps those operations atomic without deadlocking.
group_call_lock = threading.RLock()
FEATURE_DEFAULTS = {
    "bots": {"enabled": False, "ui_visible": False, "scope": "all", "description": "Experimental externally hosted bot identities."},
    "bot_mesh": {"enabled": False, "ui_visible": False, "scope": "all", "description": "Experimental multi-server bot relay."},
    "bot_moderation": {"enabled": False, "ui_visible": False, "scope": "admin", "description": "Experimental bot-powered moderation."},
    "bot_rules": {"enabled": False, "ui_visible": False, "scope": "admin", "description": "Experimental bot rules management."},
    "group_chat": {"enabled": True, "ui_visible": True, "scope": "all", "description": "Group chat create/join/send features."},
    "group_call": {"enabled": True, "ui_visible": True, "scope": "all", "description": "Group call session and signaling features."},
    "voice_call": {"enabled": True, "ui_visible": True, "scope": "all", "description": "Self-contained direct voice calls."},
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
    defaults = _fetch_group_policy("global", "__global__") if scope == "group" else _group_policy_defaults()
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
    result = {
        "feature_key": fk,
        "enabled": bool(int(row[0] or 0)),
        "ui_visible": bool(int(row[1] or 0)),
        "scope": str(row[2] or "all"),
        "description": str(row[3] or ""),
    }
    if fk in feature_modules.MODULES["bots"]["features"]:
        modules_dir = module_runtime_config.get('modules_dir') or os.path.join(os.path.dirname(os.path.abspath(__file__)), 'modules')
        if "bots" not in module_installer.installed_module_ids(modules_dir):
            result["enabled"] = False
            result["ui_visible"] = False
    return result

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

def _room_broadcast(room_id, payload, exclude=None):
    """Send a room event to each currently connected room member."""
    try:
        members = [item["username"] for item in group_rooms.list_members(DB, room_id)]
    except Exception:
        members = []
    with lock:
        targets = [clients[name] for name in members if name != exclude and name in clients]
    wire = (json.dumps(payload) + "\n").encode()
    for target in targets:
        try:
            target.sendall(wire)
        except Exception:
            pass

def _remove_user_from_all_group_calls(username):
    events = []
    direct_events = []
    with group_call_lock:
        for g, data in list(group_call_sessions.items()):
            participants = data.get("participants", set())
            pending = data.get("pending")
            if data.get("direct") and (username in participants or pending == username):
                others = (set(participants) | ({pending} if pending else set())) - {username}
                direct_events.extend((other, g.removeprefix("direct:")) for other in others)
                group_call_sessions.pop(g, None)
                continue
            if username in participants:
                participants.discard(username)
                snapshot = {"action": "group_call_event", "event": "leave", "by": username}
                snapshot.update(_group_call_snapshot(g))
                events.append((g, snapshot))
            if not participants:
                group_call_sessions.pop(g, None)
    for g, payload in events:
        _group_call_broadcast(g, payload, exclude=username)
    for target, call_id in direct_events:
        with lock:
            target_sock = clients.get(target)
        if target_sock:
            try: target_sock.sendall((json.dumps({"action": "voice_call_event", "event": "ended", "call_id": call_id, "with": username}) + "\n").encode())
            except Exception: pass

def _user_has_direct_call(username):
    with group_call_lock:
        for data in group_call_sessions.values():
            if not data.get("direct"):
                continue
            if username in data.get("participants", set()) or username == data.get("pending"):
                return True
    return False

def _expire_direct_call(call_id):
    group_key = f"direct:{call_id}"
    with group_call_lock:
        data = group_call_sessions.get(group_key)
        if not data or not data.get("pending"):
            return
        group_call_sessions.pop(group_key, None)
    names = set(data.get("participants", set())) | {data.get("pending")}
    with lock:
        targets = [clients.get(name) for name in names]
    for target in targets:
        if target:
            try: target.sendall((json.dumps({"action": "voice_call_event", "event": "failed", "call_id": call_id, "reason": "The call was not answered."}) + "\n").encode())
            except Exception: pass
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

def _bot_auth_type(bot_name):
    name = str(bot_name or "").strip()
    if not name:
        return "bot"
    mapped = str(bot_auth_map.get(name, "") or "").strip()
    if mapped:
        return mapped
    lower = name.lower()
    if "openclaw" in lower:
        return "openclaw"
    if "opencode" in lower:
        return "opencode"
    if "codex" in lower:
        return "codex"
    if "ollama" in lower:
        return "ollama"
    if "assistant" in lower:
        return "assistant"
    if "claude" in lower:
        return "claude"
    return "bot"

def _bot_session_snapshot(username):
    uname = str(username or "").strip()
    if not uname:
        return None
    with bot_session_lock:
        data = bot_session_registry.get(uname)
        if not data:
            return None
        return {
            "user": uname,
            "auth_type": str(data.get("auth_type", _bot_auth_type(uname))),
            "runtime": str(data.get("runtime", "cli")),
            "host_label": str(data.get("host_label", "") or ""),
            "platform": str(data.get("platform", "") or ""),
            "capabilities": list(data.get("capabilities", [])),
            "transports": list(data.get("transports", [])),
            "temp_dir": str(data.get("temp_dir", "") or ""),
            "accepts_files": bool(data.get("accepts_files", False)),
            "supports_delegation": bool(data.get("supports_delegation", True)),
            "background": bool(data.get("background", False)),
            "server": str(data.get("server", server_identity)),
            "connected_at": str(data.get("connected_at", "") or ""),
            "last_seen": str(data.get("last_seen", "") or ""),
            "moderation": dict(data.get("moderation", {}) or {}),
        }

def _active_bot_sessions():
    sessions = []
    with bot_session_lock:
        names = sorted(bot_session_registry.keys(), key=lambda x: x.lower())
    for name in names:
        snap = _bot_session_snapshot(name)
        if snap:
            sessions.append(snap)
    return sessions

def _cleanup_bot_session(username):
    uname = str(username or "").strip()
    if not uname:
        return
    with bot_session_lock:
        bot_session_registry.pop(uname, None)
    with bot_moderation_lock:
        bot_moderation_registry.pop(uname, None)
    with bot_temp_file_lock:
        stale_ids = [
            file_id for file_id, meta in bot_temp_file_registry.items()
            if meta.get("from") == uname or meta.get("to") == uname
        ]
        for file_id in stale_ids:
            meta = bot_temp_file_registry.pop(file_id, None)
            path = str((meta or {}).get("path", "") or "")
            if path and os.path.exists(path):
                try:
                    os.remove(path)
                except Exception:
                    pass

def _store_bot_mesh_temp_file(sender, target, filename, data_b64, mime="", request_id=""):
    clean_name = os.path.basename(str(filename or "").strip())
    if not clean_name or clean_name != str(filename or "").strip():
        raise ValueError("Invalid filename.")
    raw = base64.b64decode(str(data_b64 or "").encode("ascii"), validate=True)
    max_size = int(bot_runtime_config.get("bot_mesh_max_file_size", 10485760) or 10485760)
    if len(raw) > max_size:
        raise ValueError(f"File exceeds bot mesh size limit of {max_size} bytes.")
    root = str(bot_runtime_config.get("bot_mesh_temp_root", "") or "").strip() or os.path.join(tempfile.gettempdir(), "thrive_bot_mesh")
    os.makedirs(root, exist_ok=True)
    file_id = str(uuid.uuid4())
    path = os.path.join(root, f"{file_id}_{clean_name}")
    with open(path, "wb") as f:
        f.write(raw)
    meta = {
        "id": file_id,
        "from": str(sender or "").strip(),
        "to": str(target or "").strip(),
        "filename": clean_name,
        "mime": str(mime or "").strip(),
        "request_id": str(request_id or "").strip(),
        "size": len(raw),
        "path": path,
        "created_at": datetime.datetime.utcnow().isoformat(),
    }
    with bot_temp_file_lock:
        bot_temp_file_registry[file_id] = meta
    return meta

def _is_guest_like_username(username):
    uname = str(username or "").strip().lower()
    if not uname:
        return False
    return (
        uname.startswith("guest")
        or uname.startswith("anon")
        or uname.startswith("temp")
        or uname.endswith("-guest")
    )

def _moderation_excerpt(text, limit=280):
    cleaned = " ".join(str(text or "").replace("\r", " ").replace("\n", " ").split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: max(0, limit - 3)] + "..."

def _spam_signal_summary(text):
    raw = str(text or "")
    lower = raw.lower()
    score = 0
    reasons = []
    if len(raw) > 800:
        score += 1
        reasons.append("long_message")
    if lower.count("http://") + lower.count("https://") >= 3:
        score += 2
        reasons.append("many_links")
    if raw.count("\n") >= 8:
        score += 1
        reasons.append("multi_line_burst")
    if len(raw) >= 40 and len(set(raw)) <= max(4, len(raw) // 20):
        score += 2
        reasons.append("repetitive_pattern")
    if sum(1 for ch in raw if ch.isupper()) >= 20 and raw:
        upper_ratio = sum(1 for ch in raw if ch.isupper()) / max(1, sum(1 for ch in raw if ch.isalpha()))
        if upper_ratio >= 0.75:
            score += 1
            reasons.append("mostly_uppercase")
    if any(token in lower for token in ("free nitro", "airdrop", "bitcoin", "crypto", "claim now", "dm me", "telegram")):
        score += 2
        reasons.append("spam_keywords")
    return {"score": score, "reasons": reasons, "flagged": score >= 2}

def _emit_bot_moderation_event(event_type, payload):
    if not _feature_policy_row("bot_moderation") or not _feature_policy_row("bot_moderation").get("enabled", True):
        return
    event_kind = str(event_type or "").strip().lower()
    if not event_kind:
        return
    watchers = []
    with bot_moderation_lock:
        for uname, cfg in list(bot_moderation_registry.items()):
            if not isinstance(cfg, dict) or not cfg.get("enabled", True):
                continue
            kinds = cfg.get("kinds", [])
            if kinds and "*" not in kinds and event_kind not in kinds:
                continue
            watchers.append(uname)
    if not watchers:
        return
    envelope = {
        "action": "bot_moderation_event",
        "event_type": event_kind,
        "event_id": str(uuid.uuid4()),
        "payload": payload if isinstance(payload, dict) else {},
        "relay_server": server_identity,
        "sent_at": datetime.datetime.utcnow().isoformat(),
    }
    wire = (json.dumps(envelope) + "\n").encode()
    for uname in watchers:
        with bot_session_lock:
            data = bot_session_registry.get(uname) or {}
            target_sock = data.get("sock")
        if not target_sock:
            continue
        try:
            target_sock.sendall(wire)
        except Exception:
            pass

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

def _create_invite_token(invited_user, invited_email, invited_by, expires_hours=168):
    token = secrets.token_urlsafe(24)
    now = datetime.datetime.utcnow()
    expires_at = (now + datetime.timedelta(hours=max(1, int(expires_hours)))).isoformat()
    con = sqlite3.connect(DB)
    con.execute(
        """
        INSERT OR REPLACE INTO invite_tokens(token, invited_user, invited_email, invited_by, created_at, expires_at, used)
        VALUES(?,?,?,?,?,?,0)
        """,
        (
            token,
            str(invited_user or "").strip(),
            str(invited_email or "").strip(),
            str(invited_by or "").strip(),
            now.isoformat(),
            expires_at,
        ),
    )
    con.commit()
    con.close()
    return token

def _is_invite_expired(expires_at):
    try:
        expires = datetime.datetime.fromisoformat(str(expires_at or "").strip())
    except Exception:
        return True
    return datetime.datetime.utcnow() > expires

def _hash_passkey_secret(raw_secret):
    return hashlib.sha256(str(raw_secret or "").encode("utf-8")).hexdigest()

def _get_server_setting(key, default_value=None):
    try:
        con = sqlite3.connect(DB)
        row = con.execute("SELECT value FROM server_settings WHERE key=?", (str(key),)).fetchone()
        con.close()
        if row and len(row) > 0:
            return row[0]
    except Exception:
        pass
    return default_value

def _set_server_setting(key, value):
    con = sqlite3.connect(DB)
    con.execute(
        "INSERT OR REPLACE INTO server_settings(key, value) VALUES(?, ?)",
        (str(key), str(value)),
    )
    con.commit()
    con.close()

def _max_accounts_per_email():
    raw = _get_server_setting("max_accounts_per_email", "0")
    try:
        limit = int(str(raw))
        return max(0, limit)
    except Exception:
        return 0

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
        return secrets.token_hex(16)

def _parse_duration(value, default_seconds=300):
    text = str(value or "").strip().lower()
    if not text:
        return default_seconds
    try:
        if text.endswith("ms"):
            return max(1, int(float(text[:-2]) / 1000))
        if text.endswith("s"):
            return max(1, int(float(text[:-1])))
        if text.endswith("m"):
            return max(1, int(float(text[:-1]) * 60))
        if text.endswith("h"):
            return max(1, int(float(text[:-1]) * 60 * 60))
        if text.endswith("d"):
            return max(1, int(float(text[:-1]) * 24 * 60 * 60))
        return max(1, int(float(text)))
    except Exception:
        return default_seconds

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
    global code_expires, code_expires_label
    code_expires_label = config.get('smtp', 'code_expires', fallback='5m')
    code_expires = _parse_duration(code_expires_label, 5 * 60)
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
    raw_bots = config.get('bots', 'names', fallback='')
    bot_usernames = {name.strip() for name in raw_bots.split(',') if name.strip()}
    global bot_status_map
    bot_status_map = _parse_bot_map(config.get('bots', 'status_map', fallback=''))
    global bot_purpose_map
    bot_purpose_map = _parse_bot_map(config.get('bots', 'purpose_map', fallback=''))
    global bot_service_map
    bot_service_map = _parse_bot_map(config.get('bots', 'service_map', fallback=''))
    global bot_auth_map
    bot_auth_map = _parse_bot_map(config.get('bots', 'auth_type_map', fallback='openclaw-bot:openclaw,assistant-bot:ollama,helper-bot:codex'))
    global bot_external_usernames
    raw_external = config.get('bots', 'external_names', fallback='')
    bot_external_usernames = {name.strip() for name in raw_external.split(',') if name.strip()}
    global allow_external_bot_contacts
    allow_external_bot_contacts = config.getboolean('bots', 'allow_external_bot_contacts', fallback=True)
    global bot_voice_map
    bot_voice_map = _parse_bot_map(config.get('bots', 'voice_map', fallback=''))
    shared_rules_user = (
        os.getenv('THRIVE_SHARED_USER')
        or os.getenv('DEPLOY_USER')
        or os.getenv('SUDO_USER')
        or os.getenv('USER')
        or 'tappedin'
    )
    default_agent_rules_zip = f"/home/{shared_rules_user}/shared/agents/*.zip"
    global bot_rules_config
    bot_rules_config = {
        'agent_rules_zip_path': config.get('bots', 'agent_rules_zip_path', fallback=default_agent_rules_zip),
        'agent_rules_file_path': config.get('bots', 'agent_rules_file_path', fallback=''),
    }
    _refresh_bot_rules()
    global bot_runtime_config
    bot_runtime_config = {
        'ollama_enabled': config.getboolean('bots', 'ollama_enabled', fallback=False),
        'ollama_url': config.get('bots', 'ollama_url', fallback='http://127.0.0.1:11434'),
        'ollama_model': config.get('bots', 'ollama_model', fallback='llama3.2'),
        'ollama_timeout': config.getint('bots', 'ollama_timeout', fallback=20),
        'ollama_system_prompt': config.get('bots', 'ollama_system_prompt', fallback=''),
        'piper_enabled': config.getboolean('bots', 'piper_enabled', fallback=False),
        'piper_bin': config.get('bots', 'piper_bin', fallback='/usr/local/bin/piper'),
        'piper_models_dir': config.get('bots', 'piper_models_dir', fallback='./voices'),
        'piper_default_voice': config.get('bots', 'piper_default_voice', fallback='en_US-lessac-medium'),
        'piper_timeout': config.getint('bots', 'piper_timeout', fallback=12),
        'bot_mesh_temp_root': config.get('bots', 'bot_mesh_temp_root', fallback=os.path.join(tempfile.gettempdir(), 'thrive_bot_mesh')),
        'bot_mesh_max_file_size': config.getint('bots', 'bot_mesh_max_file_size', fallback=10485760),
        'moderation_watch_direct_messages': config.getboolean('bots', 'moderation_watch_direct_messages', fallback=True),
        'moderation_watch_file_offers': config.getboolean('bots', 'moderation_watch_file_offers', fallback=True),
        'moderation_watch_guest_logins': config.getboolean('bots', 'moderation_watch_guest_logins', fallback=True),
        'moderation_excerpt_limit': config.getint('bots', 'moderation_excerpt_limit', fallback=280),
    }
    global module_runtime_config
    catalog_urls = [item.strip() for item in config.get('modules', 'catalog_urls', fallback='').split(',') if item.strip()]
    allowed_hosts = {item.strip().lower() for item in config.get('modules', 'allowed_hosts', fallback='').split(',') if item.strip()}
    module_runtime_config = {
        'catalog_urls': catalog_urls,
        'allowed_hosts': allowed_hosts,
        'modules_dir': os.path.join(os.path.dirname(os.path.abspath(__file__)), 'modules'),
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
    if 'verification_code_at' not in existing_cols: cur.execute("ALTER TABLE users ADD COLUMN verification_code_at REAL")
    if 'is_verified' not in existing_cols: cur.execute("ALTER TABLE users ADD COLUMN is_verified INTEGER DEFAULT 1") # Default 1 for old users
    if 'reset_code' not in existing_cols: cur.execute("ALTER TABLE users ADD COLUMN reset_code TEXT")
    if 'reset_code_at' not in existing_cols: cur.execute("ALTER TABLE users ADD COLUMN reset_code_at REAL")

    cur.execute('''CREATE TABLE IF NOT EXISTS contacts (owner TEXT, contact TEXT, blocked INTEGER DEFAULT 0, PRIMARY KEY(owner, contact))''')
    cur.execute('''CREATE TABLE IF NOT EXISTS bot_tokens (owner TEXT, bot TEXT, token TEXT, created_at TEXT, PRIMARY KEY(owner, bot))''')
    cur.execute('''CREATE TABLE IF NOT EXISTS invite_tokens (token TEXT PRIMARY KEY, invited_user TEXT, invited_email TEXT, invited_by TEXT, created_at TEXT, expires_at TEXT, used INTEGER DEFAULT 0)''')
    cur.execute('''CREATE TABLE IF NOT EXISTS user_passkeys (id TEXT PRIMARY KEY, username TEXT, label TEXT, token_hash TEXT, created_at TEXT, last_used_at TEXT, revoked INTEGER DEFAULT 0)''')
    cur.execute('''CREATE TABLE IF NOT EXISTS server_settings (key TEXT PRIMARY KEY, value TEXT)''')
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
    cur.execute("INSERT OR IGNORE INTO server_settings(key, value) VALUES('max_accounts_per_email', '0')")
    conn.commit()
    _seed_feature_defaults()
    conn.close()
    group_rooms.init_group_schema(DB)

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

        # File payloads use a short-lived connection so a large upload cannot
        # block the authenticated messaging and administration session.
        if action == "file_data":
            transfer_id = req.get("transfer_id")
            file_token = req.get("file_token")
            with transfer_lock:
                transfer = pending_transfers.pop(transfer_id, None)
            if not transfer or transfer.get("file_token") != file_token:
                sock.sendall(b'{"status":"error","reason":"Invalid transfer"}\n')
                return
            recipient = transfer["to"]
            with lock:
                sock_to = clients.get(recipient)
            if sock_to:
                name_map = {item["filename"]: item["filename"] for item in transfer["files"]}
                safe_files = [
                    dict(item, filename=name_map.get(item["filename"], item["filename"]))
                    for item in req.get("files", [])
                    if "/" not in item["filename"] and "\\" not in item["filename"]
                ]
                try:
                    sock_to.sendall((json.dumps({"action": "file_data", "from": transfer["from"], "files": safe_files}) + "\n").encode())
                except Exception:
                    pass
            sock.sendall(b'{"status":"ok"}\n')
            return

        # --- Welcome Message (pre-login safe endpoint) ---
        if action == "get_welcome":
            sock.sendall((json.dumps({
                "action": "welcome_info",
                "enabled": bool(welcome_config.get('enabled', False)),
                "pre_login": welcome_config.get('pre_login', '') if welcome_config.get('enabled', False) else '',
                "post_login": welcome_config.get('post_login', '') if welcome_config.get('enabled', False) else '',
            }) + "\n").encode())
            return

        # --- Validate Invite Token (pre-login) ---
        if action == "validate_invite":
            invite_token = str(req.get("invite_token", "") or "").strip()
            if not invite_token:
                sock.sendall((json.dumps({
                    "action": "invite_validation",
                    "status": "error",
                    "reason": "Missing invite token.",
                }) + "\n").encode())
                return
            con = sqlite3.connect(DB)
            row = con.execute(
                "SELECT invited_user, invited_email, invited_by, expires_at, used FROM invite_tokens WHERE token=?",
                (invite_token,),
            ).fetchone()
            con.close()
            if not row:
                sock.sendall((json.dumps({
                    "action": "invite_validation",
                    "status": "error",
                    "reason": "Invite token is invalid.",
                }) + "\n").encode())
                return
            invited_user, invited_email, invited_by, expires_at, used = row
            if int(used or 0) != 0:
                sock.sendall((json.dumps({
                    "action": "invite_validation",
                    "status": "error",
                    "reason": "Invite token has already been used.",
                }) + "\n").encode())
                return
            if _is_invite_expired(expires_at):
                sock.sendall((json.dumps({
                    "action": "invite_validation",
                    "status": "error",
                    "reason": "Invite token has expired.",
                }) + "\n").encode())
                return
            sock.sendall((json.dumps({
                "action": "invite_validation",
                "status": "ok",
                "invite_user": str(invited_user or "").strip(),
                "invite_email": str(invited_email or "").strip(),
                "invited_by": str(invited_by or "").strip(),
                "expires_at": str(expires_at or "").strip(),
            }) + "\n").encode())
            return
        
        # --- Create Account ---
        if action == "create_account":
            new_user = req.get("user")
            new_pass = req.get("pass")
            email = req.get("email", "")
            invite_token = str(req.get("invite_token", "") or "").strip()
            if not new_user or not new_pass: 
                sock.sendall((json.dumps({"action": "create_account_failed", "reason": "Missing fields."}) + "\n").encode())
                return
            
            con = sqlite3.connect(DB)
            invite_row = None
            if invite_token:
                invite_row = con.execute(
                    "SELECT invited_user, invited_email, expires_at, used FROM invite_tokens WHERE token=?",
                    (invite_token,),
                ).fetchone()
                if not invite_row:
                    con.close()
                    sock.sendall((json.dumps({"action": "create_account_failed", "reason": "Invite token is invalid."}) + "\n").encode())
                    return
                invited_user, invited_email, expires_at, used = invite_row
                if int(used or 0) != 0:
                    con.close()
                    sock.sendall((json.dumps({"action": "create_account_failed", "reason": "Invite token has already been used."}) + "\n").encode())
                    return
                if _is_invite_expired(expires_at):
                    con.close()
                    sock.sendall((json.dumps({"action": "create_account_failed", "reason": "Invite token has expired."}) + "\n").encode())
                    return
                invited_user = str(invited_user or "").strip()
                invited_email = str(invited_email or "").strip()
                if invited_user and str(new_user).strip().lower() != invited_user.lower():
                    con.close()
                    sock.sendall((json.dumps({"action": "create_account_failed", "reason": "Invite token does not match this username."}) + "\n").encode())
                    return
                if invited_email:
                    if str(email or "").strip():
                        if str(email).strip().lower() != invited_email.lower():
                            con.close()
                            sock.sendall((json.dumps({"action": "create_account_failed", "reason": "Invite token does not match this email."}) + "\n").encode())
                            return
                    else:
                        email = invited_email

            row = con.execute("SELECT is_verified FROM users WHERE username=?", (new_user,)).fetchone()
            normalized_email = str(email or "").strip().lower()
            if normalized_email:
                limit = _max_accounts_per_email()
                if limit > 0:
                    count_row = con.execute(
                        "SELECT COUNT(*) FROM users WHERE lower(trim(email))=?",
                        (normalized_email,),
                    ).fetchone()
                    existing_count = int((count_row or [0])[0] or 0)
                    if existing_count >= limit and not row:
                        con.close()
                        sock.sendall((json.dumps({
                            "action": "create_account_failed",
                            "reason": "An account already exists for this email. Please log in with your existing account.",
                        }) + "\n").encode())
                        return
            
            # Allow overwriting unverified users
            if row and (row[0] == 1 or not smtp_config['enabled']):
                sock.sendall((json.dumps({"action": "create_account_failed", "reason": "Username is already taken."}) + "\n").encode())
                con.close(); return
            
            # Logic: If SMTP is on, set verified=0, gen code, send email. Else verified=1.
            verified = 1 if not smtp_config['enabled'] else 0
            code = EmailManager.generate_code() if not verified else None
            code_at = time.time() if code else None
            
            if row: # Overwriting unverified
                con.execute("UPDATE users SET password=?, email=?, verification_code=?, verification_code_at=?, is_verified=? WHERE username=?", (new_pass, email, code, code_at, verified, new_user))
            else:
                con.execute("INSERT INTO users(username, password, email, verification_code, verification_code_at, is_verified) VALUES(?,?,?,?,?,?)", (new_user, new_pass, email, code, code_at, verified))
            if invite_token:
                con.execute("UPDATE invite_tokens SET used=1 WHERE token=?", (invite_token,))
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
            row = con.execute("SELECT verification_code, email, verification_code_at FROM users WHERE username=?", (u_ver,)).fetchone()
            if row and row[0] == code_ver:
                issued_at = row[2] or 0
                if issued_at and time.time() - float(issued_at) > code_expires:
                    con.execute("UPDATE users SET verification_code=NULL, verification_code_at=NULL WHERE username=?", (u_ver,))
                    con.commit(); con.close()
                    sock.sendall(json.dumps({"status": "error", "reason": f"Verification code expired. Request a new code; codes expire after {code_expires_label}."}).encode() + b"\n")
                    return
                con.execute("UPDATE users SET is_verified=1, verification_code=NULL, verification_code_at=NULL WHERE username=?", (u_ver,))
                con.commit(); con.close()
                if row[1]:
                    EmailManager.send_email(
                        row[1],
                        "Thrive Messenger - Account Verified",
                        f"Hi {u_ver}, your account on {server_identity} has been verified and is ready to use."
                    )
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
                    con.execute("UPDATE users SET reset_code=?, reset_code_at=? WHERE username=?", (code, time.time(), t_user))
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
            row = con.execute("SELECT reset_code, reset_code_at FROM users WHERE username=?", (t_user,)).fetchone()
            if row and row[0] == t_code and t_code:
                issued_at = row[1] or 0
                if issued_at and time.time() - float(issued_at) > code_expires:
                    con.execute("UPDATE users SET reset_code=NULL, reset_code_at=NULL WHERE username=?", (t_user,))
                    con.commit(); con.close()
                    sock.sendall(json.dumps({"status": "error", "reason": f"Reset code expired. Request a new code; codes expire after {code_expires_label}."}).encode() + b"\n")
                    return
                con.execute("UPDATE users SET password=?, reset_code=NULL, reset_code_at=NULL WHERE username=?", (new_p, t_user))
                con.commit(); con.close()
                sock.sendall(json.dumps({"status": "ok"}).encode() + b"\n")
            else:
                con.close()
                sock.sendall(json.dumps({"status": "error", "reason": "Invalid code"}).encode() + b"\n")
            return

        if action not in ("login", "login_passkey"):
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

        if not row:
            sock.sendall(b'{"status":"error","reason":"Invalid credentials"}\n')
            db.close()
            return

        if action == "login":
            if row[1] != req.get("pass", ""):
                sock.sendall(b'{"status":"error","reason":"Invalid credentials"}\n')
                db.close()
                return
        else:
            passkey_token = str(req.get("passkey_token", "") or "").strip()
            if not passkey_token:
                sock.sendall(b'{"status":"error","reason":"Missing passkey token"}\n')
                db.close()
                return
            passkey_hash = _hash_passkey_secret(passkey_token)
            passkey_row = db.execute(
                "SELECT id FROM user_passkeys WHERE username=? AND token_hash=? AND revoked=0 LIMIT 1",
                (row[0], passkey_hash),
            ).fetchone()
            if not passkey_row:
                sock.sendall(b'{"status":"error","reason":"Invalid passkey"}\n')
                db.close()
                return
            db.execute(
                "UPDATE user_passkeys SET last_used_at=? WHERE id=?",
                (datetime.datetime.utcnow().isoformat(), passkey_row[0]),
            )
            db.commit()

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
        prior_sock = None
        with lock:
            prior_sock = clients.get(user)
            clients[user] = sock
            client_statuses[user] = "online"
            session_preferences[sock] = {}

        # Optional alert to the existing signed-in device when another login happens.
        if prior_sock and prior_sock is not sock:
            notify_existing = False
            with lock:
                notify_existing = bool(
                    session_preferences.get(prior_sock, {}).get("notify_on_other_device_login", False)
                )
            if notify_existing:
                try:
                    prior_sock.sendall((json.dumps({
                        "action": "other_device_login",
                        "user": user,
                        "ip": str(addr[0] if addr else ""),
                        "at": datetime.datetime.utcnow().isoformat() + "Z",
                    }) + "\n").encode())
                except Exception:
                    pass

        admins = get_admins()
        rows = db.execute("SELECT contact,blocked FROM contacts WHERE owner=?", (user,)).fetchall()
        contacts = [{"user":c, "blocked":b, "online": _is_online_user(c), "is_admin": (c in admins), "status_text": _status_for_user(c)} for c,b in rows]
        sock.sendall((json.dumps({"action":"contact_list","contacts":contacts})+"\n").encode())
        _send_feature_caps(sock, user)
        db.close()

        if bot_runtime_config.get("moderation_watch_guest_logins", True) and _is_guest_like_username(user):
            _emit_bot_moderation_event("guest_login", {
                "user": user,
                "ip": str(addr[0] if addr else ""),
                "logged_in_at": datetime.datetime.utcnow().isoformat(),
                "is_guest": True,
                "status_text": _status_for_user(user),
            })
        
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

            elif action == "module_list":
                if not _is_admin(user):
                    _deny_feature("admin_console", "module_list_response")
                    continue
                installed = module_installer.installed_module_ids(module_runtime_config.get('modules_dir', ''))
                catalog = feature_modules.module_catalog(_feature_policy_row, installed)
                sock.sendall((json.dumps({"action": "module_list_response", "ok": True, "modules": catalog}) + "\n").encode())

            elif action == "module_install":
                if not _is_admin(user):
                    _deny_feature("admin_console", "module_result"); continue
                module_id = str(msg.get("module_id", ""))
                metadata = feature_modules.MODULES.get(module_id)
                if not metadata or metadata.get("bundled"):
                    sock.sendall((json.dumps({"action": "module_result", "ok": False, "reason": "This module is bundled or unknown."}) + "\n").encode()); continue
                try:
                    manifest = module_installer.install_from_catalog(
                        module_id, module_runtime_config.get('catalog_urls', []), module_runtime_config.get('modules_dir', ''), module_runtime_config.get('allowed_hosts') or None,
                    )
                    sock.sendall((json.dumps({"action": "module_result", "ok": True, "event": "installed", "module_id": module_id, "manifest": manifest, "restart_required": True}) + "\n").encode())
                except Exception as exc:
                    sock.sendall((json.dumps({"action": "module_result", "ok": False, "reason": str(exc)}) + "\n").encode())

            elif action == "module_set_enabled":
                if not _is_admin(user):
                    _deny_feature("admin_console", "module_result")
                    continue
                module_id = str(msg.get("module_id", ""))
                metadata = feature_modules.MODULES.get(module_id)
                if not metadata:
                    sock.sendall((json.dumps({"action": "module_result", "ok": False, "reason": "Unknown module."}) + "\n").encode())
                    continue
                enabled = bool(msg.get("enabled", True))
                affected = feature_modules.modules_for_state_change(module_id, enabled)
                con = sqlite3.connect(DB)
                for affected_id in affected:
                    for feature_key in feature_modules.MODULES[affected_id]["features"]:
                        prior = _feature_policy_row(feature_key) or FEATURE_DEFAULTS[feature_key]
                        con.execute(
                            """INSERT OR REPLACE INTO feature_policies(feature_key, enabled, ui_visible, scope, description, updated_by, updated_at)
                               VALUES(?,?,?,?,?,?,?)""",
                            (feature_key, int(enabled), int(enabled), prior.get("scope", "all"), prior.get("description", ""), user, datetime.datetime.utcnow().isoformat()),
                        )
                con.commit(); con.close(); _broadcast_feature_caps()
                sock.sendall((json.dumps({"action": "module_result", "ok": True, "module_id": module_id, "enabled": enabled}) + "\n").encode())

            elif action == "set_session_pref":
                with lock:
                    prefs = session_preferences.get(sock)
                    if prefs is None:
                        prefs = {}
                        session_preferences[sock] = prefs
                    if "notify_on_other_device_login" in msg:
                        prefs["notify_on_other_device_login"] = bool(msg.get("notify_on_other_device_login", False))

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
                    bot_session = _bot_session_snapshot(contact_to_add) if is_bot else None
                    contact_data = {
                        "user": contact_to_add,
                        "blocked": 0,
                        "online": is_online,
                        "is_admin": contact_to_add in admins,
                        "status_text": contact_status_text,
                        "is_bot": bool(is_bot),
                        "bot_origin": "local" if _is_virtual_bot(contact_to_add) else ("external" if is_bot else "user"),
                        "bot_auth_type": _bot_auth_type(contact_to_add) if is_bot else "",
                        "bot_session": bot_session,
                        "bot_rules_available": bool(rules_text),
                        "bot_rules_preview": rules_text[:1000] if rules_text else "",
                        "bot_rules_editable": bool(is_bot and _is_admin(user)),
                    }
                    if _is_virtual_bot(contact_to_add):
                        token = _upsert_bot_token(user, contact_to_add)
                        contact_data["bot_auth_token"] = token
                    sock.sendall((json.dumps({"action": "add_contact_success", "contact": contact_data}) + "\n").encode())
                con.close()

            elif action == "register_passkey":
                label = str(msg.get("label", "") or "").strip()
                raw_token = str(msg.get("passkey_token", "") or "").strip()
                if not raw_token or len(raw_token) < 24:
                    try:
                        sock.sendall((json.dumps({
                            "action": "passkey_register_result",
                            "ok": False,
                            "reason": "Passkey token is missing or too short.",
                        }) + "\n").encode())
                    except Exception:
                        pass
                    continue
                if not label:
                    label = f"Thrive Messenger - {user}"
                now = datetime.datetime.utcnow().isoformat()
                token_hash = _hash_passkey_secret(raw_token)
                con = sqlite3.connect(DB)
                existing = con.execute(
                    "SELECT id FROM user_passkeys WHERE username=? AND token_hash=? LIMIT 1",
                    (user, token_hash),
                ).fetchone()
                if existing:
                    passkey_id = existing[0]
                    con.execute(
                        "UPDATE user_passkeys SET label=?, revoked=0 WHERE id=?",
                        (label, passkey_id),
                    )
                else:
                    passkey_id = str(uuid.uuid4())
                    con.execute(
                        "INSERT INTO user_passkeys(id, username, label, token_hash, created_at, last_used_at, revoked) VALUES(?,?,?,?,?,?,0)",
                        (passkey_id, user, label, token_hash, now, now),
                    )
                con.commit()
                con.close()
                try:
                    sock.sendall((json.dumps({
                        "action": "passkey_register_result",
                        "ok": True,
                        "passkey_id": passkey_id,
                        "label": label,
                    }) + "\n").encode())
                except Exception:
                    pass

            elif action == "list_passkeys":
                con = sqlite3.connect(DB)
                rows = con.execute(
                    "SELECT id, label, created_at, last_used_at, revoked FROM user_passkeys WHERE username=? ORDER BY created_at DESC",
                    (user,),
                ).fetchall()
                con.close()
                entries = [
                    {
                        "id": r[0],
                        "label": r[1],
                        "created_at": r[2],
                        "last_used_at": r[3],
                        "revoked": bool(r[4]),
                    }
                    for r in rows
                ]
                try:
                    sock.sendall((json.dumps({
                        "action": "passkey_list",
                        "passkeys": entries,
                    }) + "\n").encode())
                except Exception:
                    pass

            elif action == "revoke_passkey":
                passkey_id = str(msg.get("passkey_id", "") or "").strip()
                if not passkey_id:
                    try:
                        sock.sendall((json.dumps({
                            "action": "passkey_revoke_result",
                            "ok": False,
                            "reason": "Missing passkey id.",
                        }) + "\n").encode())
                    except Exception:
                        pass
                    continue
                con = sqlite3.connect(DB)
                res = con.execute(
                    "UPDATE user_passkeys SET revoked=1 WHERE id=? AND username=?",
                    (passkey_id, user),
                )
                con.commit()
                changed = int(getattr(res, "rowcount", 0) or 0)
                con.close()
                try:
                    sock.sendall((json.dumps({
                        "action": "passkey_revoke_result",
                        "ok": changed > 0,
                        "passkey_id": passkey_id,
                        "reason": "" if changed > 0 else "Passkey not found.",
                    }) + "\n").encode())
                except Exception:
                    pass

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
                    if command in ("help", "?"):
                        response = (
                            "To get more help, type ? or help!\n"
                            "Server command help:\n"
                            "/help or /?  Show this help\n"
                            "/alert <message>  Send an alert to all online users\n"
                            "/create <user> <pass> [email]  Create an account\n"
                            "/invite <user> <email>  Email invite with magic signup link\n"
                            "/accountlimit show  Show max accounts allowed per email (0=unlimited)\n"
                            "/accountlimit set <number>  Set max accounts per email (1=single account)\n"
                            "/ban <user> <MM/DD/YYYY> <reason>  Ban a user until date\n"
                            "/unban <user>  Remove user ban\n"
                            "/del <user>  Delete a user\n"
                            "/admin <user>  Grant admin role\n"
                            "/unadmin <user>  Remove admin role\n"
                            "/banfile <user> <ext|all> [MM/DD/YYYY] <reason>  Ban file uploads\n"
                            "/unbanfile <user> [ext]  Remove file upload ban\n"
                            "/gpolicy show [group]  Show group policy\n"
                            "/gpolicy set <key> <value> [group]  Set group policy key\n"
                            "/gpolicy reset [group]  Reset group policy to defaults\n"
                            "/gpolicy keys  List available group policy keys\n"
                            "/restart  Restart server after configured timeout\n"
                            "/exit  Shut down server after configured timeout"
                        )
                    elif command == "exit" and len(cmd_parts) == 1:
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
                    elif command == "invite" and len(cmd_parts) >= 3:
                        invite_user = str(cmd_parts[1] or "").strip()
                        invite_email = str(cmd_parts[2] or "").strip()
                        if not invite_user or not invite_email or "@" not in invite_email:
                            response = "Error: invite syntax: /invite <username> <email>"
                        elif not smtp_config.get('enabled', False):
                            response = "Error: SMTP email is not enabled on this server."
                        else:
                            token = _create_invite_token(invite_user, invite_email, user)
                            magic_link = (
                                "https://im.tappedin.fm/thrive-messenger-setup/"
                                f"?invite={urllib.parse.quote(token)}"
                                f"&user={urllib.parse.quote(invite_user)}"
                                f"&email={urllib.parse.quote(invite_email)}"
                            )
                            body = (
                                f"{user} invited you to join Thrive Messenger on {server_identity}.\n\n"
                                "Use this magic link to start account creation:\n"
                                f"{magic_link}\n\n"
                                "After account creation, a verification/confirmation email will be sent automatically."
                            )
                            sent = EmailManager.send_email(invite_email, "You're invited to Thrive Messenger", body)
                            if sent:
                                response = f"Invite sent to {invite_email} for user '{invite_user}'."
                            else:
                                response = f"Error: Invite email failed for {invite_email}."
                    elif command == "accountlimit" and len(cmd_parts) >= 2:
                        sub = str(cmd_parts[1] or "").strip().lower()
                        if sub == "show":
                            limit = _max_accounts_per_email()
                            response = f"Current max accounts per email: {limit} (0 means unlimited)."
                        elif sub == "set" and len(cmd_parts) >= 3:
                            try:
                                limit = max(0, int(str(cmd_parts[2]).strip()))
                                _set_server_setting("max_accounts_per_email", str(limit))
                                response = f"Updated max accounts per email to {limit}."
                            except Exception:
                                response = "Error: accountlimit set requires a non-negative integer."
                        else:
                            response = "Error: accountlimit syntax: /accountlimit show OR /accountlimit set <number>"
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
                        response = "Error: Unknown command or incorrect syntax. To get more help, type ? or help!"
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
                    is_bot = _is_registered_bot(uname)
                    bot_session = _bot_session_snapshot(uname) if is_bot else None
                    directory.append({
                        "user": uname,
                        "online": _is_online_user(uname),
                        "status_text": _status_for_user(uname),
                        "is_admin": uname in admins,
                        "is_contact": uname in user_contacts,
                        "is_blocked": user_contacts.get(uname, 0) == 1,
                        "server": server_identity,
                        "is_bot": is_bot,
                        "bot_origin": "local" if _is_virtual_bot(uname) else ("external" if is_bot else "user"),
                        "bot_auth_type": _bot_auth_type(uname) if is_bot else "",
                        "bot_session": bot_session,
                    })
                try: sock.sendall((json.dumps({"action": "user_directory_response", "users": directory}) + "\n").encode())
                except: pass

            elif action == "register_bot_session":
                if not _can_user_use_feature(user, "bot_mesh"):
                    _deny_feature("bot_mesh", "bot_session_registered")
                    continue
                if not _is_registered_bot(user):
                    try:
                        sock.sendall((json.dumps({"action": "bot_session_registered", "ok": False, "reason": "Only bot accounts can register bot sessions."}) + "\n").encode())
                    except Exception:
                        pass
                    continue
                capabilities = msg.get("capabilities", [])
                transports = msg.get("transports", [])
                moderation = msg.get("moderation", {})
                if not isinstance(capabilities, list):
                    capabilities = []
                if not isinstance(transports, list):
                    transports = []
                if not isinstance(moderation, dict):
                    moderation = {}
                moderation_kinds = moderation.get("kinds", [])
                if not isinstance(moderation_kinds, list):
                    moderation_kinds = []
                moderation_cfg = {
                    "enabled": bool(moderation.get("enabled", False)),
                    "kinds": [str(v).strip().lower() for v in moderation_kinds if str(v).strip()],
                    "auto_report": bool(moderation.get("auto_report", True)),
                    "notify_user": str(moderation.get("notify_user", "") or "").strip(),
                }
                if moderation_cfg.get("enabled") and not _can_user_use_feature(user, "bot_moderation"):
                    moderation_cfg["enabled"] = False
                now = datetime.datetime.utcnow().isoformat()
                with bot_session_lock:
                    bot_session_registry[user] = {
                        "sock": sock,
                        "auth_type": str(msg.get("auth_type", _bot_auth_type(user)) or _bot_auth_type(user)),
                        "runtime": str(msg.get("runtime", "cli") or "cli"),
                        "host_label": str(msg.get("host_label", "") or ""),
                        "platform": str(msg.get("platform", "") or ""),
                        "capabilities": [str(v).strip() for v in capabilities if str(v).strip()],
                        "transports": [str(v).strip() for v in transports if str(v).strip()],
                        "temp_dir": str(msg.get("temp_dir", "") or ""),
                        "accepts_files": bool(msg.get("accepts_files", False)),
                        "supports_delegation": bool(msg.get("supports_delegation", True)),
                        "background": bool(msg.get("background", False)),
                        "moderation": moderation_cfg,
                        "server": server_identity,
                        "connected_at": now,
                        "last_seen": now,
                    }
                with bot_moderation_lock:
                    if moderation_cfg.get("enabled"):
                        bot_moderation_registry[user] = moderation_cfg
                    else:
                        bot_moderation_registry.pop(user, None)
                try:
                    sock.sendall((json.dumps({"action": "bot_session_registered", "ok": True, "session": _bot_session_snapshot(user)}) + "\n").encode())
                except Exception:
                    pass

            elif action == "unregister_bot_session":
                _cleanup_bot_session(user)
                try:
                    sock.sendall((json.dumps({"action": "bot_session_registered", "ok": True, "removed": True, "user": user}) + "\n").encode())
                except Exception:
                    pass

            elif action == "get_bot_mesh_directory":
                if not _can_user_use_feature(user, "bot_mesh"):
                    _deny_feature("bot_mesh", "bot_mesh_directory")
                    continue
                try:
                    sock.sendall((json.dumps({"action": "bot_mesh_directory", "ok": True, "sessions": _active_bot_sessions()}) + "\n").encode())
                except Exception:
                    pass

            elif action in ("bot_mesh_request", "bot_mesh_result", "bot_mesh_status"):
                if not _can_user_use_feature(user, "bot_mesh"):
                    _deny_feature("bot_mesh", action)
                    continue
                target = str(msg.get("to", "") or "").strip()
                if not target:
                    try:
                        sock.sendall((json.dumps({"action": action, "ok": False, "reason": "Target bot is required."}) + "\n").encode())
                    except Exception:
                        pass
                    continue
                with bot_session_lock:
                    target_data = bot_session_registry.get(target)
                if not target_data or not target_data.get("sock"):
                    try:
                        sock.sendall((json.dumps({"action": action, "ok": False, "reason": f"{target} is not connected for bot mesh."}) + "\n").encode())
                    except Exception:
                        pass
                    continue
                request_id = str(msg.get("request_id", "") or str(uuid.uuid4()))
                envelope = {
                    "action": action,
                    "from": user,
                    "to": target,
                    "request_id": request_id,
                    "task": str(msg.get("task", "") or ""),
                    "result": msg.get("result"),
                    "status": str(msg.get("status", "") or ""),
                    "metadata": msg.get("metadata", {}) if isinstance(msg.get("metadata"), dict) else {},
                    "user_context": msg.get("user_context", {}) if isinstance(msg.get("user_context"), dict) else {},
                    "relay_server": server_identity,
                    "sent_at": datetime.datetime.utcnow().isoformat(),
                }
                try:
                    target_data["sock"].sendall((json.dumps(envelope) + "\n").encode())
                    sock.sendall((json.dumps({"action": action, "ok": True, "to": target, "request_id": request_id}) + "\n").encode())
                except Exception:
                    try:
                        sock.sendall((json.dumps({"action": action, "ok": False, "reason": "Bot mesh relay failed."}) + "\n").encode())
                    except Exception:
                        pass

            elif action == "bot_mesh_store_file":
                if not _can_user_use_feature(user, "bot_mesh"):
                    _deny_feature("bot_mesh", "bot_mesh_file_stored")
                    continue
                target = str(msg.get("to", "") or "").strip()
                if not target:
                    try:
                        sock.sendall((json.dumps({"action": "bot_mesh_file_stored", "ok": False, "reason": "Target bot is required."}) + "\n").encode())
                    except Exception:
                        pass
                    continue
                try:
                    meta = _store_bot_mesh_temp_file(
                        user,
                        target,
                        msg.get("filename", ""),
                        msg.get("data", ""),
                        mime=msg.get("mime", ""),
                        request_id=msg.get("request_id", ""),
                    )
                except Exception as e:
                    try:
                        sock.sendall((json.dumps({"action": "bot_mesh_file_stored", "ok": False, "reason": str(e)}) + "\n").encode())
                    except Exception:
                        pass
                    continue
                with bot_session_lock:
                    target_data = bot_session_registry.get(target)
                if target_data and target_data.get("sock"):
                    try:
                        target_data["sock"].sendall((json.dumps({
                            "action": "bot_mesh_file_available",
                            "from": user,
                            "to": target,
                            "file_id": meta["id"],
                            "filename": meta["filename"],
                            "mime": meta["mime"],
                            "size": meta["size"],
                            "request_id": meta["request_id"],
                            "relay_server": server_identity,
                            "created_at": meta["created_at"],
                        }) + "\n").encode())
                    except Exception:
                        pass
                try:
                    sock.sendall((json.dumps({"action": "bot_mesh_file_stored", "ok": True, "file_id": meta["id"], "filename": meta["filename"], "size": meta["size"]}) + "\n").encode())
                except Exception:
                    pass

            elif action == "bot_mesh_fetch_file":
                if not _can_user_use_feature(user, "bot_mesh"):
                    _deny_feature("bot_mesh", "bot_mesh_file_data")
                    continue
                file_id = str(msg.get("file_id", "") or "").strip()
                consume = bool(msg.get("consume", False))
                with bot_temp_file_lock:
                    meta = bot_temp_file_registry.get(file_id)
                if not meta:
                    try:
                        sock.sendall((json.dumps({"action": "bot_mesh_file_data", "ok": False, "reason": "File not found."}) + "\n").encode())
                    except Exception:
                        pass
                    continue
                if user not in (meta.get("from"), meta.get("to")):
                    try:
                        sock.sendall((json.dumps({"action": "bot_mesh_file_data", "ok": False, "reason": "Not authorized for this file."}) + "\n").encode())
                    except Exception:
                        pass
                    continue
                try:
                    with open(meta["path"], "rb") as f:
                        data_b64 = base64.b64encode(f.read()).decode("ascii")
                    sock.sendall((json.dumps({
                        "action": "bot_mesh_file_data",
                        "ok": True,
                        "file_id": file_id,
                        "from": meta.get("from"),
                        "to": meta.get("to"),
                        "filename": meta.get("filename"),
                        "mime": meta.get("mime"),
                        "size": meta.get("size"),
                        "request_id": meta.get("request_id"),
                        "data": data_b64,
                    }) + "\n").encode())
                except Exception as e:
                    try:
                        sock.sendall((json.dumps({"action": "bot_mesh_file_data", "ok": False, "reason": str(e)}) + "\n").encode())
                    except Exception:
                        pass
                    continue
                if consume:
                    with bot_temp_file_lock:
                        removed = bot_temp_file_registry.pop(file_id, None)
                    path = str((removed or {}).get("path", "") or "")
                    if path and os.path.exists(path):
                        try:
                            os.remove(path)
                        except Exception:
                            pass

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

            elif action == "group_room_list":
                try:
                    rooms = group_rooms.list_rooms(DB, user)
                    sock.sendall((json.dumps({"action": "group_room_list_response", "ok": True, "rooms": rooms}) + "\n").encode())
                except Exception as exc:
                    sock.sendall((json.dumps({"action": "group_room_list_response", "ok": False, "reason": str(exc)}) + "\n").encode())

            elif action == "group_room_create":
                try:
                    room = group_rooms.create_room(
                        DB, user, msg.get("name", ""), msg.get("description", ""),
                        msg.get("visibility", "public"), msg.get("expiration", "never"),
                    )
                    sock.sendall((json.dumps({"action": "group_room_result", "ok": True, "event": "created", "room": room}) + "\n").encode())
                    _room_broadcast(room["room_id"], {"action": "group_room_event", "event": "created", "room": room})
                except Exception as exc:
                    sock.sendall((json.dumps({"action": "group_room_result", "ok": False, "reason": str(exc)}) + "\n").encode())

            elif action == "group_room_join":
                try:
                    room = group_rooms.join_room(DB, str(msg.get("room_id", "")), user)
                    payload = {"action": "group_room_event", "event": "joined", "room": room, "username": user}
                    _room_broadcast(room["room_id"], payload)
                    sock.sendall((json.dumps({"action": "group_room_result", "ok": True, "event": "joined", "room": room}) + "\n").encode())
                except Exception as exc:
                    sock.sendall((json.dumps({"action": "group_room_result", "ok": False, "reason": str(exc)}) + "\n").encode())

            elif action == "group_room_leave":
                room_id = str(msg.get("room_id", ""))
                try:
                    deleted = group_rooms.leave_room(DB, room_id, user)
                    _room_broadcast(room_id, {"action": "group_room_event", "event": "left", "room_id": room_id, "username": user})
                    sock.sendall((json.dumps({"action": "group_room_result", "ok": True, "event": "left", "room_id": room_id, "deleted": deleted}) + "\n").encode())
                except Exception as exc:
                    sock.sendall((json.dumps({"action": "group_room_result", "ok": False, "reason": str(exc)}) + "\n").encode())

            elif action == "group_room_open":
                room_id = str(msg.get("room_id", ""))
                try:
                    room = group_rooms.get_room(DB, room_id, user)
                    members = group_rooms.list_members(DB, room_id)
                    messages = group_rooms.history(DB, room_id, user, msg.get("limit", 100))
                    sock.sendall((json.dumps({"action": "group_room_open_response", "ok": True, "room": room, "members": members, "messages": messages}) + "\n").encode())
                except Exception as exc:
                    sock.sendall((json.dumps({"action": "group_room_open_response", "ok": False, "reason": str(exc)}) + "\n").encode())

            elif action == "group_room_message":
                room_id = str(msg.get("room_id", ""))
                try:
                    room = group_rooms.get_room(DB, room_id, user)
                    if not _fetch_group_policy("group", room["name"]).get("allow_group_text", True):
                        raise group_rooms.GroupRoomError("Room messages are disabled by server policy.")
                    item = group_rooms.add_message(DB, room_id, user, msg.get("body", ""))
                    _room_broadcast(room_id, {"action": "group_room_message", "message": item})
                except Exception as exc:
                    sock.sendall((json.dumps({"action": "group_room_result", "ok": False, "reason": str(exc)}) + "\n").encode())

            elif action == "group_room_file":
                room_id = str(msg.get("room_id", ""))
                filename = os.path.basename(str(msg.get("filename", "")))
                encoded = str(msg.get("data", ""))
                try:
                    raw_size = len(base64.b64decode(encoded, validate=True))
                    policy = _fetch_group_policy("group", msg.get("room_name", ""))
                    if not policy.get("allow_group_files", True):
                        raise group_rooms.GroupRoomError("Room files are disabled by server policy.")
                    if raw_size > int(policy.get("max_group_file_size_bytes", 52428800)):
                        raise group_rooms.GroupRoomError("The file exceeds this room's size limit.")
                    item = group_rooms.add_message(DB, room_id, user, "", "file", filename)
                    _room_broadcast(room_id, {"action": "group_room_file", "message": item, "data": encoded})
                except Exception as exc:
                    sock.sendall((json.dumps({"action": "group_room_result", "ok": False, "reason": str(exc)}) + "\n").encode())

            elif action == "group_room_set_role":
                room_id = str(msg.get("room_id", ""))
                try:
                    group_rooms.set_member_role(DB, room_id, user, str(msg.get("username", "")), str(msg.get("role", "")))
                    members = group_rooms.list_members(DB, room_id)
                    _room_broadcast(room_id, {"action": "group_room_members", "room_id": room_id, "members": members})
                except Exception as exc:
                    sock.sendall((json.dumps({"action": "group_room_result", "ok": False, "reason": str(exc)}) + "\n").encode())

            elif action == "group_room_add_member":
                room_id = str(msg.get("room_id", ""))
                try:
                    group_rooms.add_member(DB, room_id, user, str(msg.get("username", "")), str(msg.get("role", "user")))
                    members = group_rooms.list_members(DB, room_id)
                    _room_broadcast(room_id, {"action": "group_room_members", "room_id": room_id, "members": members})
                except Exception as exc:
                    sock.sendall((json.dumps({"action": "group_room_result", "ok": False, "reason": str(exc)}) + "\n").encode())

            elif action == "group_room_update":
                room_id = str(msg.get("room_id", ""))
                try:
                    room = group_rooms.update_room(DB, room_id, user, msg.get("changes", {}))
                    _room_broadcast(room_id, {"action": "group_room_event", "event": "updated", "room": room})
                    sock.sendall((json.dumps({"action": "group_room_result", "ok": True, "event": "updated", "room": room}) + "\n").encode())
                except Exception as exc:
                    sock.sendall((json.dumps({"action": "group_room_result", "ok": False, "reason": str(exc)}) + "\n").encode())

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

            elif action == "voice_call_request":
                if not _can_user_use_feature(user, "voice_call"):
                    _deny_feature("voice_call", "voice_call_event"); continue
                target = str(msg.get("to", "")).strip()
                with lock: target_sock = clients.get(target)
                blocked = False
                if target:
                    con = sqlite3.connect(DB); blocked_row = con.execute("SELECT blocked FROM contacts WHERE owner=? AND contact=?", (target, user)).fetchone(); con.close()
                    blocked = bool(blocked_row and blocked_row[0])
                if not target_sock or target == user or blocked or _user_has_direct_call(user) or _user_has_direct_call(target):
                    sock.sendall((json.dumps({"action": "voice_call_event", "event": "failed", "reason": "The user is unavailable."}) + "\n").encode()); continue
                call_id = str(uuid.uuid4()); group_key = f"direct:{call_id}"
                with group_call_lock: group_call_sessions[group_key] = {"mode": "voice", "participants": {user}, "pending": target, "direct": True}
                target_sock.sendall((json.dumps({"action": "voice_call_incoming", "call_id": call_id, "from": user}) + "\n").encode())
                sock.sendall((json.dumps({"action": "voice_call_event", "event": "ringing", "call_id": call_id, "with": target}) + "\n").encode())
                expiry = threading.Timer(60, _expire_direct_call, args=(call_id,)); expiry.daemon = True; expiry.start()

            elif action == "voice_call_accept":
                call_id = str(msg.get("call_id", "")); group_key = f"direct:{call_id}"
                with group_call_lock:
                    data = group_call_sessions.get(group_key)
                    if not data or data.get("pending") != user: data = None
                    else: data["participants"].add(user); data.pop("pending", None); participants = set(data["participants"])
                if not data: continue
                for participant in participants:
                    with lock: target_sock = clients.get(participant)
                    if target_sock: target_sock.sendall((json.dumps({"action": "voice_call_event", "event": "accepted", "call_id": call_id, "with": next((name for name in participants if name != participant), "")}) + "\n").encode())

            elif action in ("voice_call_decline", "voice_call_end"):
                call_id = str(msg.get("call_id", "")); group_key = f"direct:{call_id}"
                with group_call_lock:
                    data = group_call_sessions.get(group_key)
                    participants = set((data or {}).get("participants", set())); pending = (data or {}).get("pending")
                    if pending: participants.add(pending)
                    if not data or user not in participants: continue
                    group_call_sessions.pop(group_key, None)
                for participant in participants:
                    if participant == user: continue
                    with lock: target_sock = clients.get(participant)
                    if target_sock: target_sock.sendall((json.dumps({"action": "voice_call_event", "event": "declined" if action.endswith("decline") else "ended", "call_id": call_id, "with": user}) + "\n").encode())

            elif action == "group_call_join":
                if not _can_user_use_feature(user, "group_call"):
                    _deny_feature("group_call", "group_call_result")
                    continue
                group = str(msg.get("group", "")).strip()
                mode = str(msg.get("mode", "voice") or "voice").strip().lower()
                if mode != "voice":
                    mode = "voice"
                if not group:
                    try:
                        sock.sendall((json.dumps({"action": "group_call_result", "ok": False, "reason": "Missing group name."}) + "\n").encode())
                    except Exception:
                        pass
                    continue
                # Enforce global/group call policy when configured.
                policy = _fetch_group_policy(scope="group", group_name=group)
                if mode == "voice" and not policy.get("allow_group_voice", True):
                    sock.sendall((json.dumps({"action": "group_call_result", "ok": False, "group": group, "reason": "Group voice calls are disabled."}) + "\n").encode())
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
                    sock.sendall((json.dumps({"action": "group_call_result", "ok": True, "event": "joined", "group": group, "mode": mode}) + "\n").encode())
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
                    sock.sendall((json.dumps({"action": "group_call_result", "ok": True, "event": "left", "group": group}) + "\n").encode())
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
                    sock.sendall((json.dumps({"action": "group_call_signal_result", "ok": False, "reason": "Group and target are required."}) + "\n").encode())
                    continue
                try:
                    room = group_rooms.get_room_by_name(DB, group, user)
                    if not room.get("role") or not group_rooms.can(DB, room["room_id"], user, "join_voice"):
                        raise group_rooms.GroupRoomError("Your room role cannot join voice.")
                except Exception as exc:
                    sock.sendall((json.dumps({"action": "group_call_result", "ok": False, "group": group, "reason": str(exc)}) + "\n").encode())
                    continue
                try:
                    room = group_rooms.get_room_by_name(DB, group, user)
                    if not room.get("role") or not group_rooms.can(DB, room["room_id"], user, "join_voice"):
                        raise group_rooms.GroupRoomError("Your room role cannot join voice.")
                except Exception as exc:
                    sock.sendall((json.dumps({"action": "group_call_result", "ok": False, "group": group, "reason": str(exc)}) + "\n").encode())
                    continue
                if not signal_type or len(signal_type) > 64 or not isinstance(signal_data, dict):
                    sock.sendall((json.dumps({"action": "group_call_signal_result", "ok": False, "reason": "Invalid signaling payload."}) + "\n").encode())
                    continue
                if len(json.dumps(signal_data, ensure_ascii=False)) > 65536:
                    sock.sendall((json.dumps({"action": "group_call_signal_result", "ok": False, "reason": "Signaling payload is too large."}) + "\n").encode())
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

            elif action == "group_call_audio":
                group = str(msg.get("group", "")).strip()
                feature_key = "voice_call" if group.startswith("direct:") else "group_call"
                if not _can_user_use_feature(user, feature_key):
                    continue
                encoded = str(msg.get("data", ""))
                try:
                    raw = base64.b64decode(encoded, validate=True)
                except Exception:
                    continue
                if not group or not raw or len(raw) > 32768:
                    continue
                with group_call_lock:
                    participants = set((group_call_sessions.get(group) or {}).get("participants", set()))
                if user not in participants:
                    continue
                _group_call_broadcast(group, {"action": "group_call_audio", "group": group, "from": user, "data": encoded}, exclude=user)

            elif action == "msg":
                to, frm = msg["to"], msg["from"]
                if _is_registered_bot(to) and not _can_user_use_feature(user, "bots"):
                    sock.sendall(json.dumps({"action": "msg_failed", "to": to, "reason": "Bot messaging is disabled for your account."}).encode() + b"\n")
                    continue
                body = str(msg.get("msg", "") or "")
                if bot_runtime_config.get("moderation_watch_direct_messages", True):
                    spam = _spam_signal_summary(body)
                    _emit_bot_moderation_event("direct_message", {
                        "from": frm,
                        "to": to,
                        "message_excerpt": _moderation_excerpt(body, int(bot_runtime_config.get("moderation_excerpt_limit", 280) or 280)),
                        "message_length": len(body),
                        "spam_score": spam.get("score", 0),
                        "spam_reasons": spam.get("reasons", []),
                        "flagged": bool(spam.get("flagged", False)),
                        "from_is_guest": _is_guest_like_username(frm),
                        "to_is_bot": _is_registered_bot(to),
                        "sent_at": datetime.datetime.utcnow().isoformat(),
                    })
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

                if bot_runtime_config.get("moderation_watch_file_offers", True):
                    _emit_bot_moderation_event("file_offer", {
                        "from": user,
                        "to": to,
                        "transfer_id": transfer_id,
                        "file_count": len(files),
                        "files": [
                            {
                                "filename": str(f.get("filename", "") or ""),
                                "size": int(f.get("size", 0) or 0),
                                "mime": str(f.get("mime", "") or ""),
                            }
                            for f in files[:20]
                        ],
                        "from_is_guest": _is_guest_like_username(user),
                        "sent_at": datetime.datetime.utcnow().isoformat(),
                    })

                try:
                    sock_to.sendall((json.dumps({"action": "file_offer", "from": user, "files": files, "transfer_id": transfer_id}) + "\n").encode())
                except:
                    sock.sendall((json.dumps({"action": "file_offer_failed", "to": to, "reason": f"Failed to send offer to {to}."}) + "\n").encode())
                    with transfer_lock: pending_transfers.pop(transfer_id, None)

            elif action == "file_accept":
                transfer_id = msg["transfer_id"]
                with transfer_lock: transfer = pending_transfers.get(transfer_id)
                if not transfer: continue
                if transfer["to"] != user: continue
                file_token = str(uuid.uuid4())
                with transfer_lock: transfer["file_token"] = file_token
                sender = transfer["from"]
                with lock: sock_sender = clients.get(sender)
                if sock_sender:
                    try: sock_sender.sendall((json.dumps({"action": "file_accepted", "transfer_id": transfer_id, "client_transfer_id": transfer.get("client_transfer_id", ""), "to": transfer["to"], "files": transfer["files"], "file_token": file_token}) + "\n").encode())
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
            if user and clients.get(user) is sock:
                del clients[user]
            client_statuses.pop(user, None)
            session_preferences.pop(sock, None)
        _cleanup_bot_session(user)
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

ADMIN_CLI_HELP = """Available commands (with or without a leading slash):
  help or ?
  create <user> <pass> [email]
  accountlimit show | accountlimit set <number>
  gpolicy show [group] | gpolicy set <key> <value> [group] | gpolicy reset [group] | gpolicy keys
  ban <user> <MM/DD/YYYY> <reason> | unban <user> | del <user>
  admin <user> | unadmin <user>
  alert <message>
  banfile <user> <ext|all> [MM/DD/YYYY] <reason> | unbanfile <user> [ext]
  restart | exit"""

def parse_admin_command(command_text):
    text = str(command_text or "").strip()
    if text.startswith("/"):
        text = text[1:].strip()
    parts = text.split()
    if parts and parts[0] == "?":
        parts[0] = "help"
    return parts

def run_cli():
    print("Thrive Server Admin Console")
    print(ADMIN_CLI_HELP)
    while True:
        try:
            cmd_line = input("> ")
            parts = parse_admin_command(cmd_line)
            if not parts: continue
            command = parts[0].lower()
            if command == "help":
                print(ADMIN_CLI_HELP)
            elif command == "exit":
                broadcast_alert(f"The server is shutting down in {shutdown_timeout} seconds.")
                print(f"Server shutting down in {shutdown_timeout} seconds...")
                time.sleep(shutdown_timeout)
                os._exit(0)
            elif command == "restart":
                broadcast_alert(f"The server is restarting in {shutdown_timeout} seconds.")
                print(f"Server restarting in {shutdown_timeout} seconds...")
                time.sleep(shutdown_timeout)
                os.execv(sys.executable, [sys.executable] + sys.argv)
            elif command == "create" and len(parts) in (3, 4): handle_create(parts[1], parts[2], parts[3] if len(parts) == 4 else "")
            elif command == "accountlimit" and len(parts) >= 2:
                if parts[1].lower() == "show":
                    print(f"Current max accounts per email: {_max_accounts_per_email()} (0 means unlimited).")
                elif parts[1].lower() == "set" and len(parts) == 3:
                    limit = max(0, int(parts[2]))
                    _set_server_setting("max_accounts_per_email", str(limit))
                    print(f"Updated max accounts per email to {limit}.")
                else:
                    print("Usage: accountlimit show OR accountlimit set <number>")
            elif command == "gpolicy" and len(parts) >= 2:
                sub = parts[1].lower()
                target_group = parts[-1] if ((sub in ("show", "reset") and len(parts) >= 3) or (sub == "set" and len(parts) >= 5)) else "__global__"
                scope = "group" if target_group != "__global__" else "global"
                if sub == "show":
                    print(json.dumps({"scope": scope, "group": target_group, "policy": _fetch_group_policy(scope, target_group)}, ensure_ascii=False, indent=2))
                elif sub == "keys":
                    print(json.dumps(_policy_schema_payload(), ensure_ascii=False, indent=2))
                elif sub == "set" and len(parts) >= 4:
                    merged = _upsert_group_policy(scope, target_group, {parts[2]: parts[3]}, "local-cli")
                    print(f"Group policy updated for {scope}:{target_group}. {parts[2]}={merged.get(parts[2])}")
                elif sub == "reset":
                    _reset_group_policy(scope, target_group)
                    print(f"Group policy reset for {scope}:{target_group}.")
                else:
                    print("Usage: gpolicy show [group] | set <key> <value> [group] | reset [group] | keys")
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
