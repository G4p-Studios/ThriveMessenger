import sqlite3, threading, socket, json, datetime, sys, configparser, ssl, os, uuid, base64, time
import smtplib, secrets
from email.mime.text import MIMEText

DB = 'thrive.db'
ADMIN_FILE = 'admins.txt'
clients = {}
client_statuses = {}
lock = threading.Lock()
smtp_config = {}
file_config = {}
shutdown_timeout = 5
max_status_length = 50
pending_transfers = {}
transfer_lock = threading.Lock()
server_port = 0
use_ssl = False

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
    def generate_code():
        return secrets.token_hex(16)  # 32-char hex, 128-bit entropy

# In-memory rate limiting (resets on server restart)
_email_send_times = {}   # email -> [unix_timestamp, ...]
_code_fail_times  = {}   # username -> [unix_timestamp, ...]
_MAIL_LIMIT       = 3    # max outbound emails per address per hour
_MAIL_WINDOW      = 3600
_CODE_FAIL_LIMIT  = 10   # max wrong code attempts per username per hour
_CODE_FAIL_WINDOW = 3600

def _email_allowed(email):
    """Return True if another email may be sent to this address, else False."""
    now = time.time()
    times = [t for t in _email_send_times.get(email, []) if now - t < _MAIL_WINDOW]
    if len(times) >= _MAIL_LIMIT:
        _email_send_times[email] = times; return False
    times.append(now); _email_send_times[email] = times; return True

def _code_attempts_ok(username):
    """Return True if the user has not exceeded the failed-attempt limit."""
    now = time.time()
    fails = [t for t in _code_fail_times.get(username, []) if now - t < _CODE_FAIL_WINDOW]
    _code_fail_times[username] = fails
    return len(fails) < _CODE_FAIL_LIMIT

def _record_code_fail(username):
    now = time.time()
    fails = [t for t in _code_fail_times.get(username, []) if now - t < _CODE_FAIL_WINDOW]
    fails.append(now); _code_fail_times[username] = fails

