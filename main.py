import wx, socket, json, threading, datetime, wx.adv, configparser, ssl, sys, base64, os
from plyer import notification

# --- Dark Mode for MSW ---
try:
    import ctypes
    from ctypes import wintypes
    import winreg

    class WxMswDarkMode:
        _instance = None
        def __new__(cls):
            if cls._instance is None:
                cls._instance = super(WxMswDarkMode, cls).__new__(cls)
                try:
                    cls.dwmapi = ctypes.WinDLL("dwmapi")
                    cls.DWMWA_USE_IMMERSIVE_DARK_MODE = 20
                except (AttributeError, OSError):
                    cls.dwmapi = None
            return cls._instance

        def enable(self, window: wx.Window, enable: bool = True):
            if not self.dwmapi: return False
            try:
                hwnd = window.GetHandle()
                value = wintypes.BOOL(enable)
                hr = self.dwmapi.DwmSetWindowAttribute(hwnd, self.DWMWA_USE_IMMERSIVE_DARK_MODE, ctypes.byref(value), ctypes.sizeof(value))
                if hr != 0:
                    self.DWMWA_USE_IMMERSIVE_DARK_MODE = 19
                    hr = self.dwmapi.DwmSetWindowAttribute(hwnd, self.DWMWA_USE_IMMERSIVE_DARK_MODE, ctypes.byref(value), ctypes.sizeof(value))
                return hr == 0
            except Exception: return False

    def is_windows_dark_mode():
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Software\Microsoft\Windows\CurrentVersion\Themes\Personalize')
            value, _ = winreg.QueryValueEx(key, 'AppsUseLightTheme')
            winreg.CloseKey(key)
            return value == 0
        except (FileNotFoundError, OSError): return False

except (ImportError, ModuleNotFoundError):
    class WxMswDarkMode:
        def enable(self, window: wx.Window, enable: bool = True): return False
    def is_windows_dark_mode(): return False
# --- End of Dark Mode Logic ---

def load_server_config():
    config = configparser.ConfigParser(); config.read('srv.conf')
    return {'host': config.get('server', 'host', fallback='localhost'),'port': config.getint('server', 'port', fallback=5005),'cafile': config.get('server', 'cafile', fallback=None),}
def load_user_config():
    config = configparser.ConfigParser()
    if not config.read('client.conf'): return {'remember': False, 'autologin': False, 'username': '', 'password': '', 'soundpack': 'default', 'chat_logging': {}}
    settings = {'soundpack': 'default', 'chat_logging': {}}
    if 'login' in config:
        settings['username'] = config.get('login', 'username', fallback='');
        try: encoded_pass = config.get('login', 'password', fallback=''); settings['password'] = base64.b64decode(encoded_pass.encode('utf-8')).decode('utf-8')
        except (base64.binascii.Error, UnicodeDecodeError): settings['password'] = ''
        settings['remember'] = config.getboolean('login', 'remember', fallback=False); settings['autologin'] = config.getboolean('login', 'autologin', fallback=False)
        settings['soundpack'] = config.get('login', 'soundpack', fallback='default')
    if 'chat_logging' in config:
        for contact, enabled in config.items('chat_logging'):
            settings['chat_logging'][contact] = (enabled.lower() == 'true')
    return settings
def save_user_config(settings):
    config = configparser.ConfigParser(); encoded_pass = base64.b64encode(settings.get('password', '').encode('utf-8')).decode('utf-8')
    config['login'] = {'username': settings.get('username', ''),'password': encoded_pass,'remember': str(settings.get('remember', False)),'autologin': str(settings.get('autologin', False)), 'soundpack': settings.get('soundpack', 'default')}
    chat_logging_settings = settings.get('chat_logging', {})
    if chat_logging_settings:
        config['chat_logging'] = {k: str(v) for k, v in chat_logging_settings.items()}
    with open('client.conf', 'w') as configfile: config.write(configfile)
SERVER_CONFIG = load_server_config(); ADDR = (SERVER_CONFIG['host'], SERVER_CONFIG['port'])

class ThriveTaskBarIcon(wx.adv.TaskBarIcon):
    def __init__(self, frame):
        super().__init__(); self.frame = frame; icon = wx.Icon(wx.ArtProvider.GetIcon(wx.ART_INFORMATION, wx.ART_OTHER, (16, 16))); self.SetIcon(icon, "Thrive Messenger"); self.Bind(wx.adv.EVT_TASKBAR_LEFT_DCLICK, self.on_restore); self.Bind(wx.EVT_MENU, self.on_restore, id=1); self.Bind(wx.EVT_MENU, self.on_exit, id=2)
    def CreatePopupMenu(self): menu = wx.Menu(); menu.Append(1, "&Restore"); menu.Append(2, "E&xit"); return menu
    def on_restore(self, event): self.frame.restore_from_tray()
    def on_exit(self, event): self.frame.on_exit(None)

