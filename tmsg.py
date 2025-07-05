import wx, socket, json, threading, datetime, wx.adv, configparser, ssl, sys, base64

# ... (All code before ClientApp is correct and unchanged) ...
def load_server_config():
    config = configparser.ConfigParser(); config.read('srv.conf')
    return {'host': config.get('server', 'host', fallback='localhost'),'port': config.getint('server', 'port', fallback=5005),'cafile': config.get('server', 'cafile', fallback=None),}
def load_user_config():
    config = configparser.ConfigParser()
    if not config.read('client.conf'): return {'remember': False, 'autologin': False, 'username': '', 'password': ''}
    settings = {};
    if 'login' in config:
        settings['username'] = config.get('login', 'username', fallback='');
        try: encoded_pass = config.get('login', 'password', fallback=''); settings['password'] = base64.b64decode(encoded_pass.encode('utf-8')).decode('utf-8')
        except (base64.binascii.Error, UnicodeDecodeError): settings['password'] = ''
        settings['remember'] = config.getboolean('login', 'remember', fallback=False); settings['autologin'] = config.getboolean('login', 'autologin', fallback=False)
    return settings
def save_user_config(settings):
    config = configparser.ConfigParser(); encoded_pass = base64.b64encode(settings.get('password', '').encode('utf-8')).decode('utf-8')
    config['login'] = {'username': settings.get('username', ''),'password': encoded_pass,'remember': str(settings.get('remember', False)),'autologin': str(settings.get('autologin', False)),}
    with open('client.conf', 'w') as configfile: config.write(configfile)
SERVER_CONFIG = load_server_config(); ADDR = (SERVER_CONFIG['host'], SERVER_CONFIG['port'])
class ThriveTaskBarIcon(wx.adv.TaskBarIcon):
    def __init__(self, frame): super().__init__(); self.frame = frame; icon = wx.Icon(wx.ArtProvider.GetIcon(wx.ART_INFORMATION, wx.ART_OTHER, (16, 16))); self.SetIcon(icon, "Thrive Messenger"); self.Bind(wx.adv.EVT_TASKBAR_LEFT_DCLICK, self.on_restore); self.Bind(wx.EVT_MENU, self.on_restore, id=1); self.Bind(wx.EVT_MENU, self.on_exit, id=2)
    def CreatePopupMenu(self): menu = wx.Menu(); menu.Append(1, "Restore"); menu.Append(2, "Exit"); return menu
    def on_restore(self, event): self.frame.restore_from_tray()
    def on_exit(self, event): self.frame.on_exit(None)

