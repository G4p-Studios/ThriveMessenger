import sqlite3, threading, socket, json, datetime, sys, configparser, ssl, os, uuid, base64, time, subprocess, tempfile
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
restart_lock = threading.Lock()
restart_scheduled_for = None

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
    if docs_context:
        system_prompt += (
            " Always verify feature and usage answers against the documentation context provided. "
            "If docs do not confirm a detail, say it is not documented/uncertain instead of guessing."
        )

    payload = {
        "model": model,
        "stream": False,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "system", "content": f"Documentation context:\n{docs_context}" if docs_context else "Documentation context unavailable."},
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
    cur.execute('''CREATE TABLE IF NOT EXISTS file_bans (username TEXT, file_type TEXT, until_date TEXT, reason TEXT, PRIMARY KEY(username, file_type))''')
    # Add file_type column if table was created with an older schema
    fb_cols = [row[1] for row in cur.execute("PRAGMA table_info(file_bans)")]
    if 'file_type' not in fb_cols: cur.execute("ALTER TABLE file_bans ADD COLUMN file_type TEXT")
    if 'until_date' not in fb_cols: cur.execute("ALTER TABLE file_bans ADD COLUMN until_date TEXT")
    if 'reason' not in fb_cols: cur.execute("ALTER TABLE file_bans ADD COLUMN reason TEXT")
    conn.commit()
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
        db.close()
        
        broadcast_contact_status(user, True)
        
        for line in f:
            msg = json.loads(line)
            action = msg.get("action")
            
            if action == "add_contact":
                contact_to_add = msg["to"]
                if contact_to_add == user: 
                    reason = "You cannot add yourself as a contact."
                    sock.sendall((json.dumps({"action": "add_contact_failed", "reason": reason}) + "\n").encode())
                    continue
                con = sqlite3.connect(DB)
                exists = con.execute("SELECT 1 FROM users WHERE username=?", (contact_to_add,)).fetchone()
                is_bot = _is_registered_bot(contact_to_add)
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
                    contact_data = {
                        "user": contact_to_add,
                        "blocked": 0,
                        "online": is_online,
                        "is_admin": contact_to_add in admins,
                        "status_text": contact_status_text,
                        "is_bot": bool(is_bot),
                        "bot_origin": "local" if _is_virtual_bot(contact_to_add) else ("external" if is_bot else "user")
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
                if user not in get_admins(): 
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
                    else:
                        response = "Error: Unknown command or incorrect syntax."
                try: sock.sendall((json.dumps({"action":"admin_response", "response": response})+"\n").encode())
                except: pass

            elif action == "schedule_restart":
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
                known = {uname for (uname,) in all_users}
                for uname in sorted(known | bot_usernames | bot_external_usernames):
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

            elif action == "msg":
                to, frm = msg["to"], msg["from"]
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
        if user: broadcast_contact_status(user, False)

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