class SettingsDialog(wx.Dialog):
    def __init__(self, parent, current_config):
        super().__init__(parent, title="Settings", size=(300, 150)); self.config = current_config
        panel = wx.Panel(self); main_sizer = wx.BoxSizer(wx.VERTICAL); sound_box = wx.StaticBoxSizer(wx.VERTICAL, panel, "&Sound Pack")
        
        dark_mode_on = is_windows_dark_mode()
        if dark_mode_on:
            dark_color = wx.Colour(40, 40, 40); light_text_color = wx.WHITE
            WxMswDarkMode().enable(self); self.SetBackgroundColour(dark_color); panel.SetBackgroundColour(dark_color)
            sound_box.GetStaticBox().SetForegroundColour(light_text_color)
            sound_box.GetStaticBox().SetBackgroundColour(dark_color)
        
        sound_packs = ['default'];
        try:
            if os.path.isdir('sounds'):
                packs = [d for d in os.listdir('sounds') if os.path.isdir(os.path.join('sounds', d))]; sound_packs = sorted(list(set(sound_packs + packs)))
        except Exception as e: print(f"Could not scan for sound packs: {e}")
        self.choice = wx.Choice(sound_box.GetStaticBox(), choices=sound_packs); current_pack = self.config.get('soundpack', 'default')
        if current_pack in sound_packs: self.choice.SetStringSelection(current_pack)
        else: self.choice.SetSelection(0)
        
        sound_box.Add(self.choice, 0, wx.EXPAND | wx.ALL, 5); main_sizer.Add(sound_box, 0, wx.EXPAND | wx.ALL, 5); btn_sizer = wx.StdDialogButtonSizer()
        ok_btn = wx.Button(panel, wx.ID_OK, label="&Apply"); ok_btn.SetDefault(); cancel_btn = wx.Button(panel, wx.ID_CANCEL)
        
        if dark_mode_on:
            self.choice.SetBackgroundColour(dark_color); self.choice.SetForegroundColour(light_text_color)
            ok_btn.SetBackgroundColour(dark_color); ok_btn.SetForegroundColour(light_text_color)
            cancel_btn.SetBackgroundColour(dark_color); cancel_btn.SetForegroundColour(light_text_color)
            
        btn_sizer.AddButton(ok_btn); btn_sizer.AddButton(cancel_btn); btn_sizer.Realize(); main_sizer.Add(btn_sizer, 0, wx.ALIGN_CENTER | wx.ALL, 10); panel.SetSizer(main_sizer)

# --- HELPER: Intelligent SSL Logic ---
def create_secure_socket():
    """
    1. Try SSL with verification (CA file OR System Defaults).
    2. If verification fails (e.g. self-signed cert or missing intermediate), try Unverified SSL.
    3. If SSL fails (protocol error), fallback to Plaintext.
    """
    sock = socket.create_connection(ADDR)
    
    # 1. Setup Context for VERIFIED SSL
    if SERVER_CONFIG['cafile'] and os.path.exists(SERVER_CONFIG['cafile']):
        # User has specific CA
        context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH, cafile=SERVER_CONFIG['cafile'])
    else:
        # Use OS Default Trust Store (Like a Browser)
        context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)

    try:
        # Attempt Strict SSL
        # Note: If SERVER_CONFIG['host'] is an IP, hostname verification will fail for Let's Encrypt certs.
        ssock = context.wrap_socket(sock, server_hostname=SERVER_CONFIG['host'])
        return ssock
    except ssl.SSLCertVerificationError as e:
        print(f"SSL Verification Failed: {e}")
        print("Tip: If using Let's Encrypt, ensure server uses 'fullchain.pem', not 'cert.pem'.")
        print("Retrying with Unverified SSL...")
        sock.close()
        
        # 2. Retry with UNVERIFIED SSL (Encrypted but not trusted)
        sock = socket.create_connection(ADDR)
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        return context.wrap_socket(sock, server_hostname=SERVER_CONFIG['host'])
    except (ssl.SSLError, OSError) as e:
        print(f"SSL Handshake failed ({e}). Retrying with Plaintext...")
        sock.close()
        
        # 3. Fallback to Plaintext
        return socket.create_connection(ADDR)