# --- MODIFIED: This class is reverted to the correct logic ---
class ClientApp(wx.App):
    def OnInit(self):
        self.user_config = load_user_config()
        # Attempt auto-login if configured
        if self.user_config.get('autologin') and self.user_config.get('username'):
            print("Attempting auto-login...")
            success, sock, reason = self.perform_login(
                self.user_config['username'], 
                self.user_config['password']
            )
            if success:
                self.start_main_session(self.user_config['username'], sock)
                return True
            else:
                # If auto-login fails, show error, disable it, and proceed to manual login
                wx.MessageBox(f"Auto-login failed: {reason}", "Login Failed", wx.ICON_ERROR)
                self.user_config['autologin'] = False
                save_user_config(self.user_config)
        
        # If auto-login is not enabled or failed, show the manual login dialog
        return self.show_login_dialog()

    def show_login_dialog(self):
        # This now correctly calls the single-profile LoginDialog
        dlg = LoginDialog(None, self.user_config)
        if dlg.ShowModal() == wx.ID_OK:
            success, sock, _ = self.perform_login(dlg.username, dlg.password)
            if success:
                # Save settings if "Remember me" was checked
                if dlg.remember_checked:
                    self.user_config = {
                        'username': dlg.username,
                        'password': dlg.password,
                        'remember': True,
                        'autologin': dlg.autologin_checked
                    }
                else: # Clear all settings if not checked
                    self.user_config = {}
                save_user_config(self.user_config)
                
                self.start_main_session(dlg.username, sock)
                return True
        return False # Exit app if login dialog is cancelled or closed

    def perform_login(self, username, password):
        """Handles the networking part of logging in. Returns (success, socket, reason)."""
        try:
            context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH, cafile=SERVER_CONFIG['cafile'])
            context.check_hostname = True; context.verify_mode = ssl.CERT_REQUIRED
            sock = socket.create_connection(ADDR)
            ssock = context.wrap_socket(sock, server_hostname=SERVER_CONFIG['host'])
            ssock.sendall(json.dumps({"action":"login","user":username,"pass":password}).encode()+b"\n")
            resp = json.loads(ssock.makefile().readline() or "{}")
            if resp.get("status") == "ok":
                return True, ssock, "Success"
            else:
                reason = resp.get("reason", "Unknown error")
                ssock.close()
                return False, None, reason
        except Exception as e:
            return False, None, str(e)

    def start_main_session(self, username, sock):
        """Initializes the main frame and listener thread after a successful login."""
        self.username = username
        self.sock = sock
        self.frame = MainFrame(self.username, self.sock)
        self.frame.Show()
        wx.adv.Sound.PlaySound("login.wav", wx.adv.SOUND_ASYNC)
        threading.Thread(target=self.listen_loop, daemon=True).start()

    def listen_loop(self):
        try:
            for line in self.sock.makefile():
                msg = json.loads(line); act = msg.get("action")
                if act == "contact_list": wx.CallAfter(self.frame.load_contacts, msg["contacts"])
                elif act == "contact_status": wx.CallAfter(self.frame.update_contact_status, msg["user"], msg["online"])
                elif act == "msg": wx.CallAfter(self.frame.receive_message, msg)
                elif act == "msg_failed": wx.CallAfter(self.frame.on_message_failed, msg["to"], msg["reason"])
                elif act == "admin_response": wx.CallAfter(self.frame.on_admin_response, msg["response"])
                elif act == "admin_status_change": wx.CallAfter(self.frame.on_admin_status_change, msg["user"], msg["is_admin"])
                elif act == "banned_kick": wx.CallAfter(self.on_banned); break
        except (IOError, json.JSONDecodeError, ValueError): print("Disconnected from server."); wx.CallAfter(self.on_server_disconnect)
    
    def on_banned(self):
        wx.MessageBox("You have been banned...", "Banned", wx.ICON_ERROR)
        if hasattr(self, 'frame'): self.frame.on_exit(None)
    
    def on_server_disconnect(self):
        if hasattr(self, 'frame') and self.frame.IsShown():
            wx.MessageBox("Connection lost...", "Connection Lost", wx.ICON_ERROR)
        if hasattr(self, 'frame') and self.frame:
            self.frame.is_exiting = True
            self.frame.Close()
        self.show_login_dialog()

