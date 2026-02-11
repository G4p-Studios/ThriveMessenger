import sqlite3, threading, socket, json, datetime, sys, configparser, ssl, os
import smtplib, random, string
from email.mime.text import MIMEText

DB = 'thrive.db'
ADMIN_FILE = 'admins.txt'
clients = {}
lock = threading.Lock()
smtp_config = {}

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
        return ''.join(random.choices(string.digits, k=length))

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
    conn.commit()
    conn.close()

def broadcast_contact_status(user, online):
    msg = json.dumps({"action":"contact_status","user":user,"online":online}) + "\n"
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
        with lock: clients.pop(user, None)
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
        with lock: clients[user] = sock
        
        admins = get_admins()
        rows = db.execute("SELECT contact,blocked FROM contacts WHERE owner=?", (user,)).fetchall()
        contacts = [{"user":c, "blocked":b, "online": (c in clients), "is_admin": (c in admins)} for c,b in rows]
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
                if not exists: 
                    reason = f"User '{contact_to_add}' does not exist."
                    sock.sendall((json.dumps({"action": "add_contact_failed", "reason": reason}) + "\n").encode())
                else:
                    con.execute("INSERT OR IGNORE INTO contacts(owner,contact) VALUES(?,?)", (user, contact_to_add))
                    con.commit()
                    with lock: is_online = contact_to_add in clients
                    admins = get_admins()
                    contact_data = {"user": contact_to_add, "blocked": 0, "online": is_online, "is_admin": contact_to_add in admins}
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
                        broadcast_alert("The server is shutting down now.")
                        os._exit(0)
                    elif command == "alert" and len(cmd_parts) >= 2:
                        alert_message = " ".join(cmd_parts[1:])
                        broadcast_alert(alert_message)
                        response = "Alert sent to all online users."
                    elif command == "create" and len(cmd_parts) == 3: 
                        handle_create(cmd_parts[1], cmd_parts[2])
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
                    else: 
                        response = "Error: Unknown command or incorrect syntax."
                try: sock.sendall((json.dumps({"action":"admin_response", "response": response})+"\n").encode())
                except: pass
                
            elif action == "msg":
                to, frm = msg["to"], msg["from"]
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
                    reason = f"{to} is offline."
                else:
                    try: 
                        sock_to.sendall((json.dumps(msg)+"\n").encode())
                        reason = None
                    except: pass
                if reason: 
                    sock.sendall(json.dumps({"action": "msg_failed", "to": to, "reason": reason}).encode() + b"\n")
                    
            elif action == "logout": break
    finally:
        try: cs.close()
        except: pass
        with lock:
            if user in clients: del clients[user]
        if user: broadcast_contact_status(user, False)

def serve_loop(config):
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

def handle_create(user, password):
    con = sqlite3.connect(DB)
    # Admin console creation defaults to verified
    con.execute("INSERT OR IGNORE INTO users(username,password,is_verified) VALUES(?,?,1)",(user, password))
    con.commit()
    con.close()
    print(f"User '{user}' created or already exists.")

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
    print("Available commands: create, ban, unban, del, admin, unadmin, alert, exit")
    while True:
        try:
            cmd_line = input("> ").strip()
            parts = cmd_line.split()
            if not parts: continue
            command = parts[0].lower()
            if command == "exit": 
                print("Server shutting down by console command.")
                os._exit(0)
            elif command == "create" and len(parts)==3: handle_create(parts[1], parts[2])
            elif command == "ban" and len(parts)>=4: handle_ban(parts[1], parts[2], " ".join(parts[3:]))
            elif command == "unban" and len(parts)==2: handle_unban(parts[1])
            elif command == "del" and len(parts)==2: handle_delete(parts[1])
            elif command == "admin" and len(parts)==2: add_admin(parts[1])
            elif command == "unadmin" and len(parts)==2: remove_admin(parts[1])
            elif command == "alert" and len(parts)>=2: 
                broadcast_alert(" ".join(parts[1:]))
                print("Alert sent.")
            else: print(f"Unknown command or wrong number of arguments for: '{command}'")
        except (KeyboardInterrupt, EOFError): 
            print("\nExiting.")
            os._exit(0)

def main():
    config = load_config()
    init_db()
    threading.Thread(target=serve_loop, args=(config,), daemon=True).start()
    run_cli()

if __name__=="__main__": main()