class ClientApp(wx.App):
    def OnInit(self):
        self.user_config = load_user_config()
        if self.user_config.get('autologin') and self.user_config.get('username'):
            print("Attempting auto-login...")
            success, sock, reason = self.perform_login(self.user_config['username'], self.user_config['password'])
            if success: self.start_main_session(self.user_config['username'], sock); return True
            else: wx.MessageBox(f"Auto-login failed: {reason}", "Login Failed", wx.ICON_ERROR); self.user_config['autologin'] = False; save_user_config(self.user_config)
        return self.show_login_dialog()
    
    def show_login_dialog(self):
        while True:
            dlg = LoginDialog(None, self.user_config)
            result = dlg.ShowModal()
            if result == wx.ID_OK:
                success, sock, _ = self.perform_login(dlg.username, dlg.password)
                if success:
                    if dlg.remember_checked: self.user_config['username'] = dlg.username; self.user_config['password'] = dlg.password; self.user_config['remember'] = True; self.user_config['autologin'] = dlg.autologin_checked
                    else: self.user_config = {'soundpack': self.user_config.get('soundpack', 'default'), 'chat_logging': self.user_config.get('chat_logging', {})}
                    save_user_config(self.user_config); self.start_main_session(dlg.username, sock); return True
            elif result == wx.ID_ABORT:
                success, sock, _ = self.perform_login(dlg.new_username, dlg.new_password)
                if success:
                    self.user_config = {'username': dlg.new_username, 'password': dlg.new_password, 'remember': True, 'autologin': True, 'soundpack': 'default', 'chat_logging': {}}
                    save_user_config(self.user_config); self.start_main_session(dlg.new_username, sock); return True
            else: return False
    
    def perform_login(self, username, password):
        try:
            # Use the intelligent connection helper
            ssock = create_secure_socket()
            
            ssock.sendall(json.dumps({"action":"login","user":username,"pass":password}).encode()+b"\n")
            resp = json.loads(ssock.makefile().readline() or "{}")
            if resp.get("status") == "ok": return True, ssock, "Success"
            else:
                reason = resp.get("reason", "Unknown error"); wx.MessageBox("Login failed: " + reason, "Login Failed", wx.ICON_ERROR); ssock.close(); return False, None, reason
        except Exception as e: wx.MessageBox(f"A connection error occurred: {e}", "Connection Error", wx.ICON_ERROR); return False, None, str(e)
    
    def start_main_session(self, username, sock):
        self.username = username; self.sock = sock; self.frame = MainFrame(self.username, self.sock); self.frame.Show()
        self.play_sound("login.wav"); threading.Thread(target=self.listen_loop, daemon=True).start()
    
    def play_sound(self, sound_file):
        pack = self.user_config.get('soundpack', 'default')
        path = os.path.join('sounds', pack, sound_file)
        if os.path.exists(path): wx.adv.Sound.PlaySound(path, wx.adv.SOUND_ASYNC)
        else:
            default_path = os.path.join('sounds', 'default', sound_file)
            if os.path.exists(default_path): wx.adv.Sound.PlaySound(default_path, wx.adv.SOUND_ASYNC)
            else: print(f"Warning: Sound file '{sound_file}' not found in '{pack}' or 'default' pack.")
    
    def listen_loop(self):
        try:
            for line in self.sock.makefile():
                msg = json.loads(line); act = msg.get("action")
                if act == "contact_list": wx.CallAfter(self.frame.load_contacts, msg["contacts"])
                elif act == "contact_status": wx.CallAfter(self.frame.update_contact_status, msg["user"], msg["online"])
                elif act == "msg": wx.CallAfter(self.frame.receive_message, msg)
                elif act == "msg_failed": wx.CallAfter(self.frame.on_message_failed, msg["to"], msg["reason"])
                elif act == "add_contact_failed": wx.CallAfter(self.frame.on_add_contact_failed, msg["reason"])
                elif act == "add_contact_success": wx.CallAfter(self.frame.on_add_contact_success, msg["contact"])
                elif act == "admin_response": wx.CallAfter(self.frame.on_admin_response, msg["response"])
                elif act == "admin_status_change": wx.CallAfter(self.frame.on_admin_status_change, msg["user"], msg["is_admin"])
                elif act == "server_alert": wx.CallAfter(self.frame.on_server_alert, msg["message"])
                elif act == "banned_kick": wx.CallAfter(self.on_banned); break
        except (IOError, json.JSONDecodeError, ValueError): print("Disconnected from server."); wx.CallAfter(self.on_server_disconnect)
    
    def on_banned(self):
        wx.MessageBox("You have been banned...", "Banned", wx.ICON_ERROR)
        if hasattr(self, 'frame'): self.frame.on_exit(None)
    
    def on_server_disconnect(self):
        if hasattr(self, 'frame') and self.frame.IsShown(): wx.MessageBox("Connection lost...", "Connection Lost", wx.ICON_ERROR)
        if hasattr(self, 'frame') and self.frame: self.frame.is_exiting = True; self.frame.Close()
        self.show_login_dialog()