# --- The rest of the file is exactly as you pasted it and is correct ---
class LoginDialog(wx.Dialog):
    def __init__(self, parent, user_config):
        super().__init__(parent, title="Login", size=(300, 280))
        self.user_config = user_config
        
        panel = wx.Panel(self)
        s = wx.BoxSizer(wx.VERTICAL)

        user_box = wx.StaticBoxSizer(wx.VERTICAL, panel, "Username")
        self.u = wx.TextCtrl(user_box.GetStaticBox())
        user_box.Add(self.u, 0, wx.EXPAND | wx.ALL, 5)

        pass_box = wx.StaticBoxSizer(wx.VERTICAL, panel, "Password")
        self.p = wx.TextCtrl(pass_box.GetStaticBox(), style=wx.TE_PASSWORD | wx.TE_PROCESS_ENTER)
        pass_box.Add(self.p, 0, wx.EXPAND | wx.ALL, 5)
        
        self.u.SetValue(self.user_config.get('username', ''))
        if self.user_config.get('remember'):
            self.p.SetValue(self.user_config.get('password', ''))

        s.Add(user_box, 0, wx.EXPAND | wx.ALL, 5)
        s.Add(pass_box, 0, wx.EXPAND | wx.ALL, 5)
        
        self.remember_cb = wx.CheckBox(panel, label="Remember me")
        self.autologin_cb = wx.CheckBox(panel, label="Log in automatically")
        
        self.remember_cb.SetValue(self.user_config.get('remember', False))
        self.autologin_cb.SetValue(self.user_config.get('autologin', False))

        self.remember_cb.Bind(wx.EVT_CHECKBOX, self.on_check_remember)
        
        s.Add(self.remember_cb, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)
        s.Add(self.autologin_cb, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)

        btn = wx.Button(panel, label="Login")
        btn.Bind(wx.EVT_BUTTON, self.on_login)
        self.p.Bind(wx.EVT_TEXT_ENTER, self.on_login)
        s.Add(btn, 0, wx.CENTER | wx.ALL, 5)
        
        panel.SetSizer(s)
        self.on_check_remember(None)

    def on_check_remember(self, event):
        if self.remember_cb.IsChecked():
            self.autologin_cb.Enable()
        else:
            self.autologin_cb.SetValue(False)
            self.autologin_cb.Disable()

    def on_login(self, _):
        u, p = self.u.GetValue(), self.p.GetValue()
        if not u or not p:
            wx.MessageBox("Username and password cannot be empty.", "Login Error", wx.ICON_ERROR); return
        self.username = u; self.password = p
        self.remember_checked = self.remember_cb.IsChecked()
        self.autologin_checked = self.autologin_cb.IsChecked()
        self.EndModal(wx.ID_OK)