def _clear_code_fails(username):
    _code_fail_times.pop(username, None)

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
    global file_config
    file_config = {
        'size_limit': config.getint('server', 'size_limit', fallback=0),
        'blackfiles': [ext.strip().lower() for ext in config.get('server', 'blackfiles', fallback='').split(',') if ext.strip()],
    }
    global shutdown_timeout
    shutdown_timeout = config.getint('server', 'shutdown_timeout', fallback=5)
    global max_status_length
    max_status_length = config.getint('server', 'max_status_length', fallback=50)
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
    if 'code_expires' not in existing_cols: cur.execute("ALTER TABLE users ADD COLUMN code_expires TEXT")

    cur.execute('''CREATE TABLE IF NOT EXISTS contacts (owner TEXT, contact TEXT, blocked INTEGER DEFAULT 0, PRIMARY KEY(owner, contact))''')
    cur.execute('''CREATE TABLE IF NOT EXISTS file_bans (username TEXT, file_type TEXT, until_date TEXT, reason TEXT, PRIMARY KEY(username, file_type))''')
    cur.execute('''CREATE TABLE IF NOT EXISTS offline_messages (id INTEGER PRIMARY KEY AUTOINCREMENT, recipient TEXT NOT NULL, sender TEXT NOT NULL, message TEXT NOT NULL, timestamp TEXT NOT NULL)''')
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
        
        # --- Create Account ---
        if action == "create_account":
            new_user = req.get("user")
            new_pass = req.get("pass")
            email = req.get("email", "")
            if not new_user or not new_pass:
                sock.sendall((json.dumps({"action": "create_account_failed", "reason": "Missing fields."}) + "\n").encode())
                return

            con = sqlite3.connect(DB)
            row = con.execute("SELECT is_verified FROM users WHERE LOWER(username)=LOWER(?)", (new_user,)).fetchone()

            # Allow overwriting unverified users only
            if row and (row[0] == 1 or not smtp_config['enabled']):
                sock.sendall((json.dumps({"action": "create_account_failed", "reason": "Username is already taken."}) + "\n").encode())
                con.close(); return

            verified = 1 if not smtp_config['enabled'] else 0
            code = None; expires = None

            if not verified:
                # Block registration with an email already owned by a verified account
                if email:
                    taken = con.execute("SELECT 1 FROM users WHERE LOWER(email)=LOWER(?) AND is_verified=1", (email,)).fetchone()
                    if taken:
                        sock.sendall((json.dumps({"action": "create_account_failed", "reason": "Email is already in use."}) + "\n").encode())
                        con.close(); return
                # Rate-limit outbound verification emails per address
                if not _email_allowed(email or new_user):
                    sock.sendall((json.dumps({"action": "create_account_failed", "reason": "Too many verification attempts. Try again later."}) + "\n").encode())
                    con.close(); return
                code = EmailManager.generate_code()
                expires = (datetime.datetime.utcnow() + datetime.timedelta(hours=1)).isoformat()

            if row:  # Overwriting unverified
                con.execute("UPDATE users SET password=?, email=?, verification_code=?, is_verified=?, code_expires=? WHERE username=?",
                            (new_pass, email, code, verified, expires, new_user))
            else:
                con.execute("INSERT INTO users(username, password, email, verification_code, is_verified, code_expires) VALUES(?,?,?,?,?,?)",
                            (new_user, new_pass, email, code, verified, expires))
            con.commit(); con.close()

            if not verified:
                if EmailManager.send_email(email, "Thrive Messenger - Verify Account", f"Your verification code is: {code}"):
                    sock.sendall((json.dumps({"action": "verify_pending"}) + "\n").encode())
                else:
                    print("Failed to send verification email.")
                    sock.sendall((json.dumps({"action": "create_account_failed", "reason": "Could not send verification email."}) + "\n").encode())
            else:
                sock.sendall((json.dumps({"action": "create_account_success"}) + "\n").encode())
            return

        # --- Verify Account ---
        if action == "verify_account":
            u_ver = req.get("user")
            code_ver = req.get("code")
            if not _code_attempts_ok(u_ver):
                sock.sendall(json.dumps({"status": "error", "reason": "Too many failed attempts. Try again later."}).encode() + b"\n")
                return
            con = sqlite3.connect(DB)
            row = con.execute("SELECT verification_code, code_expires FROM users WHERE username=?", (u_ver,)).fetchone()
            if row and row[0] and row[0] == code_ver:
                if row[1] and datetime.datetime.utcnow().isoformat() > row[1]:
                    con.close(); _record_code_fail(u_ver)
                    sock.sendall(json.dumps({"status": "error", "reason": "Code has expired. Please register again."}).encode() + b"\n")
                else:
                    con.execute("UPDATE users SET is_verified=1, verification_code=NULL, code_expires=NULL WHERE username=?", (u_ver,))
                    con.commit(); con.close(); _clear_code_fails(u_ver)
                    sock.sendall(json.dumps({"status": "ok"}).encode() + b"\n")
            else:
                con.close(); _record_code_fail(u_ver)
                sock.sendall(json.dumps({"status": "error", "reason": "Invalid code."}).encode() + b"\n")
            return

        # --- Request Password Reset ---
        if action == "request_reset":
            ident = req.get("identifier")
            con = sqlite3.connect(DB)
            row = con.execute("SELECT username, email FROM users WHERE username=? OR email=?", (ident, ident)).fetchone()
            if row:
                t_user, t_email = row
                if t_email:
                    if not _email_allowed(t_email):
                        con.close()
                        sock.sendall(json.dumps({"status": "error", "reason": "Too many reset attempts. Try again later."}).encode() + b"\n")
                        return
                    code = EmailManager.generate_code()
                    expires = (datetime.datetime.utcnow() + datetime.timedelta(hours=1)).isoformat()
                    con.execute("UPDATE users SET reset_code=?, code_expires=? WHERE username=?", (code, expires, t_user))
                    con.commit()
                    EmailManager.send_email(t_email, "Thrive Messenger - Password Reset", f"Your password reset code is: {code}")
                    sock.sendall(json.dumps({"status": "ok", "user": t_user}).encode() + b"\n")
                else:
                    sock.sendall(json.dumps({"status": "error", "reason": "No email on file."}).encode() + b"\n")
            else:
                sock.sendall(json.dumps({"status": "ok"}).encode() + b"\n")
            con.close()
            return

        # --- Perform Password Reset ---
        if action == "reset_password":
            t_user = req.get("user")
            t_code = req.get("code")
            new_p = req.get("new_pass")
            if not _code_attempts_ok(t_user):
                sock.sendall(json.dumps({"status": "error", "reason": "Too many failed attempts. Try again later."}).encode() + b"\n")
                return
            con = sqlite3.connect(DB)
            row = con.execute("SELECT reset_code, code_expires FROM users WHERE username=?", (t_user,)).fetchone()
            if row and row[0] and row[0] == t_code:
                if row[1] and datetime.datetime.utcnow().isoformat() > row[1]:
                    con.close(); _record_code_fail(t_user)
                    sock.sendall(json.dumps({"status": "error", "reason": "Code has expired."}).encode() + b"\n")
                else:
                    con.execute("UPDATE users SET password=?, reset_code=NULL, code_expires=NULL WHERE username=?", (new_p, t_user))
                    con.commit(); con.close(); _clear_code_fails(t_user)
                    sock.sendall(json.dumps({"status": "ok"}).encode() + b"\n")
            else:
                con.close(); _record_code_fail(t_user)
                sock.sendall(json.dumps({"status": "error", "reason": "Invalid code."}).encode() + b"\n")
            return

        if action != "login": 
            sock.sendall(b'{"status":"error","reason":"Expected login"}\n')
            return

        db = sqlite3.connect(DB)
        cur = db.cursor()
        cur.execute("SELECT password,banned_until,ban_reason,is_verified FROM users WHERE username=?", (req["user"],))
        row = cur.fetchone()
        
        if not row or row[0] != req["pass"]: 
            sock.sendall(b'{"status":"error","reason":"Invalid credentials"}\n')
            db.close()
            return
            
        bi, br, verified = row[1], row[2], row[3]
        
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
        user = req["user"]
        with lock:
            clients[user] = sock
            client_statuses[user] = "online"

        admins = get_admins()
        rows = db.execute("SELECT contact,blocked FROM contacts WHERE owner=?", (user,)).fetchall()
        with lock:
            contacts = [{"user":c, "blocked":b, "online": (c in clients), "is_admin": (c in admins), "status_text": client_statuses.get(c, "offline") if c in clients else "offline"} for c,b in rows]
        sock.sendall((json.dumps({"action":"contact_list","contacts":contacts})+"\n").encode())
        offline = db.execute("SELECT sender, message, timestamp FROM offline_messages WHERE recipient=? ORDER BY id ASC", (user,)).fetchall()
        if offline:
            msgs = [{"from": s, "msg": m, "time": t} for s, m, t in offline]
            sock.sendall((json.dumps({"action": "offline_messages", "messages": msgs}) + "\n").encode())
            db.execute("DELETE FROM offline_messages WHERE recipient=?", (user,))
            db.commit()
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
                if not exists: 
                    reason = f"User '{contact_to_add}' does not exist."
                    sock.sendall((json.dumps({"action": "add_contact_failed", "reason": reason}) + "\n").encode())
                else:
                    con.execute("INSERT OR IGNORE INTO contacts(owner,contact) VALUES(?,?)", (user, contact_to_add))
                    con.commit()
                    with lock:
                        is_online = contact_to_add in clients
                        contact_status_text = client_statuses.get(contact_to_add, "offline") if is_online else "offline"
                    admins = get_admins()
                    contact_data = {"user": contact_to_add, "blocked": 0, "online": is_online, "is_admin": contact_to_add in admins, "status_text": contact_status_text}
                    sock.sendall((json.dumps({"action": "add_contact_success", "contact": contact_data}) + "\n").encode())
                con.close()
                
            elif action in ("block_contact","unblock_contact"):
                flag = 1 if action=="block_contact" else 0
                con = sqlite3.connect(DB)
                con.execute("UPDATE contacts SET blocked=? WHERE owner=? AND contact=?", (flag,user,msg["to"]))
                con.commit()
                con.close()
                
            elif action == "delete_contact":
                con = sqlite3.connect(DB)
                con.execute("DELETE FROM contacts WHERE owner=? AND contact=?", (user,msg["to"]))
                con.commit()
                con.close()
                
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
                        print(f"Restart initiated by admin: {user}")
                        broadcast_alert(f"The server is restarting in {shutdown_timeout} seconds.")
                        response = f"Server is restarting in {shutdown_timeout} seconds..."
                        try: sock.sendall((json.dumps({"action":"admin_response", "response": response})+"\n").encode())
                        except: pass
                        time.sleep(shutdown_timeout)
                        os.execv(sys.executable, [sys.executable] + sys.argv)
                    elif command == "alert" and len(cmd_parts) >= 2:
                        alert_message = " ".join(cmd_parts[1:])
                        broadcast_alert(alert_message)
                        response = "Alert sent to all online users."
                    elif command == "create" and len(cmd_parts) in (3, 4):
                        email = cmd_parts[3] if len(cmd_parts) == 4 else ""
                        handle_create(cmd_parts[1], cmd_parts[2], email)
                        response = f"User '{cmd_parts[1]}' created."
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
                
            elif action == "server_info":
                con = sqlite3.connect(DB)
                total_users = con.execute("SELECT COUNT(*) FROM users").fetchone()[0]
                con.close()
                with lock: online_count = len(clients)
                info = {"action": "server_info_response", "port": server_port, "ssl": use_ssl, "total_users": total_users, "online_users": online_count, "size_limit": file_config.get('size_limit', 0), "blackfiles": file_config.get('blackfiles', []), "max_status_length": max_status_length}
                try: sock.sendall((json.dumps(info) + "\n").encode())
                except: pass

            elif action == "user_directory":
                con = sqlite3.connect(DB)
                all_users = con.execute("SELECT username FROM users WHERE is_verified=1").fetchall()
                user_contacts = {row[0]: row[1] for row in con.execute("SELECT contact, blocked FROM contacts WHERE owner=?", (user,)).fetchall()}
                con.close()
                admins = get_admins()
                directory = []
                with lock:
                    for (uname,) in all_users:
                        is_online = uname in clients
                        directory.append({"user": uname, "online": is_online, "status_text": client_statuses.get(uname, "offline") if is_online else "offline", "is_admin": uname in admins, "is_contact": uname in user_contacts, "is_blocked": user_contacts.get(uname, 0) == 1})
                try: sock.sendall((json.dumps({"action": "user_directory_response", "users": directory}) + "\n").encode())
                except: pass

            elif action == "msg":
                to = msg["to"]
                frm = user  # always use the authenticated identity; never trust client-supplied "from"
                con = sqlite3.connect(DB)
                recipient_has_blocked = con.execute("SELECT blocked FROM contacts WHERE owner=? AND contact=?", (to, frm)).fetchone()
                sender_has_blocked = con.execute("SELECT blocked FROM contacts WHERE owner=? AND contact=?", (frm, to)).fetchone()
                con.close()

                with lock: sock_to = clients.get(to)
                if recipient_has_blocked and recipient_has_blocked[0] == 1:
                    reason = f"Message couldn't be sent because {to} has you blocked."
                elif sender_has_blocked and sender_has_blocked[0] == 1:
                    reason = "You have blocked this contact."
                elif not sock_to:
                    con2 = sqlite3.connect(DB)
                    con2.execute("INSERT INTO offline_messages (recipient, sender, message, timestamp) VALUES (?, ?, ?, ?)", (to, frm, msg["msg"], msg["time"]))
                    con2.commit(); con2.close()
                    reason = None
                else:
                    try:
                        outgoing = {"action": "msg", "from": frm, "to": to, "msg": msg["msg"], "time": msg["time"]}
                        sock_to.sendall((json.dumps(outgoing) + "\n").encode())
                        reason = None
                    except: pass
                if reason:
                    sock.sendall(json.dumps({"action": "msg_failed", "to": to, "reason": reason}).encode() + b"\n")
                    
            elif action == "file_offer":
                to = msg["to"]
                # Strip any path components from filenames before processing or forwarding
                files = [dict(f, filename=os.path.basename(f["filename"])) for f in msg.get("files", [])]

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
                transfer_id = str(uuid.uuid4())  # always server-generated; never trust client-supplied ID
                with transfer_lock:
                    pending_transfers[transfer_id] = {"from": user, "to": to, "files": files}

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
                    try: sock_sender.sendall((json.dumps({"action": "file_accepted", "transfer_id": transfer_id, "to": transfer["to"], "files": transfer["files"]}) + "\n").encode())
                    except: pass

            elif action == "file_decline":
                transfer_id = msg["transfer_id"]
                with transfer_lock: transfer = pending_transfers.pop(transfer_id, None)
                if not transfer: continue
                sender = transfer["from"]
                with lock: sock_sender = clients.get(sender)
                if sock_sender:
                    try: sock_sender.sendall((json.dumps({"action": "file_declined", "transfer_id": transfer_id, "to": transfer["to"], "files": transfer["files"]}) + "\n").encode())
                    except: pass

            elif action == "file_data":
                transfer_id = msg["transfer_id"]
                with transfer_lock: transfer = pending_transfers.get(transfer_id)
                if not transfer: continue
                if transfer["from"] != user: continue  # only the original sender may deliver data
                with transfer_lock: pending_transfers.pop(transfer_id, None)
                recipient = transfer["to"]
                with lock: sock_to = clients.get(recipient)
                if sock_to:
                    # Re-use the sanitized filenames from the accepted offer; ignore client-supplied names in data packet
                    safe_files = [dict(fd, filename=os.path.basename(fd["filename"])) for fd in msg["files"]]
                    try: sock_to.sendall((json.dumps({"action": "file_data", "from": transfer["from"], "files": safe_files}) + "\n").encode())
                    except: pass

            elif action == "set_status":
                status_text = msg.get("status_text", "online")[:max_status_length]
                with lock: client_statuses[user] = status_text
                broadcast_contact_status(user, True)

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
        con.commit()
        print(f"User '{user}' created.")
    else:
        print(f"User '{user}' already exists (case-insensitive match).")
    con.close()

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
    print("Available commands: create, ban, unban, del, admin, unadmin, alert, banfile, unbanfile, restart, exit")
    while True:
        try:
            cmd_line = input("> ").strip()
            parts = cmd_line.split()
            if not parts: continue
            command = parts[0].lower()
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