class CreateAccountDialog(wx.Dialog):
    def __init__(self, parent):
        super().__init__(parent, title="Create New Account", size=(300, 280)); panel = wx.Panel(self); s = wx.BoxSizer(wx.VERTICAL)

        dark_mode_on = is_windows_dark_mode()
        if dark_mode_on:
            dark_color = wx.Colour(40, 40, 40); light_text_color = wx.WHITE
            WxMswDarkMode().enable(self); self.SetBackgroundColour(dark_color); panel.SetBackgroundColour(dark_color)

        user_box = wx.StaticBoxSizer(wx.VERTICAL, panel, "&Username"); self.u_text = wx.TextCtrl(user_box.GetStaticBox()); user_box.Add(self.u_text, 0, wx.EXPAND | wx.ALL, 5)
        pass_box = wx.StaticBoxSizer(wx.VERTICAL, panel, "&Password"); self.p1_text = wx.TextCtrl(pass_box.GetStaticBox(), style=wx.TE_PASSWORD); pass_box.Add(self.p1_text, 0, wx.EXPAND | wx.ALL, 5)
        confirm_box = wx.StaticBoxSizer(wx.VERTICAL, panel, "&Confirm Password"); self.p2_text = wx.TextCtrl(confirm_box.GetStaticBox(), style=wx.TE_PASSWORD); confirm_box.Add(self.p2_text, 0, wx.EXPAND | wx.ALL, 5)
        self.autologin_cb = wx.CheckBox(panel, label="&Log in automatically upon creation"); self.autologin_cb.SetValue(True)
        btn_sizer = wx.StdDialogButtonSizer(); ok_btn = wx.Button(panel, wx.ID_OK, label="&Create"); ok_btn.SetDefault(); ok_btn.Bind(wx.EVT_BUTTON, self.on_create)
        cancel_btn = wx.Button(panel, wx.ID_CANCEL)

        if dark_mode_on:
            for box in [user_box, pass_box, confirm_box]:
                box.GetStaticBox().SetForegroundColour(light_text_color)
                box.GetStaticBox().SetBackgroundColour(dark_color)
            for ctrl in [self.u_text, self.p1_text, self.p2_text]: ctrl.SetBackgroundColour(dark_color); ctrl.SetForegroundColour(light_text_color)
            ok_btn.SetBackgroundColour(dark_color); ok_btn.SetForegroundColour(light_text_color)
            cancel_btn.SetBackgroundColour(dark_color); cancel_btn.SetForegroundColour(light_text_color)

        s.Add(user_box, 0, wx.EXPAND | wx.ALL, 5); s.Add(pass_box, 0, wx.EXPAND | wx.ALL, 5); s.Add(confirm_box, 0, wx.EXPAND | wx.ALL, 5)
        s.Add(self.autologin_cb, 0, wx.ALL, 10)
        btn_sizer.AddButton(ok_btn); btn_sizer.AddButton(cancel_btn); btn_sizer.Realize(); s.Add(btn_sizer, 0, wx.ALIGN_CENTER | wx.ALL, 5); panel.SetSizer(s)
    
    def on_create(self, event):
        u = self.u_text.GetValue().strip(); p1 = self.p1_text.GetValue(); p2 = self.p2_text.GetValue()
        if not u or not p1: wx.MessageBox("Username and password cannot be blank.", "Validation Error", wx.ICON_ERROR); return
        if p1 != p2: wx.MessageBox("Passwords do not match.", "Validation Error", wx.ICON_ERROR); return
        self.EndModal(wx.ID_OK)