class MainFrame(wx.Frame):
    def __init__(self, user, sock):
        super().__init__(None, title=f"Thrive Messenger – {user}", size=(400,380)); self.user, self.sock = user, sock; self.task_bar_icon = None; self.is_exiting = False
        self.Bind(wx.EVT_CLOSE, self.on_close_window); panel = wx.Panel(self); box_contacts = wx.StaticBoxSizer(wx.VERTICAL, panel, "Contacts")
        self.lv = wx.ListCtrl(box_contacts.GetStaticBox(), style=wx.LC_REPORT); self.lv.InsertColumn(0, "Username", width=120); self.lv.InsertColumn(1, "Status", width=100)
        self.lv.Bind(wx.EVT_CHAR_HOOK, self.on_key)
        self.lv.Bind(wx.EVT_LIST_ITEM_SELECTED, self.update_button_states)
        self.lv.Bind(wx.EVT_LIST_ITEM_DESELECTED, self.update_button_states)
        box_contacts.Add(self.lv, 1, wx.EXPAND|wx.ALL, 5)
        self.btn_block = wx.Button(panel, label="Block"); self.btn_add = wx.Button(panel, label="Add Contact")
        self.btn_send = wx.Button(panel, label="Start Chat"); self.btn_delete = wx.Button(panel, label="Delete Contact")
        self.btn_admin = wx.Button(panel, label="Use Server Side Commands"); self.btn_logout = wx.Button(panel, label="Logout"); self.btn_exit = wx.Button(panel, label="Exit")
        self.btn_block.Bind(wx.EVT_BUTTON, self.on_block_toggle); self.btn_add.Bind(wx.EVT_BUTTON, self.on_add)
        self.btn_send.Bind(wx.EVT_BUTTON, self.on_send); self.btn_delete.Bind(wx.EVT_BUTTON, self.on_delete)
        self.btn_admin.Bind(wx.EVT_BUTTON, self.on_admin); self.btn_logout.Bind(wx.EVT_BUTTON, self.on_logout); self.btn_exit.Bind(wx.EVT_BUTTON, self.on_exit)
        gs_main = wx.GridSizer(1, 4, 5, 5)
        gs_main.Add(self.btn_block, 0, wx.EXPAND); gs_main.Add(self.btn_add, 0, wx.EXPAND); gs_main.Add(self.btn_send, 0, wx.EXPAND); gs_main.Add(self.btn_delete, 0, wx.EXPAND)
        gs_util = wx.GridSizer(1, 3, 5, 5)
        gs_util.Add(self.btn_admin, 0, wx.EXPAND); gs_util.Add(self.btn_logout, 0, wx.EXPAND); gs_util.Add(self.btn_exit, 0, wx.EXPAND)
        s = wx.BoxSizer(wx.VERTICAL); s.Add(box_contacts, 1, wx.EXPAND|wx.ALL, 5); s.Add(gs_main, 0, wx.CENTER|wx.ALL, 5); s.Add(gs_util, 0, wx.CENTER|wx.ALL, 5); panel.SetSizer(s)
        self.update_button_states()
    def update_button_states(self, event=None):
        is_selection = self.lv.GetSelectedItemCount() > 0
        self.btn_send.Enable(is_selection); self.btn_delete.Enable(is_selection); self.btn_block.Enable(is_selection)
        if is_selection:
            sel_idx = self.lv.GetFirstSelected(); contact_name = self.lv.GetItemText(sel_idx)
            is_blocked = self.contact_states.get(contact_name, 0); self.btn_block.SetLabel("Unblock" if is_blocked else "Block")
        else: self.btn_block.SetLabel("Block")
        if event: event.Skip()
    def load_contacts(self, contacts):
        self.contact_states = {c["user"]: c["blocked"] for c in contacts}; self.lv.DeleteAllItems()
        for c in contacts:
            idx = self.lv.InsertItem(self.lv.GetItemCount(), c["user"]); status = "online" if c["online"] and not c["blocked"] else "offline"
            if c.get("is_admin"): status += " (Admin)"
            self.lv.SetItem(idx, 1, status)
            if c["blocked"]: self.lv.SetItemTextColour(idx, wx.Colour(150,150,150))
        self.update_button_states()
    def on_admin_status_change(self, user, is_admin):
        for idx in range(self.lv.GetItemCount()):
            if self.lv.GetItemText(idx) == user:
                current_status = self.lv.GetItemText(idx, 1); base_status = current_status.replace(" (Admin)", "")
                new_status = base_status + " (Admin)" if is_admin else base_status; self.lv.SetItem(idx, 1, new_status); break
    def on_admin(self, _): dlg = self.get_admin_dialog() or AdminDialog(self, self.sock); dlg.Show(); dlg.input_ctrl.SetFocus()
    def get_admin_dialog(self):
        for child in self.GetChildren():
            if isinstance(child, AdminDialog): return child
        return None
    def on_admin_response(self, response_text):
        dlg = self.get_admin_dialog()
        if dlg: dlg.append_response(response_text)
    def on_close_window(self, event):
        if self.is_exiting: event.Skip()
        else: self.Hide(); self.task_bar_icon = ThriveTaskBarIcon(self)
    def restore_from_tray(self):
        if self.task_bar_icon: self.task_bar_icon.Destroy(); self.task_bar_icon = None
        self.Show(); self.Raise()
    def on_exit(self, _):
        print("Exiting application...");
        try: self.sock.close()
        except: pass
        if self.task_bar_icon: self.task_bar_icon.Destroy()
        sys.exit(0)
    def on_logout(self, _):
        self.is_exiting = True;
        try: self.sock.sendall(json.dumps({"action":"logout"}).encode()+b"\n")
        except: pass
        wx.adv.Sound.PlaySound("logout.wav", wx.adv.SOUND_ASYNC); self.Close(); wx.GetApp().show_login_dialog()
    def on_key(self, evt):
        if evt.GetKeyCode() == wx.WXK_RETURN: self.on_send(None)
        else: evt.Skip()
    def update_contact_status(self, user, online):
        for idx in range(self.lv.GetItemCount()):
            if self.lv.GetItemText(idx) == user:
                current_status = self.lv.GetItemText(idx, 1); is_admin = "(Admin)" in current_status
                new_status = "online" if online else "offline"
                if is_admin: new_status += " (Admin)"
                self.lv.SetItem(idx, 1, new_status)
                if online: wx.adv.Sound.PlaySound("contact_online.wav", wx.adv.SOUND_ASYNC)
                break
    def on_add(self, _): c = wx.GetTextFromUser("Contact username?","Add Contact"); self.sock.sendall(json.dumps({"action":"add_contact","to":c}).encode()+b"\n"); self.contact_states[c] = 0; idx = self.lv.InsertItem(self.lv.GetItemCount(), c); self.lv.SetItem(idx, 1, "offline"); self.update_button_states()
    def on_block_toggle(self, _): sel = self.lv.GetFirstSelected(); c = self.lv.GetItemText(sel); blocked = self.contact_states.get(c,0) == 1; action = "unblock_contact" if blocked else "block_contact"; self.sock.sendall(json.dumps({"action":action,"to":c}).encode()+b"\n"); self.contact_states[c] = 0 if blocked else 1; idx_color = wx.NullColour if blocked else wx.Colour(150,150,150); self.lv.SetItemTextColour(sel, idx_color); self.update_button_states()
    def on_delete(self, _): sel = self.lv.GetFirstSelected(); c = self.lv.GetItemText(sel); self.sock.sendall(json.dumps({"action":"delete_contact","to":c}).encode()+b"\n"); self.lv.DeleteItem(sel); self.contact_states.pop(c, None); self.update_button_states()
    def on_send(self, _): 
        sel = self.lv.GetFirstSelected()
        if sel < 0: return
        c = self.lv.GetItemText(sel); dlg = self.get_chat(c) or ChatDialog(self, c, self.sock, self.user); dlg.Show(); dlg.input_ctrl.SetFocus()
    def receive_message(self, msg): wx.adv.Sound.PlaySound("receive.wav", wx.adv.SOUND_ASYNC); dlg = self.get_chat(msg["from"]) or ChatDialog(self, msg["from"], self.sock, self.user); dlg.Show(); dlg.append(msg["msg"], msg["from"], msg["time"]); dlg.input_ctrl.SetFocus(); self.RequestUserAttention()
    def on_message_failed(self, to, reason): chat_dlg = self.get_chat(to); (chat_dlg.append_error(reason) if chat_dlg else wx.MessageBox(reason, "Message Failed", wx.OK | wx.ICON_ERROR))
    def get_chat(self, contact):
        for child in self.GetChildren():
            if isinstance(child, ChatDialog) and child.contact == contact: return child
        return None