class LoginDialog(wx.Dialog):
    def __init__(self, parent, user_config):
        super().__init__(parent, title="Login", size=(300, 320)); self.user_config = user_config
        panel = wx.Panel(self); s = wx.BoxSizer(wx.VERTICAL)
        
        dark_mode_on = is_windows_dark_mode()
        if dark_mode_on:
            dark_color = wx.Colour(40, 40, 40); light_text_color = wx.WHITE
            WxMswDarkMode().enable(self); self.SetBackgroundColour(dark_color); panel.SetBackgroundColour(dark_color)
            
        user_box = wx.StaticBoxSizer(wx.VERTICAL, panel, "&Username")
        self.u = wx.TextCtrl(user_box.GetStaticBox()); user_box.Add(self.u, 0, wx.EXPAND | wx.ALL, 5)
        pass_box = wx.StaticBoxSizer(wx.VERTICAL, panel, "&Password"); self.p = wx.TextCtrl(pass_box.GetStaticBox(), style=wx.TE_PASSWORD | wx.TE_PROCESS_ENTER)
        pass_box.Add(self.p, 0, wx.EXPAND | wx.ALL, 5); self.u.SetValue(self.user_config.get('username', ''));
        if self.user_config.get('remember'): self.p.SetValue(self.user_config.get('password', ''))
        self.remember_cb = wx.CheckBox(panel, label="&Remember me")
        self.autologin_cb = wx.CheckBox(panel, label="Log in &automatically"); self.remember_cb.SetValue(self.user_config.get('remember', False))
        self.autologin_cb.SetValue(self.user_config.get('autologin', False)); self.remember_cb.Bind(wx.EVT_CHECKBOX, self.on_check_remember)
        login_btn = wx.Button(panel, label="&Login"); login_btn.Bind(wx.EVT_BUTTON, self.on_login)
        create_btn = wx.Button(panel, label="&Create Account..."); create_btn.Bind(wx.EVT_BUTTON, self.on_create_account)

        if dark_mode_on:
            for box in [user_box, pass_box]:
                box.GetStaticBox().SetForegroundColour(light_text_color)
                box.GetStaticBox().SetBackgroundColour(dark_color)
            for ctrl in [self.u, self.p]: ctrl.SetBackgroundColour(dark_color); ctrl.SetForegroundColour(light_text_color)
            login_btn.SetBackgroundColour(dark_color); login_btn.SetForegroundColour(light_text_color)
            create_btn.SetBackgroundColour(dark_color); create_btn.SetForegroundColour(light_text_color)
            
        s.Add(user_box, 0, wx.EXPAND | wx.ALL, 5); s.Add(pass_box, 0, wx.EXPAND | wx.ALL, 5)
        s.Add(self.remember_cb, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 10); s.Add(self.autologin_cb, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)
        btn_sizer = wx.BoxSizer(wx.HORIZONTAL); 
        btn_sizer.Add(login_btn, 1, wx.EXPAND | wx.ALL, 2); btn_sizer.Add(create_btn, 1, wx.EXPAND | wx.ALL, 2); s.Add(btn_sizer, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 5)
        self.p.Bind(wx.EVT_TEXT_ENTER, self.on_login); panel.SetSizer(s); self.on_check_remember(None)
    
    def on_create_account(self, event):
        with CreateAccountDialog(self) as dlg:
            if dlg.ShowModal() == wx.ID_OK:
                u, p, auto = dlg.u_text.GetValue(), dlg.p1_text.GetValue(), dlg.autologin_cb.IsChecked()
                try:
                    # Use intelligent connection helper
                    ssock = create_secure_socket()

                    ssock.sendall(json.dumps({"action":"create_account","user":u,"pass":p}).encode()+b"\n")
                    resp = json.loads(ssock.makefile().readline() or "{}")
                    ssock.close()
                    if resp.get("action") == "create_account_success":
                        wx.MessageBox("Account created successfully!", "Success", wx.OK | wx.ICON_INFORMATION)
                        if auto: self.new_username = u; self.new_password = p; self.EndModal(wx.ID_ABORT)
                        else: self.u.SetValue(u); self.p.SetValue("")
                    else: wx.MessageBox("Failed to create account: " + resp.get("reason", "Unknown error"), "Creation Failed", wx.ICON_ERROR)
                except Exception as e: wx.MessageBox(f"A connection error occurred: {e}", "Connection Error", wx.ICON_ERROR)
    
    def on_check_remember(self, event):
        if self.remember_cb.IsChecked(): self.autologin_cb.Enable()
        else: self.autologin_cb.SetValue(False); self.autologin_cb.Disable()
    
    def on_login(self, _):
        u, p = self.u.GetValue(), self.p.GetValue()
        if not u or not p: wx.MessageBox("Username and password cannot be empty.", "Login Error", wx.ICON_ERROR); return
        self.username = u; self.password = p; self.remember_checked = self.remember_cb.IsChecked(); self.autologin_checked = self.autologin_cb.IsChecked(); self.EndModal(wx.ID_OK)

class MainFrame(wx.Frame):
    def update_contact_status(self, user, online):
        for idx in range(self.lv.GetItemCount()):
            if self.lv.GetItemText(idx) == user:
                current_status = self.lv.GetItemText(idx, 1); is_admin = "(Admin)" in current_status
                new_status = "online" if online else "offline"
                if is_admin: new_status += " (Admin)"
                self.lv.SetItem(idx, 1, new_status)
                if online:
                    wx.GetApp().play_sound("contact_online.wav")
                    try: notification.notify("Contact online", f"{user} has come online.", timeout=5)
                    except Exception as e: print(f"Error showing notification: {e}")
                else:
                    wx.GetApp().play_sound("contact_offline.wav")
                    try: notification.notify("Contact offline", f"{user} has gone offline.", timeout=5)
                    except Exception as e: print(f"Error showing notification: {e}")
                break

    def __init__(self, user, sock):
        super().__init__(None, title=f"Thrive Messenger – {user}", size=(400,380)); self.user, self.sock = user, sock; self.task_bar_icon = None; self.is_exiting = False
        self.notifications = []; self.Bind(wx.EVT_CLOSE, self.on_close_window); panel = wx.Panel(self)
        
        dark_mode_on = is_windows_dark_mode()
        if dark_mode_on:
            dark_color = wx.Colour(40, 40, 40); light_text_color = wx.WHITE
            WxMswDarkMode().enable(self); self.SetBackgroundColour(dark_color); panel.SetBackgroundColour(dark_color)
            
        box_contacts = wx.StaticBoxSizer(wx.VERTICAL, panel, "&Contacts")
        self.lv = wx.ListCtrl(box_contacts.GetStaticBox(), style=wx.LC_REPORT); self.lv.InsertColumn(0, "Username", width=120); self.lv.InsertColumn(1, "Status", width=100)
        self.lv.Bind(wx.EVT_CHAR_HOOK, self.on_key); self.lv.Bind(wx.EVT_LIST_ITEM_SELECTED, self.update_button_states); self.lv.Bind(wx.EVT_LIST_ITEM_DESELECTED, self.update_button_states)
        
        if dark_mode_on:
            box_contacts.GetStaticBox().SetForegroundColour(light_text_color)
            box_contacts.GetStaticBox().SetBackgroundColour(dark_color)
            self.lv.SetBackgroundColour(dark_color); self.lv.SetForegroundColour(light_text_color)

        box_contacts.Add(self.lv, 1, wx.EXPAND|wx.ALL, 5)
        self.btn_block = wx.Button(panel, label="&Block"); self.btn_add = wx.Button(panel, label="&Add Contact"); self.btn_send = wx.Button(panel, label="&Start Chat"); self.btn_delete = wx.Button(panel, label="&Delete Contact")
        self.btn_admin = wx.Button(panel, label="Use Ser&ver Side Commands"); self.btn_settings = wx.Button(panel, label="Se&ttings...")
        self.btn_logout = wx.Button(panel, label="L&ogout"); self.btn_exit = wx.Button(panel, label="E&xit")
        
        if dark_mode_on:
            buttons = [self.btn_block, self.btn_add, self.btn_send, self.btn_delete, self.btn_admin, self.btn_settings, self.btn_logout, self.btn_exit]
            for btn in buttons:
                btn.SetBackgroundColour(dark_color)
                btn.SetForegroundColour(light_text_color)
                
        self.btn_block.Bind(wx.EVT_BUTTON, self.on_block_toggle); self.btn_add.Bind(wx.EVT_BUTTON, self.on_add); self.btn_send.Bind(wx.EVT_BUTTON, self.on_send); self.btn_delete.Bind(wx.EVT_BUTTON, self.on_delete)
        self.btn_admin.Bind(wx.EVT_BUTTON, self.on_admin); self.btn_settings.Bind(wx.EVT_BUTTON, self.on_settings)
        self.btn_logout.Bind(wx.EVT_BUTTON, self.on_logout); self.btn_exit.Bind(wx.EVT_BUTTON, self.on_exit)
        accel_entries = [(wx.ACCEL_ALT, ord('B'), self.btn_block.GetId()), (wx.ACCEL_ALT, ord('A'), self.btn_add.GetId()), (wx.ACCEL_ALT, ord('S'), self.btn_send.GetId()), (wx.ACCEL_ALT, ord('D'), self.btn_delete.GetId()), (wx.ACCEL_ALT, ord('V'), self.btn_admin.GetId()), (wx.ACCEL_ALT, ord('T'), self.btn_settings.GetId()), (wx.ACCEL_ALT, ord('O'), self.btn_logout.GetId()), (wx.ACCEL_ALT, ord('X'), self.btn_exit.GetId()),]
        accel_tbl = wx.AcceleratorTable(accel_entries); self.SetAcceleratorTable(accel_tbl)
        gs_main = wx.GridSizer(1, 4, 5, 5); gs_main.Add(self.btn_block, 0, wx.EXPAND); gs_main.Add(self.btn_add, 0, wx.EXPAND); gs_main.Add(self.btn_send, 0, wx.EXPAND); gs_main.Add(self.btn_delete, 0, wx.EXPAND)
        gs_util = wx.GridSizer(1, 4, 5, 5); gs_util.Add(self.btn_admin, 0, wx.EXPAND); gs_util.Add(self.btn_settings, 0, wx.EXPAND); gs_util.Add(self.btn_logout, 0, wx.EXPAND); gs_util.Add(self.btn_exit, 0, wx.EXPAND)
        s = wx.BoxSizer(wx.VERTICAL); s.Add(box_contacts, 1, wx.EXPAND|wx.ALL, 5); s.Add(gs_main, 0, wx.CENTER|wx.ALL, 5); s.Add(gs_util, 0, wx.CENTER|wx.ALL, 5); panel.SetSizer(s)
        self.update_button_states()
    def on_settings(self, event):
        app = wx.GetApp()
        with SettingsDialog(self, app.user_config) as dlg:
            if dlg.ShowModal() == wx.ID_OK:
                selected_pack = dlg.choice.GetStringSelection(); app.user_config['soundpack'] = selected_pack; save_user_config(app.user_config)
                wx.MessageBox("Settings have been applied.", "Settings Saved", wx.OK | wx.ICON_INFORMATION)
    def update_button_states(self, event=None):
        is_selection = self.lv.GetSelectedItemCount() > 0
        self.btn_send.Enable(is_selection); self.btn_delete.Enable(is_selection); self.btn_block.Enable(is_selection)
        if is_selection:
            sel_idx = self.lv.GetFirstSelected(); contact_name = self.lv.GetItemText(sel_idx)
            is_blocked = self.contact_states.get(contact_name, 0); self.btn_block.SetLabel("&Unblock" if is_blocked else "&Block")
        else: self.btn_block.SetLabel("&Block")
        if event: event.Skip()
    def on_add_contact_failed(self, reason): wx.MessageBox(reason, "Add Contact Failed", wx.ICON_ERROR)
    def on_add_contact_success(self, contact_data):
        c = contact_data; self.contact_states[c["user"]] = c["blocked"]; idx = self.lv.InsertItem(self.lv.GetItemCount(), c["user"])
        status = "online" if c["online"] and not c["blocked"] else "offline"
        if c.get("is_admin"): status += " (Admin)"
        self.lv.SetItem(idx, 1, status)
        if c["blocked"]: self.lv.SetItemTextColour(idx, wx.Colour(150,150,150))
        self.update_button_states()
    def on_server_alert(self, message):
        wx.GetApp().play_sound("receive.wav"); wx.MessageBox(message, "Server Alert", wx.OK | wx.ICON_INFORMATION | wx.STAY_ON_TOP)
    def on_add(self, _):
        with wx.TextEntryDialog(self, "Enter the username of the contact you wish to add:", "Add Contact") as dlg:
            if dlg.ShowModal() == wx.ID_OK:
                c = dlg.GetValue().strip()
                if not c: wx.MessageBox("Username cannot be blank.", "Input Error", wx.ICON_ERROR); return
                if c == self.user: wx.MessageBox("You cannot add yourself as a contact.", "Input Error", wx.ICON_ERROR); return
                self.sock.sendall(json.dumps({"action":"add_contact","to":c}).encode()+b"\n")
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
        wx.GetApp().play_sound("logout.wav"); self.Close(); wx.GetApp().show_login_dialog()
    def on_key(self, evt):
        if evt.GetKeyCode() == wx.WXK_RETURN: self.on_send(None)
        else: evt.Skip()
    def on_block_toggle(self, _): 
        sel = self.lv.GetFirstSelected()
        if sel < 0: return
        c = self.lv.GetItemText(sel); blocked = self.contact_states.get(c,0) == 1; action = "unblock_contact" if blocked else "block_contact"; self.sock.sendall(json.dumps({"action":action,"to":c}).encode()+b"\n"); self.contact_states[c] = 0 if blocked else 1; idx_color = wx.NullColour if blocked else wx.Colour(150,150,150); self.lv.SetItemTextColour(sel, idx_color); self.update_button_states()
    def on_delete(self, _):
        sel = self.lv.GetFirstSelected()
        if sel < 0: return
        c = self.lv.GetItemText(sel); self.sock.sendall(json.dumps({"action":"delete_contact","to":c}).encode()+b"\n"); self.lv.DeleteItem(sel); self.contact_states.pop(c, None); self.update_button_states()
    def on_send(self, _): 
        sel = self.lv.GetFirstSelected()
        if sel < 0: return
        c = self.lv.GetItemText(sel);
        app = wx.GetApp(); logging_config = app.user_config.get('chat_logging', {}); is_logging_enabled = logging_config.get(c, False)
        dlg = self.get_chat(c) or ChatDialog(self, c, self.sock, self.user, is_logging_enabled)
        dlg.Show(); dlg.input_ctrl.SetFocus()
    def receive_message(self, msg):
        wx.GetApp().play_sound("receive.wav"); 
        app = wx.GetApp(); logging_config = app.user_config.get('chat_logging', {}); is_logging_enabled = logging_config.get(msg["from"], False)
        dlg = self.get_chat(msg["from"]) or ChatDialog(self, msg["from"], self.sock, self.user, is_logging_enabled)
        dlg.Show(); dlg.append(msg["msg"], msg["from"], msg["time"]); dlg.input_ctrl.SetFocus(); self.RequestUserAttention()
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
        super().__init__(parent, title="Server Side Commands", size=(450, 300)); self.sock = sock; self.Bind(wx.EVT_CHAR_HOOK, self.on_key); s = wx.BoxSizer(wx.VERTICAL)
        
        dark_mode_on = is_windows_dark_mode()
        if dark_mode_on:
            dark_color = wx.Colour(40, 40, 40); light_text_color = wx.WHITE
            WxMswDarkMode().enable(self); self.SetBackgroundColour(dark_color)
            
        self.hist = wx.ListCtrl(self, style=wx.LC_REPORT)
        self.hist.InsertColumn(0, "Server Response", width=200); self.hist.InsertColumn(1, "Time", width=220)
        box_msg = wx.StaticBoxSizer(wx.VERTICAL, self, "&Enter command (e.g., /create user pass)"); self.input_ctrl = wx.TextCtrl(box_msg.GetStaticBox(), style=wx.TE_PROCESS_ENTER)
        btn = wx.Button(self, label="&Send Command")
        
        if dark_mode_on:
            self.hist.SetBackgroundColour(dark_color); self.hist.SetForegroundColour(light_text_color)
            box_msg.GetStaticBox().SetForegroundColour(light_text_color)
            box_msg.GetStaticBox().SetBackgroundColour(dark_color)
            self.input_ctrl.SetBackgroundColour(dark_color); self.input_ctrl.SetForegroundColour(light_text_color)
            btn.SetBackgroundColour(dark_color); btn.SetForegroundColour(light_text_color)
            
        s.Add(self.hist, 1, wx.EXPAND|wx.ALL, 5); self.input_ctrl.Bind(wx.EVT_TEXT_ENTER, self.on_send); box_msg.Add(self.input_ctrl, 0, wx.EXPAND|wx.ALL, 5)
        s.Add(box_msg, 0, wx.EXPAND|wx.ALL, 5); btn.Bind(wx.EVT_BUTTON, self.on_send); s.Add(btn, 0, wx.CENTER|wx.ALL, 5); self.SetSizer(s)
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
    def __init__(self, parent, contact, sock, user, logging_enabled=False):
        super().__init__(parent, title=f"Chat with {contact}", size=(450, 450))
        self.contact, self.sock, self.user = contact, sock, user
        self.Bind(wx.EVT_CHAR_HOOK, self.on_key)
        
        dark_mode_on = is_windows_dark_mode()
        if dark_mode_on:
            dark_color = wx.Colour(40, 40, 40); light_text_color = wx.WHITE
            WxMswDarkMode().enable(self); self.SetBackgroundColour(dark_color)
        
        s = wx.BoxSizer(wx.VERTICAL)
        self.hist = wx.ListCtrl(self, style=wx.LC_REPORT)
        self.hist.InsertColumn(0, "Sender", width=80); self.hist.InsertColumn(1, "Message", width=160); self.hist.InsertColumn(2, "Time", width=180)
        self.save_hist_cb = wx.CheckBox(self, label="Sa&ve chat history")
        self.save_hist_cb.SetValue(logging_enabled); self.save_hist_cb.Bind(wx.EVT_CHECKBOX, self.on_toggle_save)
        box_msg = wx.StaticBoxSizer(wx.VERTICAL, self, "Type &message (Shift+Enter for newline)")
        self.input_ctrl = wx.TextCtrl(box_msg.GetStaticBox(), style=wx.TE_MULTILINE)
        btn = wx.Button(self, label="&Send")
        
        if dark_mode_on:
            self.hist.SetBackgroundColour(dark_color); self.hist.SetForegroundColour(light_text_color)
            box_msg.GetStaticBox().SetForegroundColour(light_text_color)
            box_msg.GetStaticBox().SetBackgroundColour(dark_color)
            self.input_ctrl.SetBackgroundColour(dark_color); self.input_ctrl.SetForegroundColour(light_text_color)
            btn.SetBackgroundColour(dark_color); btn.SetForegroundColour(light_text_color)

        s.Add(self.hist, 1, wx.EXPAND|wx.ALL, 5)
        s.Add(self.save_hist_cb, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)
        self.input_ctrl.Bind(wx.EVT_KEY_DOWN, self.on_input_key)
        box_msg.Add(self.input_ctrl, 1, wx.EXPAND|wx.ALL, 5)
        s.Add(box_msg, 1, wx.EXPAND|wx.ALL, 5)
        
        btn.Bind(wx.EVT_BUTTON, self.on_send)
        s.Add(btn, 0, wx.CENTER|wx.ALL, 5)
        self.SetSizer(s)
    def on_toggle_save(self, event):
        app = wx.GetApp(); is_enabled = self.save_hist_cb.IsChecked()
        if 'chat_logging' not in app.user_config: app.user_config['chat_logging'] = {}
        app.user_config['chat_logging'][self.contact] = is_enabled
        save_user_config(app.user_config)
    def _save_message_to_log(self, formatted_log_line):
        try:
            docs_path = os.path.join(os.path.expanduser('~'), 'Documents')
            log_dir = os.path.join(docs_path, 'ThriveMessenger', 'chats', self.contact)
            os.makedirs(log_dir, exist_ok=True)
            log_file = f"{datetime.date.today().isoformat()}.txt"
            log_path = os.path.join(log_dir, log_file)
            with open(log_path, 'a', encoding='utf-8') as f: f.write(formatted_log_line)
        except Exception as e: print(f"Error: Could not save chat history to '{log_path}'. Reason: {e}")
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
        ts = datetime.datetime.now().isoformat()
        msg = {"action":"msg","to":self.contact,"from":self.user,"time":ts,"msg":txt}
        self.sock.sendall(json.dumps(msg).encode()+b"\n")
        self.append(txt, self.user, ts)
        wx.GetApp().play_sound("send.wav")
        self.input_ctrl.Clear(); self.input_ctrl.SetFocus()
    def append(self, text, sender, ts, is_error=False):
        idx = self.hist.GetItemCount(); self.hist.InsertItem(idx, sender); self.hist.SetItem(idx, 1, text)
        formatted_time = format_timestamp(ts); self.hist.SetItem(idx, 2, formatted_time)
        if is_error: self.hist.SetItemTextColour(idx, wx.RED)
        if self.save_hist_cb.IsChecked():
            log_line = f"[{formatted_time}] {sender}: {text}\n"
            self._save_message_to_log(log_line)
    def append_error(self, reason):
        ts = datetime.datetime.now().isoformat()
        self.append(reason, "System", ts, is_error=True)
        self.input_ctrl.SetFocus()

def main():
    app = ClientApp(False); app.MainLoop()

if __name__ == "__main__": main()