def get_day_with_suffix(d): return str(d) + "th" if 11 <= d <= 13 else str(d) + {1: "st", 2: "nd", 3: "rd"}.get(d % 10, "th")
def format_timestamp(iso_ts):
    try:
        dt = datetime.datetime.fromisoformat(iso_ts); day_with_suffix = get_day_with_suffix(dt.day)
        formatted_hour = dt.strftime('%I:%M %p').lstrip('0'); return dt.strftime(f'%A, %B {day_with_suffix}, %Y at {formatted_hour}')
    except (ValueError, TypeError): return iso_ts
class AdminDialog(wx.Dialog):
    def __init__(self, parent, sock):
        super().__init__(parent, title="Server Side Commands", size=(450, 300)); self.sock = sock; self.Bind(wx.EVT_CHAR_HOOK, self.on_key); s = wx.BoxSizer(wx.VERTICAL); self.hist = wx.ListCtrl(self, style=wx.LC_REPORT)
        self.hist.InsertColumn(0, "Server Response", width=200); self.hist.InsertColumn(1, "Time", width=220)
        s.Add(self.hist, 1, wx.EXPAND|wx.ALL, 5); box_msg = wx.StaticBoxSizer(wx.VERTICAL, self, "Enter command (e.g., /create user pass)"); self.input_ctrl = wx.TextCtrl(box_msg.GetStaticBox(), style=wx.TE_PROCESS_ENTER); self.input_ctrl.Bind(wx.EVT_TEXT_ENTER, self.on_send); box_msg.Add(self.input_ctrl, 0, wx.EXPAND|wx.ALL, 5)
        s.Add(box_msg, 0, wx.EXPAND|wx.ALL, 5); btn = wx.Button(self, label="Send Command"); btn.Bind(wx.EVT_BUTTON, self.on_send); s.Add(btn, 0, wx.CENTER|wx.ALL, 5); self.SetSizer(s)
    def on_key(self, event):
        if event.GetKeyCode() == wx.WXK_ESCAPE: self.Close()
        else: event.Skip()
    def on_send(self, _):
        cmd = self.input_ctrl.GetValue().strip()
        if not cmd: return
        if not cmd.startswith('/'): self.append_response("Error: Commands must start with /"); return
        msg = {"action":"admin_cmd", "cmd": cmd[1:]}; self.sock.sendall(json.dumps(msg).encode()+b"\n"); self.input_ctrl.Clear(); self.input_ctrl.SetFocus()
    def append_response(self, text):
        ts = datetime.datetime.now().isoformat(); idx = self.hist.GetItemCount(); self.hist.InsertItem(idx, text); self.hist.SetItem(idx, 1, format_timestamp(ts))
        if text.lower().startswith('error'): self.hist.SetItemTextColour(idx, wx.RED)
class ChatDialog(wx.Dialog):
    def __init__(self, parent, contact, sock, user):
        super().__init__(parent, title=f"Chat with {contact}", size=(450, 400)); self.contact, self.sock, self.user = contact, sock, user; self.Bind(wx.EVT_CHAR_HOOK, self.on_key); s = wx.BoxSizer(wx.VERTICAL); self.hist = wx.ListCtrl(self, style=wx.LC_REPORT)
        self.hist.InsertColumn(0, "Sender", width=80); self.hist.InsertColumn(1, "Message", width=160); self.hist.InsertColumn(2, "Time", width=180)
        s.Add(self.hist, 1, wx.EXPAND|wx.ALL, 5); box_msg = wx.StaticBoxSizer(wx.VERTICAL, self, "Type message (Shift+Enter for newline)"); self.input_ctrl = wx.TextCtrl(box_msg.GetStaticBox(), style=wx.TE_MULTILINE); self.input_ctrl.Bind(wx.EVT_KEY_DOWN, self.on_input_key)
        box_msg.Add(self.input_ctrl, 1, wx.EXPAND|wx.ALL, 5); s.Add(box_msg, 1, wx.EXPAND|wx.ALL, 5); btn = wx.Button(self, label="Send"); btn.Bind(wx.EVT_BUTTON, self.on_send); s.Add(btn, 0, wx.CENTER|wx.ALL, 5); self.SetSizer(s)
    def on_input_key(self, event):
        keycode = event.GetKeyCode()
        if keycode in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER):
            if event.ShiftDown(): self.input_ctrl.WriteText('\n')
            else: self.on_send(None)
        else: event.Skip()
    def on_key(self, event):
        if event.GetKeyCode() == wx.WXK_ESCAPE: self.Close()
        else: event.Skip()
    def on_send(self, _):
        txt = self.input_ctrl.GetValue().strip()
        if not txt: return
        ts = datetime.datetime.now().isoformat(); msg = {"action":"msg","to":self.contact,"from":self.user,"time":ts,"msg":txt}; self.sock.sendall(json.dumps(msg).encode()+b"\n"); self.append(txt, self.user, ts); wx.adv.Sound.PlaySound("send.wav", wx.adv.SOUND_ASYNC); self.input_ctrl.Clear(); self.input_ctrl.SetFocus()
    def append(self, text, sender, ts, is_error=False):
        idx = self.hist.GetItemCount(); self.hist.InsertItem(idx, sender); self.hist.SetItem(idx, 1, text); self.hist.SetItem(idx, 2, format_timestamp(ts))
        if is_error: self.hist.SetItemTextColour(idx, wx.RED)
    def append_error(self, reason):
        ts = datetime.datetime.now().isoformat(); self.append(reason, "System", ts, is_error=True); self.input_ctrl.SetFocus()
def main():
    app = ClientApp(False); app.MainLoop()

if __name__ == "__main__": main()