import wx, socket, json, threading, datetime, wx.adv, configparser, ssl, sys, os, base64, uuid, subprocess, tempfile, re, time
import keyring

try:
    from accessible_output2.outputs.auto import Auto as _AO2Auto
    _ao2 = _AO2Auto()
    _ao2_available = True
except Exception:
    _ao2 = None
    _ao2_available = False

def speak(text):
    if _ao2_available and _ao2:
        try:
            app = wx.GetApp()
            interrupt = app.user_config.get('interrupt_speech', True) if app else True
            _ao2.speak(text, interrupt=interrupt)
        except Exception: pass

VERSION_TAG = "v2026-alpha16"
if sys.platform == 'win32':
    from winotify import Notification as _WinNotification
else:
    from plyer import notification as _plyer_notification

def show_notification(title, message, timeout=5):
    try:
        if sys.platform == 'win32':
            toast = _WinNotification(app_id="Thrive Messenger", title=title, msg=message, duration="short")
            toast.show()
        else:
            _plyer_notification.notify(title, message, timeout=timeout)
    except Exception as e:
        print(f"Error showing notification: {e}")

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

if sys.platform == 'win32':
    try: ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('Thrive.Thrive_Messenger')
    except Exception: pass

def load_server_config():
    # Now reading connection details from client.conf instead of srv.conf
    config = configparser.ConfigParser(interpolation=None)
    config.read('client.conf')
    return {
        'host': config.get('server', 'host', fallback='msg.thecubed.cc'),
        'port': config.getint('server', 'port', fallback=2005),
        'cafile': config.get('server', 'cafile', fallback=None),
        'max_retries': config.getint('server', 'max_retries', fallback=5),
        'retry_timeout': config.getint('server', 'retry_timeout', fallback=15),
    }

def get_config_dir():
    if sys.platform == 'win32':
        base = os.environ.get('APPDATA', os.path.expanduser('~'))
    elif sys.platform == 'darwin':
        base = os.path.join(os.path.expanduser('~'), 'Library', 'Application Support')
    else:
        base = os.environ.get('XDG_CONFIG_HOME', os.path.join(os.path.expanduser('~'), '.config'))
    config_dir = os.path.join(base, 'ThriveMessenger')
    os.makedirs(config_dir, exist_ok=True)
    return config_dir

def get_settings_path():
    return os.path.join(get_config_dir(), 'user_settings.json')

def _migrate_settings():
    old_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'user_settings.json')
    new_path = get_settings_path()
    if os.path.exists(old_path) and not os.path.exists(new_path):
        try:
            import shutil
            shutil.move(old_path, new_path)
            print(f"Migrated user_settings.json to {new_path}")
        except Exception as e:
            print(f"Could not migrate settings: {e}")

_migrate_settings()

def load_user_config():
    """
    Loads user preferences from user_settings.json and password from OS Keyring.
    """
    settings = {
        'remember': False,
        'autologin': False,
        'username': '',
        'password': '',
        'soundpack': 'default',
        'chat_logging': {},
        'tts_enabled': False
    }

    # 1. Load non-sensitive preferences from JSON
    settings_path = get_settings_path()
    if os.path.exists(settings_path):
        try:
            with open(settings_path, 'r') as f:
                data = json.load(f)
                settings.update(data)
        except (json.JSONDecodeError, OSError):
            print("Could not load user_settings.json, using defaults.")

    # 2. Load password from Keyring if "Remember me" is active
    if settings.get('username') and settings.get('remember'):
        try:
            stored_pass = keyring.get_password("ThriveMessenger", settings['username'])
            if stored_pass:
                settings['password'] = stored_pass
        except Exception as e:
            print(f"Keyring error (load): {e}")
            
    return settings

def save_user_config(settings):
    """
    Saves user preferences to user_settings.json and password to OS Keyring.
    """
    username = settings.get('username', '')
    password = settings.get('password', '')
    remember = settings.get('remember', False)
    
    # 1. Save non-sensitive data to JSON
    data_to_save = settings.copy()
    if 'password' in data_to_save:
        del data_to_save['password'] # Never save password to file

    try:
        with open(get_settings_path(), 'w') as f:
            json.dump(data_to_save, f, indent=4)
    except Exception as e:
        print(f"Error saving settings file: {e}")

    # 2. Manage Keyring
    if username:
        if remember and password:
            try:
                keyring.set_password("ThriveMessenger", username, password)
            except Exception as e:
                print(f"Keyring error (save): {e}")
        else:
            # If remember is False, ensure we remove the credential from the OS manager
            try:
                if keyring.get_password("ThriveMessenger", username):
                    keyring.delete_password("ThriveMessenger", username)
            except Exception as e:
                # Password might not exist, ignore
                pass

def get_conversations_path(username):
    return os.path.join(get_config_dir(), f'conversations_{username}.json')

def load_noncontact_senders(username):
    path = get_conversations_path(username)
    try:
        with open(path, 'r') as f:
            return set(json.load(f))
    except (OSError, json.JSONDecodeError, TypeError):
        return set()

def save_noncontact_senders(username, senders):
    try:
        with open(get_conversations_path(username), 'w') as f:
            json.dump(sorted(senders), f)
    except OSError as e:
        print(f"Could not save conversations: {e}")

def get_noncontact_chat_path(my_username, contact):
    path = os.path.join(get_config_dir(), 'noncontact_messages', my_username)
    os.makedirs(path, exist_ok=True)
    return os.path.join(path, f'{contact}.json')

def load_noncontact_messages(my_username, contact):
    try:
        with open(get_noncontact_chat_path(my_username, contact), 'r', encoding='utf-8') as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return []

def save_noncontact_messages(my_username, contact, messages):
    try:
        with open(get_noncontact_chat_path(my_username, contact), 'w', encoding='utf-8') as f:
            json.dump(messages, f)
    except OSError as e:
        print(f"Could not save messages: {e}")

def delete_noncontact_messages(my_username, contact):
    try: os.remove(get_noncontact_chat_path(my_username, contact))
    except OSError: pass

SERVER_CONFIG = load_server_config()
ADDR = (SERVER_CONFIG['host'], SERVER_CONFIG['port'])

def _get_servers(user_config):
    servers = user_config.get('servers')
    if not servers:
        servers = [{"name": "Official Server", "host": "msg.thecubed.cc", "port": 2005, "primary": True}]
        user_config['servers'] = servers
    return servers

def _apply_active_server(user_config):
    global SERVER_CONFIG, ADDR
    servers = _get_servers(user_config)
    primary = next((s for s in servers if s.get('primary')), servers[0])
    SERVER_CONFIG['host'] = primary['host']
    SERVER_CONFIG['port'] = primary['port']
    ADDR = (SERVER_CONFIG['host'], SERVER_CONFIG['port'])

_IPC_PORT = 48951

def parse_github_tag(tag):
    m = re.match(r'^v(\d{4})-alpha(\d+)(?:\.(\d+))?$', tag)
    if not m: return None
    return (int(m.group(1)) - 2000, 0, int(m.group(2)), int(m.group(3)) if m.group(3) else 0)

def get_program_dir():
    if getattr(sys, 'frozen', False): return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))

def is_installer_install():
    return os.path.exists(os.path.join(get_program_dir(), 'unins000.exe'))

def check_for_update(callback):
    def _check():
        import urllib.request
        try:
            req = urllib.request.Request("https://api.github.com/repos/G4p-Studios/ThriveMessenger/releases/latest",
                headers={"Accept": "application/vnd.github+json", "User-Agent": "ThriveMessenger/" + VERSION_TAG})
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode())
            tag = data.get("tag_name", "")
            remote = parse_github_tag(tag)
            if remote is None: wx.CallAfter(callback, None, None, f"Unrecognized release tag: {tag}"); return
            local = parse_github_tag(VERSION_TAG)
            if local is None: wx.CallAfter(callback, None, None, f"Unrecognized local version tag: {VERSION_TAG}"); return
            if remote != local:
                wx.CallAfter(callback, tag, ".".join(str(x) for x in remote), None)
            else:
                wx.CallAfter(callback, None, None, None)
        except Exception as e:
            wx.CallAfter(callback, None, None, str(e))
    threading.Thread(target=_check, daemon=True).start()

def download_update(url, dest, progress_dlg, callback):
    def _download():
        import urllib.request
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "ThriveMessenger/" + VERSION_TAG})
            with urllib.request.urlopen(req, timeout=120) as resp:
                total = int(resp.headers.get('Content-Length', 0))
                downloaded = 0
                with open(dest, 'wb') as f:
                    while True:
                        chunk = resp.read(65536)
                        if not chunk: break
                        f.write(chunk)
                        downloaded += len(chunk)
                        if total > 0:
                            pct = min(int(downloaded * 100 / total), 100)
                            wx.CallAfter(progress_dlg.Update, pct, f"Downloaded {downloaded // 1024} KB of {total // 1024} KB")
            wx.CallAfter(callback, True, None)
        except Exception as e:
            wx.CallAfter(callback, False, str(e))
    threading.Thread(target=_download, daemon=True).start()

def apply_installer_update(installer_path):
    program_dir = get_program_dir()
    exe_path = os.path.join(program_dir, 'thrive_messenger.exe')
    batch_path = os.path.join(tempfile.gettempdir(), 'thrive_update.cmd')
    with open(batch_path, 'w') as f:
        f.write(f'@echo off\r\n')
        f.write(f'start /wait "" "{installer_path}" /VERYSILENT /CLOSEAPPLICATIONS /NORESTART\r\n')
        f.write(f'start "" "{exe_path}"\r\n')
        f.write(f'del "{installer_path}"\r\n')
        f.write(f'del "%~f0"\r\n')
    subprocess.Popen(['cmd', '/c', batch_path], creationflags=0x08000000)

def apply_zip_update(zip_path):
    program_dir = get_program_dir()
    exe_path = os.path.join(program_dir, 'thrive_messenger.exe')
    pid = os.getpid()
    temp_extract = os.path.join(tempfile.gettempdir(), 'thrive_update_extract')
    batch_path = os.path.join(tempfile.gettempdir(), 'thrive_update.cmd')
    with open(batch_path, 'w') as f:
        f.write(f'@echo off\r\n')
        f.write(f':waitloop\r\n')
        f.write(f'tasklist /fi "PID eq {pid}" 2>NUL | find /i "{pid}" >NUL\r\n')
        f.write(f'if not errorlevel 1 (\r\n')
        f.write(f'    timeout /t 1 /nobreak >NUL\r\n')
        f.write(f'    goto waitloop\r\n')
        f.write(f')\r\n')
        f.write(f'if exist "{temp_extract}" rmdir /s /q "{temp_extract}"\r\n')
        f.write(f'powershell -Command "Expand-Archive -Path \'{zip_path}\' -DestinationPath \'{temp_extract}\' -Force"\r\n')
        f.write(f'xcopy /s /e /y /q "{temp_extract}\\thrive_messenger\\*" "{program_dir}\\"\r\n')
        f.write(f'rmdir /s /q "{temp_extract}"\r\n')
        f.write(f'del "{zip_path}"\r\n')
        f.write(f'start "" "{exe_path}"\r\n')
        f.write(f'del "%~f0"\r\n')
    subprocess.Popen(['cmd', '/c', batch_path], creationflags=0x08000000)

class ThriveTaskBarIcon(wx.adv.TaskBarIcon):
    def __init__(self, frame):
        super().__init__(); self.frame = frame; icon = wx.Icon(wx.ArtProvider.GetIcon(wx.ART_INFORMATION, wx.ART_OTHER, (16, 16))); self.SetIcon(icon, "Thrive Messenger"); self.Bind(wx.adv.EVT_TASKBAR_LEFT_DCLICK, self.on_restore); self.Bind(wx.EVT_MENU, self.on_restore, id=1); self.Bind(wx.EVT_MENU, self.on_exit, id=2)
    def CreatePopupMenu(self): menu = wx.Menu(); menu.Append(1, "&Restore"); menu.Append(2, "E&xit"); return menu
    def on_restore(self, event): self.frame.restore_from_tray()
    def on_exit(self, event): self.frame.on_exit(None)

class SettingsDialog(wx.Dialog):
    def __init__(self, parent, current_config):
        super().__init__(parent, title="Settings", size=(300, 370)); self.config = current_config
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

        self.tts_cb = wx.CheckBox(panel, label="&Read new messages aloud")
        self.tts_cb.SetValue(self.config.get('tts_enabled', True))
        if not _ao2_available:
            self.tts_cb.Enable(False)
            self.tts_cb.SetToolTip("accessible_output2 is not installed")

        self.announce_status_cb = wx.CheckBox(panel, label="Speak online/offline &announcements")
        self.announce_status_cb.SetValue(self.config.get('announce_status', False))
        if not _ao2_available:
            self.announce_status_cb.Enable(False)
            self.announce_status_cb.SetToolTip("accessible_output2 is not installed")

        self.announce_files_cb = wx.CheckBox(panel, label="Speak &file received notifications")
        self.announce_files_cb.SetValue(self.config.get('announce_files', False))
        if not _ao2_available:
            self.announce_files_cb.Enable(False)
            self.announce_files_cb.SetToolTip("accessible_output2 is not installed")

        self.interrupt_speech_cb = wx.CheckBox(panel, label="&Interrupt speech")
        self.interrupt_speech_cb.SetValue(self.config.get('interrupt_speech', True))
        if not _ao2_available:
            self.interrupt_speech_cb.Enable(False)
            self.interrupt_speech_cb.SetToolTip("accessible_output2 is not installed")

        self.btn_chpass = wx.Button(panel, label="C&hange Password...")
        self.btn_chpass.Bind(wx.EVT_BUTTON, self.on_change_password)

        sound_box.Add(self.choice, 0, wx.EXPAND | wx.ALL, 5); main_sizer.Add(sound_box, 0, wx.EXPAND | wx.ALL, 5)
        main_sizer.Add(self.tts_cb, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)
        main_sizer.Add(self.announce_status_cb, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)
        main_sizer.Add(self.announce_files_cb, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)
        main_sizer.Add(self.interrupt_speech_cb, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)
        main_sizer.Add(self.btn_chpass, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)
        btn_sizer = wx.StdDialogButtonSizer()
        ok_btn = wx.Button(panel, wx.ID_OK, label="&Apply"); ok_btn.SetDefault(); cancel_btn = wx.Button(panel, wx.ID_CANCEL)

        if dark_mode_on:
            self.choice.SetBackgroundColour(dark_color); self.choice.SetForegroundColour(light_text_color)
            self.tts_cb.SetForegroundColour(light_text_color); self.tts_cb.SetBackgroundColour(dark_color)
            self.announce_status_cb.SetForegroundColour(light_text_color); self.announce_status_cb.SetBackgroundColour(dark_color)
            self.announce_files_cb.SetForegroundColour(light_text_color); self.announce_files_cb.SetBackgroundColour(dark_color)
            self.interrupt_speech_cb.SetForegroundColour(light_text_color); self.interrupt_speech_cb.SetBackgroundColour(dark_color)
            self.btn_chpass.SetBackgroundColour(dark_color); self.btn_chpass.SetForegroundColour(light_text_color)
            ok_btn.SetBackgroundColour(dark_color); ok_btn.SetForegroundColour(light_text_color)
            cancel_btn.SetBackgroundColour(dark_color); cancel_btn.SetForegroundColour(light_text_color)

        btn_sizer.AddButton(ok_btn); btn_sizer.AddButton(cancel_btn); btn_sizer.Realize(); main_sizer.Add(btn_sizer, 0, wx.ALIGN_CENTER | wx.ALL, 10); panel.SetSizer(main_sizer)

    def on_change_password(self, _):
        with ChangePasswordDialog(self) as dlg:
            if dlg.ShowModal() == wx.ID_OK:
                cur = dlg.cur_ctrl.GetValue(); new = dlg.new_ctrl.GetValue()
                frame = self.GetParent()
                try: frame.sock.sendall((json.dumps({"action": "change_password", "current_pass": cur, "new_pass": new}) + "\n").encode())
                except Exception as e: wx.MessageBox(f"Failed to send request: {e}", "Error", wx.ICON_ERROR)

class ReconnectDialog(wx.Dialog):
    def __init__(self):
        super().__init__(None, title="Connection Lost", style=wx.DEFAULT_DIALOG_STYLE | wx.STAY_ON_TOP)
        self.cancelled = False
        panel = wx.Panel(self); sizer = wx.BoxSizer(wx.VERTICAL)

        dark_mode_on = is_windows_dark_mode()
        if dark_mode_on:
            dark_color = wx.Colour(40, 40, 40); light_text_color = wx.WHITE
            WxMswDarkMode().enable(self); self.SetBackgroundColour(dark_color); panel.SetBackgroundColour(dark_color)

        self.status_label = wx.StaticText(panel, label="Connection to the server was lost.")
        give_up_btn = wx.Button(panel, label="Give Up")
        give_up_btn.Bind(wx.EVT_BUTTON, self.on_give_up)

        if dark_mode_on:
            self.status_label.SetForegroundColour(light_text_color); self.status_label.SetBackgroundColour(dark_color)
            give_up_btn.SetBackgroundColour(dark_color); give_up_btn.SetForegroundColour(light_text_color)

        sizer.Add(self.status_label, 0, wx.ALL, 15)
        sizer.Add(give_up_btn, 0, wx.ALIGN_CENTER | wx.BOTTOM, 10)
        panel.SetSizer(sizer); self.Fit(); self.Centre()

    def set_status(self, text):
        self.status_label.SetLabel(text); self.Layout(); self.Fit()

    def on_give_up(self, _):
        self.cancelled = True; self.EndModal(wx.ID_CANCEL)

class ChangePasswordDialog(wx.Dialog):
    def __init__(self, parent):
        super().__init__(parent, title="Change Password", size=(300, 220))
        panel = wx.Panel(self); sizer = wx.BoxSizer(wx.VERTICAL)
        dark_mode_on = is_windows_dark_mode()
        if dark_mode_on:
            dark_color = wx.Colour(40, 40, 40); light_text_color = wx.WHITE
            WxMswDarkMode().enable(self); self.SetBackgroundColour(dark_color); panel.SetBackgroundColour(dark_color)
        cur_box = wx.StaticBoxSizer(wx.VERTICAL, panel, "&Current Password")
        self.cur_ctrl = wx.TextCtrl(cur_box.GetStaticBox(), style=wx.TE_PASSWORD)
        new_box = wx.StaticBoxSizer(wx.VERTICAL, panel, "&New Password")
        self.new_ctrl = wx.TextCtrl(new_box.GetStaticBox(), style=wx.TE_PASSWORD)
        conf_box = wx.StaticBoxSizer(wx.VERTICAL, panel, "Con&firm New Password")
        self.conf_ctrl = wx.TextCtrl(conf_box.GetStaticBox(), style=wx.TE_PASSWORD)
        btn_sizer = wx.StdDialogButtonSizer()
        ok_btn = wx.Button(panel, wx.ID_OK, label="&Change"); ok_btn.SetDefault()
        cancel_btn = wx.Button(panel, wx.ID_CANCEL)
        ok_btn.Bind(wx.EVT_BUTTON, self.on_ok)
        if dark_mode_on:
            for box in [cur_box, new_box, conf_box]:
                box.GetStaticBox().SetForegroundColour(light_text_color); box.GetStaticBox().SetBackgroundColour(dark_color)
            for ctrl in [self.cur_ctrl, self.new_ctrl, self.conf_ctrl]:
                ctrl.SetBackgroundColour(dark_color); ctrl.SetForegroundColour(light_text_color)
            ok_btn.SetBackgroundColour(dark_color); ok_btn.SetForegroundColour(light_text_color)
            cancel_btn.SetBackgroundColour(dark_color); cancel_btn.SetForegroundColour(light_text_color)
        cur_box.Add(self.cur_ctrl, 0, wx.EXPAND | wx.ALL, 5)
        new_box.Add(self.new_ctrl, 0, wx.EXPAND | wx.ALL, 5)
        conf_box.Add(self.conf_ctrl, 0, wx.EXPAND | wx.ALL, 5)
        sizer.Add(cur_box, 0, wx.EXPAND | wx.ALL, 5)
        sizer.Add(new_box, 0, wx.EXPAND | wx.ALL, 5)
        sizer.Add(conf_box, 0, wx.EXPAND | wx.ALL, 5)
        btn_sizer.AddButton(ok_btn); btn_sizer.AddButton(cancel_btn); btn_sizer.Realize()
        sizer.Add(btn_sizer, 0, wx.ALIGN_CENTER | wx.ALL, 5)
        panel.SetSizer(sizer)
    def on_ok(self, _):
        if not self.cur_ctrl.GetValue():
            wx.MessageBox("Please enter your current password.", "Error", wx.ICON_ERROR); return
        if not self.new_ctrl.GetValue():
            wx.MessageBox("Please enter a new password.", "Error", wx.ICON_ERROR); return
        if self.new_ctrl.GetValue() != self.conf_ctrl.GetValue():
            wx.MessageBox("New passwords do not match.", "Error", wx.ICON_ERROR); return
        self.EndModal(wx.ID_OK)

STATUS_PRESETS = ["online", "offline", "busy", "away", "on the phone", "doing homework", "in the shower", "watching TV", "hiding from the parents", "fixing my PC", "battery about to die"]

class StatusDialog(wx.Dialog):
    def __init__(self, parent, current_status="online"):
        super().__init__(parent, title="Set Status")
        self.panel = panel = wx.Panel(self); self.sizer = s = wx.BoxSizer(wx.VERTICAL)
        self.dark_mode_on = is_windows_dark_mode()
        if self.dark_mode_on:
            self.dark_color = wx.Colour(40, 40, 40); self.light_text_color = wx.WHITE
            WxMswDarkMode().enable(self); self.SetBackgroundColour(self.dark_color); panel.SetBackgroundColour(self.dark_color)
        status_box = wx.StaticBoxSizer(wx.VERTICAL, panel, "&Preset")
        self.choice = wx.Choice(status_box.GetStaticBox(), choices=STATUS_PRESETS + ["Custom..."])
        is_custom = current_status not in STATUS_PRESETS
        if is_custom: self.choice.SetStringSelection("Custom...")
        else: self.choice.SetStringSelection(current_status)
        status_box.Add(self.choice, 0, wx.EXPAND | wx.ALL, 5)
        self.custom_box = wx.StaticBoxSizer(wx.VERTICAL, panel, "S&tatus Text")
        self.status_text = wx.TextCtrl(self.custom_box.GetStaticBox())
        self.status_text.SetValue(current_status if is_custom else "")
        self.custom_box.Add(self.status_text, 0, wx.EXPAND | wx.ALL, 5)
        self.choice.Bind(wx.EVT_CHOICE, self._on_choice)
        s.Add(status_box, 0, wx.EXPAND | wx.ALL, 5); s.Add(self.custom_box, 0, wx.EXPAND | wx.ALL, 5)
        btn_sizer = wx.StdDialogButtonSizer()
        ok_btn = wx.Button(panel, wx.ID_OK, label="&Apply"); ok_btn.SetDefault()
        cancel_btn = wx.Button(panel, wx.ID_CANCEL)
        if self.dark_mode_on:
            for box in [status_box, self.custom_box]:
                box.GetStaticBox().SetForegroundColour(self.light_text_color); box.GetStaticBox().SetBackgroundColour(self.dark_color)
            self.choice.SetBackgroundColour(self.dark_color); self.choice.SetForegroundColour(self.light_text_color)
            self.status_text.SetBackgroundColour(self.dark_color); self.status_text.SetForegroundColour(self.light_text_color)
            ok_btn.SetBackgroundColour(self.dark_color); ok_btn.SetForegroundColour(self.light_text_color)
            cancel_btn.SetBackgroundColour(self.dark_color); cancel_btn.SetForegroundColour(self.light_text_color)
        btn_sizer.AddButton(ok_btn); btn_sizer.AddButton(cancel_btn); btn_sizer.Realize()
        s.Add(btn_sizer, 0, wx.ALIGN_CENTER | wx.ALL, 10); panel.SetSizer(s)
        if not is_custom: self.sizer.Hide(self.custom_box)
        self.panel.Layout()
        self.SetSize((350, 220 if is_custom else 150))
    def _on_choice(self, event):
        sel = self.choice.GetStringSelection()
        if sel == "Custom...":
            self.status_text.SetValue(""); self.sizer.Show(self.custom_box); self.panel.Layout()
            self.SetSize((350, 220)); self.status_text.SetFocus()
        else:
            self.status_text.SetValue(sel); self.sizer.Hide(self.custom_box); self.panel.Layout()
            self.SetSize((350, 150))

def create_secure_socket(timeout=None):
    sock = socket.create_connection(ADDR, timeout=timeout)
    if SERVER_CONFIG['cafile'] and os.path.exists(SERVER_CONFIG['cafile']):
        context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH, cafile=SERVER_CONFIG['cafile'])
    else: context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
    try: return context.wrap_socket(sock, server_hostname=SERVER_CONFIG['host'])
    except ssl.SSLCertVerificationError:
        sock.close(); sock = socket.create_connection(ADDR, timeout=timeout)
        context = ssl.create_default_context(); context.check_hostname = False; context.verify_mode = ssl.CERT_NONE
        return context.wrap_socket(sock, server_hostname=SERVER_CONFIG['host'])
    except (ssl.SSLError, OSError):
        sock.close(); return socket.create_connection(ADDR, timeout=timeout)

class ClientApp(wx.App):
    def OnInit(self):
        self.instance_checker = wx.SingleInstanceChecker("ThriveMessenger-%s" % wx.GetUserId())
        if self.instance_checker.IsAnotherRunning():
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.connect(('127.0.0.1', _IPC_PORT))
                s.sendall(b'restore')
                s.close()
            except Exception:
                pass
            return False
        try:
            self._ipc_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._ipc_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._ipc_sock.bind(('127.0.0.1', _IPC_PORT))
            self._ipc_sock.listen(1)
            threading.Thread(target=self._ipc_listener, daemon=True).start()
        except Exception:
            self._ipc_sock = None
        self.user_config = load_user_config()
        _apply_active_server(self.user_config)
        if self.user_config.get('autologin') and self.user_config.get('username') and self.user_config.get('password'):
            print("Attempting auto-login...")
            success, sock, sf, reason = self.perform_login(self.user_config['username'], self.user_config['password'])
            if success: self.start_main_session(self.user_config['username'], sock, sf); return True
            else: wx.MessageBox(f"Auto-login failed: {reason}", "Login Failed", wx.ICON_ERROR); self.user_config['autologin'] = False; save_user_config(self.user_config)
        return self.show_login_dialog()
    
    def _ipc_listener(self):
        while True:
            try:
                conn, _ = self._ipc_sock.accept()
                data = conn.recv(1024)
                conn.close()
                if data == b'restore':
                    wx.CallAfter(self._restore_window)
            except Exception:
                break

    def _restore_window(self):
        if hasattr(self, 'frame') and self.frame:
            if not self.frame.IsShown():
                self.frame.restore_from_tray()
            if sys.platform == 'win32':
                hwnd = self.frame.GetHandle()
                ctypes.windll.user32.ShowWindow(hwnd, 9)  # SW_RESTORE
                ctypes.windll.user32.SetForegroundWindow(hwnd)
            else:
                self.frame.Raise()
                self.frame.SetFocus()

    def show_login_dialog(self):
        while True:
            dlg = LoginDialog(None, self.user_config)
            result = dlg.ShowModal()
            if result == wx.ID_OK:
                success, sock, sf, _ = self.perform_login(dlg.username, dlg.password)
                if success:
                    if dlg.remember_checked:
                        self.user_config['username'] = dlg.username
                        self.user_config['password'] = dlg.password
                        self.user_config['remember'] = True
                        self.user_config['autologin'] = dlg.autologin_checked
                    else:
                        # Clear sensitive data but keep generic settings
                        self.user_config.update({'username': '', 'password': '', 'remember': False, 'autologin': False})

                    save_user_config(self.user_config)
                    self.start_main_session(dlg.username, sock, sf)
                    return True
            elif result == wx.ID_ABORT:
                success, sock, sf, _ = self.perform_login(dlg.new_username, dlg.new_password)
                if success:
                    self.user_config = {'username': dlg.new_username, 'password': dlg.new_password, 'remember': True, 'autologin': True, 'soundpack': 'default', 'chat_logging': {}}
                    save_user_config(self.user_config); self.start_main_session(dlg.new_username, sock, sf); return True
            else: return False
    
    def perform_login(self, username, password, silent=False, connect_timeout=None):
        try:
            ssock = create_secure_socket(timeout=connect_timeout)
            ssock.settimeout(None)  # switch to blocking after connect
            ssock.sendall(json.dumps({"action":"login","user":username,"pass":password}).encode()+b"\n")
            sf = ssock.makefile()
            resp = json.loads(sf.readline() or "{}")
            if resp.get("status") == "ok": return True, ssock, sf, "Success"
            else:
                reason = resp.get("reason", "Unknown error")
                if not silent: wx.MessageBox("Login failed: " + reason, "Login Failed", wx.ICON_ERROR)
                ssock.close(); return False, None, None, reason
        except Exception as e:
            if not silent: wx.MessageBox(f"A connection error occurred: {e}", "Connection Error", wx.ICON_ERROR)
            return False, None, None, str(e)
    
    def start_main_session(self, username, sock, sf):
        self.username = username; self.sock = sock; self.sockfile = sf; self.pending_file_paths = {}
        self.intentional_disconnect = False
        self.frame = MainFrame(self.username, self.sock); self.frame.Show()
        if self.frame.current_status != "online":
            try: self.sock.sendall((json.dumps({"action": "set_status", "status_text": self.frame.current_status}) + "\n").encode())
            except Exception: pass
        self.play_sound("login.wav"); threading.Thread(target=self.listen_loop, daemon=True).start()
        self.frame.on_check_updates(silent=True)


    def play_sound(self, sound_file):
        pack = self.user_config.get('soundpack', 'default')
        path = os.path.join('sounds', pack, sound_file)
        if os.path.exists(path): wx.adv.Sound.PlaySound(path, wx.adv.SOUND_ASYNC)
        else:
            default_path = os.path.join('sounds', 'default', sound_file)
            if os.path.exists(default_path): wx.adv.Sound.PlaySound(default_path, wx.adv.SOUND_ASYNC)
    
    def listen_loop(self):
        sock = self.sock
        handled = False
        try:
            for line in self.sockfile:
                msg = json.loads(line); act = msg.get("action")
                if act == "contact_list": wx.CallAfter(self.frame.load_contacts, msg["contacts"])
                elif act == "contact_status": wx.CallAfter(self.frame.update_contact_status, msg["user"], msg["online"], msg.get("status_text"))
                elif act == "msg": wx.CallAfter(self.frame.receive_message, msg)
                elif act == "msg_failed": wx.CallAfter(self.frame.on_message_failed, msg["to"], msg["reason"])
                elif act == "add_contact_failed": wx.CallAfter(self.frame.on_add_contact_failed, msg["reason"])
                elif act == "add_contact_success": wx.CallAfter(self.frame.on_add_contact_success, msg["contact"])
                elif act == "admin_response": wx.CallAfter(self.frame.on_admin_response, msg["response"])
                elif act == "server_info_response": wx.CallAfter(self.frame.on_server_info_response, msg)
                elif act == "user_directory_response": wx.CallAfter(self.frame.on_user_directory_response, msg)
                elif act == "admin_status_change": wx.CallAfter(self.frame.on_admin_status_change, msg["user"], msg["is_admin"])
                elif act == "server_alert": wx.CallAfter(self.frame.on_server_alert, msg["message"])
                elif act == "file_offer": wx.CallAfter(self.on_file_offer, msg)
                elif act == "file_offer_failed": wx.CallAfter(self.on_file_offer_failed, msg)
                elif act == "file_accepted": wx.CallAfter(self.on_file_accepted, msg)
                elif act == "file_declined": wx.CallAfter(self.on_file_declined, msg)
                elif act == "file_data": wx.CallAfter(self.on_file_data, msg)
                elif act == "offline_messages": wx.CallAfter(self.frame.on_offline_messages, msg["messages"])
                elif act == "change_password_result": wx.CallAfter(self.frame.on_change_password_result, msg)
                elif act == "banned_kick": wx.CallAfter(self.on_banned); handled = True; break
        except (IOError, json.JSONDecodeError, ValueError):
            print("Disconnected from server.")
            if self.sock is sock and not self.intentional_disconnect: wx.CallAfter(self.on_server_disconnect); handled = True
            else: handled = True
        if not handled and self.sock is sock and not self.intentional_disconnect:
            print("Server closed connection.")
            wx.CallAfter(self.on_server_disconnect)
    
    def on_banned(self):
        self._return_to_login("You have been banned.", "Banned")

    def on_server_disconnect(self):
        if self.intentional_disconnect: return
        self.intentional_disconnect = True
        try: self.sock.close()
        except: pass
        username = getattr(self, 'username', '')
        password = self.user_config.get('password', '')
        if not username or not password:
            self.intentional_disconnect = False
            self._return_to_login("Connection to the server was lost.", "Connection Lost")
            return
        dlg = ReconnectDialog()
        threading.Thread(target=self._reconnect_loop, args=(dlg, username, password,
            SERVER_CONFIG['max_retries'], SERVER_CONFIG['retry_timeout']), daemon=True).start()
        result = dlg.ShowModal(); dlg.Destroy()
        if result != wx.ID_OK:
            self.intentional_disconnect = False
            self._return_to_login("Could not reconnect to the server.", "Connection Lost")

    def _reconnect_loop(self, dlg, username, password, max_retries=5, wait_secs=15):
        for attempt in range(1, max_retries + 1):
            if dlg.cancelled: return
            wx.CallAfter(dlg.set_status, f"Reconnecting... (attempt {attempt} of {max_retries})")
            success, sock, sf, _ = self.perform_login(username, password, silent=True, connect_timeout=10)
            if success:
                wx.CallAfter(self._finish_reconnect, dlg, sock, sf); return
            if dlg.cancelled: return
            for remaining in range(wait_secs, 0, -1):
                if dlg.cancelled: return
                wx.CallAfter(dlg.set_status, f"Attempt {attempt} of {max_retries} failed. Retrying in {remaining}s...")
                time.sleep(1)
        wx.CallAfter(dlg.EndModal, wx.ID_CANCEL)

    def _finish_reconnect(self, dlg, sock, sf):
        self.sock = sock; self.sockfile = sf; self.pending_file_paths = {}
        self.intentional_disconnect = False
        self.frame.sock = sock
        for child in self.frame.GetChildren():
            if isinstance(child, (ChatDialog, AdminDialog)): child.sock = sock
        threading.Thread(target=self.listen_loop, daemon=True).start()
        if self.frame.current_status != "online":
            try: sock.sendall((json.dumps({"action": "set_status", "status_text": self.frame.current_status}) + "\n").encode())
            except: pass
        self.play_sound("login.wav")
        dlg.EndModal(wx.ID_OK)

    def _return_to_login(self, message, title):
        if self.intentional_disconnect: return
        self.intentional_disconnect = True
        try: self.sock.close()
        except: pass
        self.SetExitOnFrameDelete(False)
        old_frame = None
        if hasattr(self, 'frame') and self.frame:
            old_frame = self.frame; old_frame.Hide()
            if old_frame.task_bar_icon: old_frame.task_bar_icon.Destroy(); old_frame.task_bar_icon = None
        wx.MessageBox(message, title, wx.ICON_ERROR)
        self.intentional_disconnect = False
        result = self.show_login_dialog()
        if old_frame: old_frame.is_exiting = True; old_frame.Destroy()
        if not result: self.ExitMainLoop()

    def on_file_offer(self, msg):
        sender = msg["from"]; files = msg["files"]; transfer_id = msg["transfer_id"]
        self.play_sound("file_receive.wav")
        parent = self.frame.get_chat(sender) or self.frame
        if len(files) == 1:
            f = files[0]; size = f.get("size", 0)
            prompt = f"{sender} wants to send you a file:\n\n{f['filename']} ({format_size(size)})\n\nDo you want to accept?"
        else:
            total_size = sum(f.get("size", 0) for f in files)
            file_list = "\n".join(f"  {f['filename']} ({format_size(f.get('size', 0))})" for f in files)
            prompt = f"{sender} wants to send you {len(files)} files ({format_size(total_size)} total):\n\n{file_list}\n\nDo you want to accept?"
        result = wx.MessageBox(prompt, "File Transfer Request", wx.YES_NO | wx.ICON_QUESTION, parent)
        if result == wx.YES:
            self.sock.sendall((json.dumps({"action": "file_accept", "transfer_id": transfer_id}) + "\n").encode())
            chat = self.frame.get_chat(sender)
            if chat:
                names = ", ".join(f["filename"] for f in files)
                chat.append(f"Accepting {len(files)} file(s): {names}...", "System", time.time())
        else:
            self.sock.sendall((json.dumps({"action": "file_decline", "transfer_id": transfer_id}) + "\n").encode())
            chat = self.frame.get_chat(sender)
            if chat: chat.append(f"Declined {len(files)} file(s) from {sender}", "System", time.time())

    def on_file_offer_failed(self, msg):
        self.play_sound("file_error.wav")
        to = msg.get("to", ""); reason = msg.get("reason", "Unknown error")
        chat = self.frame.get_chat(to)
        if chat: chat.append_error(f"File transfer failed: {reason}")
        else: wx.MessageBox(f"File transfer failed: {reason}", "File Transfer Error", wx.ICON_ERROR)

    def on_file_accepted(self, msg):
        transfer_id = msg["transfer_id"]; to = msg["to"]; files_info = msg["files"]
        file_token = msg.get("file_token", "")
        client_tid = msg.get("client_transfer_id") or transfer_id
        file_paths = self.pending_file_paths.pop(client_tid, None)
        if not file_paths:
            chat = self.frame.get_chat(to)
            if chat: chat.append_error("File transfer error: files no longer available.")
            return
        def _send():
            try:
                files_data = []
                for fp in file_paths:
                    with open(fp, 'rb') as f: files_data.append({"filename": os.path.basename(fp), "data": base64.b64encode(f.read()).decode('ascii')})
                # Send file data on a dedicated connection so the main
                # connection stays free for messages, directory, etc.
                xfer_sock = create_secure_socket()
                try:
                    xfer_sock.sendall((json.dumps({"action": "file_data", "transfer_id": transfer_id, "file_token": file_token, "to": to, "files": files_data}) + "\n").encode())
                    resp = json.loads(xfer_sock.makefile().readline() or "{}")
                finally:
                    xfer_sock.close()
                if resp.get("status") != "ok":
                    raise Exception(resp.get("reason", "Server rejected file data"))
                names = [os.path.basename(fp) for fp in file_paths]
                wx.CallAfter(self._on_files_sent, to, names)
            except Exception as e:
                wx.CallAfter(self._on_file_send_error, to, e)
        threading.Thread(target=_send, daemon=True).start()

    def _on_files_sent(self, to, filenames):
        self.play_sound("file_send.wav")
        chat = self.frame.get_chat(to)
        if chat:
            names = ", ".join(filenames)
            chat.append(f"{len(filenames)} file(s) sent: {names}", "System", time.time())

    def _on_file_send_error(self, to, error):
        self.play_sound("file_error.wav")
        chat = self.frame.get_chat(to)
        if chat: chat.append_error(f"Failed to send file(s): {error}")

    def on_file_declined(self, msg):
        transfer_id = msg["transfer_id"]; to = msg["to"]; files = msg["files"]
        client_tid = msg.get("client_transfer_id") or transfer_id
        self.pending_file_paths.pop(client_tid, None)
        self.play_sound("file_error.wav")
        names = ", ".join(f["filename"] for f in files)
        chat = self.frame.get_chat(to)
        if chat: chat.append(f"{to} declined your file(s): {names}", "System", time.time())
        else: wx.MessageBox(f"{to} declined your file(s): {names}", "File Declined", wx.ICON_INFORMATION)

    def on_file_data(self, msg):
        sender = msg["from"]; files = msg["files"]
        docs_path = os.path.join(os.path.expanduser('~'), 'Documents')
        save_dir = os.path.join(docs_path, 'ThriveMessenger', 'files')
        os.makedirs(save_dir, exist_ok=True)
        saved = []
        for finfo in files:
            filename = finfo["filename"]; data = finfo["data"]
            try:
                save_path = os.path.join(save_dir, filename)
                if os.path.exists(save_path):
                    name, ext = os.path.splitext(filename)
                    counter = 1
                    while os.path.exists(save_path):
                        save_path = os.path.join(save_dir, f"{name} ({counter}){ext}")
                        counter += 1
                with open(save_path, 'wb') as f: f.write(base64.b64decode(data))
                saved.append(os.path.basename(save_path))
            except Exception as e:
                self.play_sound("file_error.wav")
                chat = self.frame.get_chat(sender)
                if chat: chat.append_error(f"Failed to save file '{filename}': {e}")
        if saved:
            self.play_sound("file_receive.wav")
            chat = self.frame.get_chat(sender)
            names = ", ".join(saved)
            if chat: chat.append(f"{len(saved)} file(s) received and saved: {names}", "System", time.time())
            else:
                if wx.GetApp().user_config.get('announce_files', False):
                    speak(f"{sender} sent you {len(saved)} file{'s' if len(saved) != 1 else ''}.")
                else:
                    show_notification("Files Received", f"{sender} sent you {len(saved)} file(s)")

    def send_file_to(self, contact, parent=None):
        if parent is None: parent = self.frame.get_chat(contact) or self.frame
        with wx.FileDialog(parent, "Choose file(s) to send", wildcard="All files (*.*)|*.*", style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST | wx.FD_MULTIPLE) as dlg:
            if dlg.ShowModal() == wx.ID_CANCEL: return
            file_paths = dlg.GetPaths()
        files = []; valid_paths = []
        for file_path in file_paths:
            filename = os.path.basename(file_path)
            try: size = os.path.getsize(file_path)
            except OSError as e:
                wx.MessageBox(f"Cannot read file '{filename}': {e}", "File Error", wx.ICON_ERROR); continue
            files.append({"filename": filename, "size": size})
            valid_paths.append(file_path)
        if not files: return
        transfer_id = str(uuid.uuid4())
        self.pending_file_paths[transfer_id] = valid_paths
        self.sock.sendall((json.dumps({"action": "file_offer", "to": contact, "files": files, "transfer_id": transfer_id}) + "\n").encode())
        chat = self.frame.get_chat(contact)
        if chat:
            names = ", ".join(f["filename"] for f in files)
            chat.append(f"Sending file offer ({len(files)} file(s)): {names}...", "System", time.time())

class VerificationDialog(wx.Dialog):
    def __init__(self, parent, username):
        super().__init__(parent, title="Account Verification", size=(300, 180)); self.username = username
        panel = wx.Panel(self); s = wx.BoxSizer(wx.VERTICAL)
        dark_mode_on = is_windows_dark_mode()
        if dark_mode_on:
            dark_color = wx.Colour(40, 40, 40); light_text_color = wx.WHITE
            WxMswDarkMode().enable(self); self.SetBackgroundColour(dark_color); panel.SetBackgroundColour(dark_color)
        
        lbl = wx.StaticText(panel, label=f"Enter the code sent to your email:"); s.Add(lbl, 0, wx.ALL, 10)
        self.code_txt = wx.TextCtrl(panel); s.Add(self.code_txt, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 10)
        btn_sizer = wx.StdDialogButtonSizer(); ok_btn = wx.Button(panel, wx.ID_OK, label="&Verify"); ok_btn.SetDefault(); cancel_btn = wx.Button(panel, wx.ID_CANCEL)
        
        if dark_mode_on:
            lbl.SetForegroundColour(light_text_color); self.code_txt.SetBackgroundColour(dark_color); self.code_txt.SetForegroundColour(light_text_color)
            ok_btn.SetBackgroundColour(dark_color); ok_btn.SetForegroundColour(light_text_color); cancel_btn.SetBackgroundColour(dark_color); cancel_btn.SetForegroundColour(light_text_color)
            
        btn_sizer.AddButton(ok_btn); btn_sizer.AddButton(cancel_btn); btn_sizer.Realize(); s.Add(btn_sizer, 0, wx.ALIGN_CENTER | wx.ALL, 10); panel.SetSizer(s)

class ForgotPasswordDialog(wx.Dialog):
    def __init__(self, parent):
        super().__init__(parent, title="Reset Password", size=(350, 250)); panel = wx.Panel(self); self.sizer = wx.BoxSizer(wx.VERTICAL)
        self.panel = panel
        self.dark_mode_on = is_windows_dark_mode()
        if self.dark_mode_on:
            self.dark_color = wx.Colour(40, 40, 40); self.light_text_color = wx.WHITE
            WxMswDarkMode().enable(self); self.SetBackgroundColour(self.dark_color); panel.SetBackgroundColour(self.dark_color)
        
        self.step1_sizer = wx.BoxSizer(wx.VERTICAL)
        lbl1 = wx.StaticText(panel, label="Enter your registered Email or Username:"); self.email_txt = wx.TextCtrl(panel)
        btn_req = wx.Button(panel, label="Request Reset Code"); btn_req.Bind(wx.EVT_BUTTON, self.on_request)
        self.step1_sizer.Add(lbl1, 0, wx.ALL, 5); self.step1_sizer.Add(self.email_txt, 0, wx.EXPAND | wx.ALL, 5); self.step1_sizer.Add(btn_req, 0, wx.ALIGN_CENTER | wx.ALL, 5)
        
        self.step2_sizer = wx.BoxSizer(wx.VERTICAL)
        lbl2 = wx.StaticText(panel, label="Enter Reset Code:"); self.code_txt = wx.TextCtrl(panel)
        lbl3 = wx.StaticText(panel, label="New Password:"); self.pass_txt = wx.TextCtrl(panel, style=wx.TE_PASSWORD)
        btn_reset = wx.Button(panel, label="Change Password"); btn_reset.Bind(wx.EVT_BUTTON, self.on_reset)
        self.step2_sizer.Add(lbl2, 0, wx.ALL, 5); self.step2_sizer.Add(self.code_txt, 0, wx.EXPAND | wx.ALL, 5)
        self.step2_sizer.Add(lbl3, 0, wx.ALL, 5); self.step2_sizer.Add(self.pass_txt, 0, wx.EXPAND | wx.ALL, 5)
        self.step2_sizer.Add(btn_reset, 0, wx.ALIGN_CENTER | wx.ALL, 5)
        
        self.sizer.Add(self.step1_sizer, 1, wx.EXPAND | wx.ALL, 5); self.sizer.Add(self.step2_sizer, 1, wx.EXPAND | wx.ALL, 5)
        self.sizer.Hide(self.step2_sizer)
        
        if self.dark_mode_on:
            for c in [lbl1, lbl2, lbl3, self.email_txt, self.code_txt, self.pass_txt, btn_req, btn_reset]:
                c.SetForegroundColour(self.light_text_color); 
                if isinstance(c, (wx.TextCtrl, wx.Button)): c.SetBackgroundColour(self.dark_color)

        panel.SetSizer(self.sizer)

    def on_request(self, e):
        ident = self.email_txt.GetValue().strip()
        if not ident: wx.MessageBox("Please enter email or username.", "Error"); return
        try:
            sock = create_secure_socket()
            sock.sendall(json.dumps({"action":"request_reset", "identifier":ident}).encode()+b"\n")
            resp = json.loads(sock.makefile().readline() or "{}"); sock.close()
            if resp.get("status") == "ok":
                wx.MessageBox("If that account exists, a code has been sent.", "Code Sent", wx.ICON_INFORMATION)
                self.username_cache = resp.get("user", ident) 
                self.sizer.Hide(self.step1_sizer); self.sizer.Show(self.step2_sizer); self.panel.Layout()
            else: wx.MessageBox(resp.get("reason", "Error"), "Failed", wx.ICON_ERROR)
        except Exception as ex: wx.MessageBox(str(ex), "Connection Error")

    def on_reset(self, e):
        code = self.code_txt.GetValue().strip(); new_p = self.pass_txt.GetValue()
        if not code or not new_p: return
        try:
            sock = create_secure_socket()
            sock.sendall(json.dumps({"action":"reset_password", "user": self.username_cache, "code": code, "new_pass": new_p}).encode()+b"\n")
            resp = json.loads(sock.makefile().readline() or "{}"); sock.close()
            if resp.get("status") == "ok": wx.MessageBox("Password changed successfully!", "Success"); self.EndModal(wx.ID_OK)
            else: wx.MessageBox(resp.get("reason", "Error"), "Failed", wx.ICON_ERROR)
        except Exception as ex: wx.MessageBox(str(ex), "Connection Error")

class CreateAccountDialog(wx.Dialog):
    def __init__(self, parent):
        super().__init__(parent, title="Create New Account", size=(300, 330)); panel = wx.Panel(self); s = wx.BoxSizer(wx.VERTICAL)

        dark_mode_on = is_windows_dark_mode()
        if dark_mode_on:
            dark_color = wx.Colour(40, 40, 40); light_text_color = wx.WHITE
            WxMswDarkMode().enable(self); self.SetBackgroundColour(dark_color); panel.SetBackgroundColour(dark_color)

        user_box = wx.StaticBoxSizer(wx.VERTICAL, panel, "&Username"); self.u_text = wx.TextCtrl(user_box.GetStaticBox()); user_box.Add(self.u_text, 0, wx.EXPAND | wx.ALL, 5)
        email_box = wx.StaticBoxSizer(wx.VERTICAL, panel, "&Email (Optional but recommended)"); self.e_text = wx.TextCtrl(email_box.GetStaticBox()); email_box.Add(self.e_text, 0, wx.EXPAND | wx.ALL, 5)
        pass_box = wx.StaticBoxSizer(wx.VERTICAL, panel, "&Password"); self.p1_text = wx.TextCtrl(pass_box.GetStaticBox(), style=wx.TE_PASSWORD); pass_box.Add(self.p1_text, 0, wx.EXPAND | wx.ALL, 5)
        confirm_box = wx.StaticBoxSizer(wx.VERTICAL, panel, "&Confirm Password"); self.p2_text = wx.TextCtrl(confirm_box.GetStaticBox(), style=wx.TE_PASSWORD); confirm_box.Add(self.p2_text, 0, wx.EXPAND | wx.ALL, 5)
        self.autologin_cb = wx.CheckBox(panel, label="&Log in automatically upon creation"); self.autologin_cb.SetValue(True)
        btn_sizer = wx.StdDialogButtonSizer(); ok_btn = wx.Button(panel, wx.ID_OK, label="&Create"); ok_btn.SetDefault(); ok_btn.Bind(wx.EVT_BUTTON, self.on_create)
        cancel_btn = wx.Button(panel, wx.ID_CANCEL)

        if dark_mode_on:
            for box in [user_box, email_box, pass_box, confirm_box]:
                box.GetStaticBox().SetForegroundColour(light_text_color)
                box.GetStaticBox().SetBackgroundColour(dark_color)
            for ctrl in [self.u_text, self.e_text, self.p1_text, self.p2_text]: ctrl.SetBackgroundColour(dark_color); ctrl.SetForegroundColour(light_text_color)
            ok_btn.SetBackgroundColour(dark_color); ok_btn.SetForegroundColour(light_text_color)
            cancel_btn.SetBackgroundColour(dark_color); cancel_btn.SetForegroundColour(light_text_color)
            self.autologin_cb.SetForegroundColour(light_text_color)

        s.Add(user_box, 0, wx.EXPAND | wx.ALL, 5); s.Add(email_box, 0, wx.EXPAND | wx.ALL, 5); s.Add(pass_box, 0, wx.EXPAND | wx.ALL, 5); s.Add(confirm_box, 0, wx.EXPAND | wx.ALL, 5)
        s.Add(self.autologin_cb, 0, wx.ALL, 10)
        btn_sizer.AddButton(ok_btn); btn_sizer.AddButton(cancel_btn); btn_sizer.Realize(); s.Add(btn_sizer, 0, wx.ALIGN_CENTER | wx.ALL, 5); panel.SetSizer(s)
    
    def on_create(self, event):
        u = self.u_text.GetValue().strip(); p1 = self.p1_text.GetValue(); p2 = self.p2_text.GetValue()
        if not u or not p1: wx.MessageBox("Username and password cannot be blank.", "Validation Error", wx.ICON_ERROR); return
        if p1 != p2: wx.MessageBox("Passwords do not match.", "Validation Error", wx.ICON_ERROR); return
        self.EndModal(wx.ID_OK)

class AddServerDialog(wx.Dialog):
    def __init__(self, parent):
        super().__init__(parent, title="Add Server", size=(300, 230))
        panel = wx.Panel(self); s = wx.BoxSizer(wx.VERTICAL)
        dark_mode_on = is_windows_dark_mode()
        if dark_mode_on:
            dark_color = wx.Colour(40, 40, 40); light_text_color = wx.WHITE
            WxMswDarkMode().enable(self); self.SetBackgroundColour(dark_color); panel.SetBackgroundColour(dark_color)
        name_box = wx.StaticBoxSizer(wx.VERTICAL, panel, "&Name")
        self.name_txt = wx.TextCtrl(name_box.GetStaticBox()); name_box.Add(self.name_txt, 0, wx.EXPAND | wx.ALL, 5)
        host_box = wx.StaticBoxSizer(wx.VERTICAL, panel, "&Host")
        self.host_txt = wx.TextCtrl(host_box.GetStaticBox()); host_box.Add(self.host_txt, 0, wx.EXPAND | wx.ALL, 5)
        port_box = wx.StaticBoxSizer(wx.VERTICAL, panel, "P&ort")
        self.port_txt = wx.TextCtrl(port_box.GetStaticBox()); self.port_txt.SetValue("2005"); port_box.Add(self.port_txt, 0, wx.EXPAND | wx.ALL, 5)
        ok_btn = wx.Button(panel, wx.ID_OK, "&OK"); cancel_btn = wx.Button(panel, wx.ID_CANCEL, "&Cancel")
        if dark_mode_on:
            for box in [name_box, host_box, port_box]:
                box.GetStaticBox().SetForegroundColour(light_text_color); box.GetStaticBox().SetBackgroundColour(dark_color)
            for ctrl in [self.name_txt, self.host_txt, self.port_txt]: ctrl.SetBackgroundColour(dark_color); ctrl.SetForegroundColour(light_text_color)
            for btn in [ok_btn, cancel_btn]: btn.SetBackgroundColour(dark_color); btn.SetForegroundColour(light_text_color)
        s.Add(name_box, 0, wx.EXPAND | wx.ALL, 5); s.Add(host_box, 0, wx.EXPAND | wx.ALL, 5); s.Add(port_box, 0, wx.EXPAND | wx.ALL, 5)
        btn_sizer = wx.BoxSizer(wx.HORIZONTAL); btn_sizer.Add(ok_btn, 0, wx.ALL, 5); btn_sizer.Add(cancel_btn, 0, wx.ALL, 5)
        s.Add(btn_sizer, 0, wx.ALIGN_CENTER | wx.ALL, 5); panel.SetSizer(s)
        self.SetEscapeId(wx.ID_CANCEL)

class ServerManagerDialog(wx.Dialog):
    def __init__(self, parent, user_config):
        super().__init__(parent, title="Server Manager", size=(450, 300))
        self.user_config = user_config; self.servers = [s.copy() for s in _get_servers(user_config)]
        panel = wx.Panel(self); s = wx.BoxSizer(wx.VERTICAL)
        dark_mode_on = is_windows_dark_mode()
        if dark_mode_on:
            dark_color = wx.Colour(40, 40, 40); light_text_color = wx.WHITE
            WxMswDarkMode().enable(self); self.SetBackgroundColour(dark_color); panel.SetBackgroundColour(dark_color)
        self.list = wx.ListCtrl(panel, style=wx.LC_REPORT | wx.LC_SINGLE_SEL)
        self.list.InsertColumn(0, "Name", width=150); self.list.InsertColumn(1, "Host", width=150)
        self.list.InsertColumn(2, "Port", width=60); self.list.InsertColumn(3, "Primary", width=60)
        self._populate_list()
        self.add_btn = wx.Button(panel, label="&Add..."); self.del_btn = wx.Button(panel, label="&Delete")
        self.primary_btn = wx.Button(panel, label="Set as &Primary")
        close_btn = wx.Button(panel, wx.ID_CANCEL, "&Close")
        self.add_btn.Bind(wx.EVT_BUTTON, self.on_add); self.del_btn.Bind(wx.EVT_BUTTON, self.on_delete)
        self.primary_btn.Bind(wx.EVT_BUTTON, self.on_set_primary)
        close_btn.Bind(wx.EVT_BUTTON, lambda e: self.Close()); self.Bind(wx.EVT_CLOSE, self._on_close)
        self.list.Bind(wx.EVT_LIST_ITEM_SELECTED, self._on_sel); self.list.Bind(wx.EVT_LIST_ITEM_DESELECTED, self._on_sel)
        if dark_mode_on:
            self.list.SetBackgroundColour(dark_color); self.list.SetForegroundColour(light_text_color)
            for btn in [self.add_btn, self.del_btn, self.primary_btn, close_btn]: btn.SetBackgroundColour(dark_color); btn.SetForegroundColour(light_text_color)
        btn_sizer = wx.BoxSizer(wx.HORIZONTAL)
        btn_sizer.Add(self.add_btn, 0, wx.ALL, 5); btn_sizer.Add(self.del_btn, 0, wx.ALL, 5); btn_sizer.Add(self.primary_btn, 0, wx.ALL, 5)
        s.Add(self.list, 1, wx.EXPAND | wx.ALL, 5); s.Add(btn_sizer, 0, wx.ALIGN_CENTER)
        s.Add(close_btn, 0, wx.ALIGN_CENTER | wx.ALL, 5); panel.SetSizer(s); self._update_buttons()

    def _populate_list(self):
        self.list.DeleteAllItems()
        for i, srv in enumerate(self.servers):
            self.list.InsertItem(i, srv['name']); self.list.SetItem(i, 1, srv['host'])
            self.list.SetItem(i, 2, str(srv['port'])); self.list.SetItem(i, 3, "Yes" if srv.get('primary') else "")

    def _update_buttons(self):
        sel = self.list.GetFirstSelected()
        if sel < 0: self.del_btn.Enable(False); self.primary_btn.Enable(False); return
        srv = self.servers[sel]
        self.del_btn.Enable(len(self.servers) > 1 and not srv.get('primary'))
        self.primary_btn.Enable(not srv.get('primary'))

    def _on_sel(self, event): self._update_buttons()

    def on_add(self, event):
        with AddServerDialog(self) as dlg:
            if dlg.ShowModal() == wx.ID_OK:
                name = dlg.name_txt.GetValue().strip(); host = dlg.host_txt.GetValue().strip()
                try: port = int(dlg.port_txt.GetValue().strip())
                except ValueError: wx.MessageBox("Port must be a number.", "Error", wx.ICON_ERROR); return
                if not name or not host: wx.MessageBox("Name and host are required.", "Error", wx.ICON_ERROR); return
                if port < 1 or port > 65535: wx.MessageBox("Port must be between 1 and 65535.", "Error", wx.ICON_ERROR); return
                self.servers.append({"name": name, "host": host, "port": port, "primary": len(self.servers) == 0})
                self._populate_list(); self._update_buttons()

    def on_delete(self, event):
        sel = self.list.GetFirstSelected()
        if sel < 0: return
        srv = self.servers[sel]
        if srv.get('primary'): wx.MessageBox("Cannot delete the primary server.", "Error", wx.ICON_ERROR); return
        if len(self.servers) <= 1: wx.MessageBox("Cannot delete the only server.", "Error", wx.ICON_ERROR); return
        self.servers.pop(sel); self._populate_list(); self._update_buttons()

    def on_set_primary(self, event):
        sel = self.list.GetFirstSelected()
        if sel < 0: return
        for srv in self.servers: srv['primary'] = False
        self.servers[sel]['primary'] = True
        self._populate_list(); self.list.Select(sel); self._update_buttons()

    def _on_close(self, event):
        self.user_config['servers'] = self.servers; _apply_active_server(self.user_config)
        save_user_config(self.user_config); self.EndModal(wx.ID_CLOSE)

class LoginDialog(wx.Dialog):
    def __init__(self, parent, user_config):
        super().__init__(parent, title="Login", size=(300, 350)); self.user_config = user_config
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
        forgot_btn = wx.Button(panel, label="&Forgot Password?"); forgot_btn.Bind(wx.EVT_BUTTON, self.on_forgot)
        servers_btn = wx.Button(panel, label="&Servers..."); servers_btn.Bind(wx.EVT_BUTTON, self.on_servers)

        if dark_mode_on:
            for box in [user_box, pass_box]:
                box.GetStaticBox().SetForegroundColour(light_text_color)
                box.GetStaticBox().SetBackgroundColour(dark_color)
            for ctrl in [self.u, self.p]: ctrl.SetBackgroundColour(dark_color); ctrl.SetForegroundColour(light_text_color)
            for btn in [login_btn, create_btn, forgot_btn, servers_btn]: btn.SetBackgroundColour(dark_color); btn.SetForegroundColour(light_text_color)
            self.remember_cb.SetForegroundColour(light_text_color); self.autologin_cb.SetForegroundColour(light_text_color)

        s.Add(user_box, 0, wx.EXPAND | wx.ALL, 5); s.Add(pass_box, 0, wx.EXPAND | wx.ALL, 5)
        s.Add(self.remember_cb, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 10); s.Add(self.autologin_cb, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)
        btn_sizer = wx.BoxSizer(wx.HORIZONTAL);
        btn_sizer.Add(login_btn, 1, wx.EXPAND | wx.ALL, 2); btn_sizer.Add(create_btn, 1, wx.EXPAND | wx.ALL, 2)
        s.Add(btn_sizer, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 5)
        btn_sizer2 = wx.BoxSizer(wx.HORIZONTAL)
        btn_sizer2.Add(forgot_btn, 1, wx.EXPAND | wx.ALL, 2); btn_sizer2.Add(servers_btn, 1, wx.EXPAND | wx.ALL, 2)
        s.Add(btn_sizer2, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 5)
        self.p.Bind(wx.EVT_TEXT_ENTER, self.on_login); panel.SetSizer(s); self.on_check_remember(None)
    
    def on_forgot(self, event):
        with ForgotPasswordDialog(self) as dlg: dlg.ShowModal()

    def on_servers(self, event):
        with ServerManagerDialog(self, self.user_config) as dlg: dlg.ShowModal()

    def on_create_account(self, event):
        with CreateAccountDialog(self) as dlg:
            if dlg.ShowModal() == wx.ID_OK:
                u, p, em, auto = dlg.u_text.GetValue(), dlg.p1_text.GetValue(), dlg.e_text.GetValue(), dlg.autologin_cb.IsChecked()
                try:
                    ssock = create_secure_socket()
                    ssock.sendall(json.dumps({"action":"create_account","user":u,"pass":p,"email":em}).encode()+b"\n")
                    resp = json.loads(ssock.makefile().readline() or "{}")
                    ssock.close()
                    
                    if resp.get("action") == "verify_pending":
                        wx.MessageBox("A verification code has been sent to your email.", "Verification Required", wx.ICON_INFORMATION)
                        while True:
                            with VerificationDialog(self, u) as vdlg:
                                if vdlg.ShowModal() != wx.ID_OK: break
                                code = vdlg.code_txt.GetValue().strip()
                            sock2 = create_secure_socket()
                            sock2.sendall(json.dumps({"action":"verify_account", "user":u, "code":code}).encode()+b"\n")
                            vresp = json.loads(sock2.makefile().readline() or "{}"); sock2.close()
                            if vresp.get("status") == "ok":
                                wx.MessageBox("Account verified!", "Success")
                                if auto: self.new_username = u; self.new_password = p; self.EndModal(wx.ID_ABORT)
                                break
                            wx.MessageBox("Verification failed: " + vresp.get("reason", "Unknown error"), "Error", wx.ICON_ERROR)
                    elif resp.get("action") == "create_account_success":
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

def format_size(size_bytes):
    if size_bytes <= 0: return "No limit"
    if size_bytes < 1024: return f"{size_bytes} bytes"
    elif size_bytes < 1048576: return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1073741824: return f"{size_bytes / 1048576:.1f} MB"
    else: return f"{size_bytes / 1073741824:.1f} GB"

class ServerInfoDialog(wx.Dialog):
    def __init__(self, parent, info):
        super().__init__(parent, title="Server Information", size=(400, 300))
        dark_mode_on = is_windows_dark_mode()
        if dark_mode_on:
            dark_color = wx.Colour(40, 40, 40); light_text_color = wx.WHITE
            WxMswDarkMode().enable(self); self.SetBackgroundColour(dark_color)
        s = wx.BoxSizer(wx.VERTICAL)
        self.lv = wx.ListCtrl(self, style=wx.LC_REPORT)
        self.lv.InsertColumn(0, "Property", width=180); self.lv.InsertColumn(1, "Value", width=200)
        if dark_mode_on:
            self.lv.SetBackgroundColour(dark_color); self.lv.SetForegroundColour(light_text_color)
        for prop, val in info:
            idx = self.lv.GetItemCount(); self.lv.InsertItem(idx, prop); self.lv.SetItem(idx, 1, val)
        btn = wx.Button(self, wx.ID_OK, label="&Close")
        if dark_mode_on:
            btn.SetBackgroundColour(dark_color); btn.SetForegroundColour(light_text_color)
        s.Add(self.lv, 1, wx.EXPAND | wx.ALL, 5); s.Add(btn, 0, wx.ALIGN_CENTER | wx.ALL, 5); self.SetSizer(s)
        self.Bind(wx.EVT_CHAR_HOOK, self.on_key)
    def on_key(self, event):
        if event.GetKeyCode() == wx.WXK_ESCAPE: self.Close()
        else: event.Skip()

class UserDirectoryDialog(wx.Dialog):
    def __init__(self, parent_frame, users, my_username, contact_states):
        super().__init__(parent_frame, title="User Directory", size=(550, 500), style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.parent_frame = parent_frame; self.my_username = my_username; self.contact_states = contact_states
        self._all_users = users; self._selected_user = None
        panel = wx.Panel(self)
        dark_mode_on = is_windows_dark_mode()
        if dark_mode_on:
            dc = wx.Colour(40, 40, 40); lt = wx.WHITE
            WxMswDarkMode().enable(self); self.SetBackgroundColour(dc); panel.SetBackgroundColour(dc)
        s = wx.BoxSizer(wx.VERTICAL)
        search_label = wx.StaticText(panel, label="Searc&h:")
        self.search_box = wx.TextCtrl(panel, style=wx.TE_PROCESS_ENTER)
        self.search_box.Bind(wx.EVT_TEXT, self.on_search)
        if dark_mode_on:
            search_label.SetForegroundColour(lt); self.search_box.SetBackgroundColour(dc); self.search_box.SetForegroundColour(lt)
        search_sizer = wx.BoxSizer(wx.HORIZONTAL)
        search_sizer.Add(search_label, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 5)
        search_sizer.Add(self.search_box, 1, wx.EXPAND)
        s.Add(search_sizer, 0, wx.EXPAND | wx.ALL, 5)
        self.notebook = wx.Notebook(panel)
        self.tabs = {}
        for tab_name in ["Everyone", "Online", "Offline", "Admins"]:
            lv = wx.ListCtrl(self.notebook, style=wx.LC_REPORT | wx.LC_SINGLE_SEL)
            lv.InsertColumn(0, "Username", width=150); lv.InsertColumn(1, "Status", width=150); lv.InsertColumn(2, "Info", width=150)
            lv.Bind(wx.EVT_LIST_ITEM_SELECTED, self.on_selection_changed)
            lv.Bind(wx.EVT_LIST_ITEM_DESELECTED, self.on_selection_changed)
            lv.Bind(wx.EVT_LIST_ITEM_ACTIVATED, self.on_item_activated)
            lv.Bind(wx.EVT_CHAR_HOOK, self.on_list_key)
            if dark_mode_on: lv.SetBackgroundColour(dc); lv.SetForegroundColour(lt)
            self.notebook.AddPage(lv, tab_name); self.tabs[tab_name] = lv
        self.notebook.Bind(wx.EVT_NOTEBOOK_PAGE_CHANGED, self.on_tab_changed)
        if dark_mode_on: self.notebook.SetBackgroundColour(dc); self.notebook.SetForegroundColour(lt)
        s.Add(self.notebook, 1, wx.EXPAND | wx.ALL, 5)
        self.btn_chat = wx.Button(panel, label="&Start Chat"); self.btn_file = wx.Button(panel, label="Send &File")
        self.btn_block = wx.Button(panel, label="&Block"); self.btn_add = wx.Button(panel, label="&Add to Contacts")
        self.btn_close = wx.Button(panel, label="&Close")
        self.btn_chat.Bind(wx.EVT_BUTTON, self.on_start_chat); self.btn_file.Bind(wx.EVT_BUTTON, self.on_send_file)
        self.btn_block.Bind(wx.EVT_BUTTON, self.on_block_toggle); self.btn_add.Bind(wx.EVT_BUTTON, self.on_add_to_contacts)
        self.btn_close.Bind(wx.EVT_BUTTON, lambda e: self.Close())
        if dark_mode_on:
            for btn in [self.btn_chat, self.btn_file, self.btn_block, self.btn_add, self.btn_close]:
                btn.SetBackgroundColour(dc); btn.SetForegroundColour(lt)
        btn_sizer = wx.BoxSizer(wx.HORIZONTAL)
        btn_sizer.Add(self.btn_chat, 1, wx.EXPAND | wx.ALL, 2); btn_sizer.Add(self.btn_file, 1, wx.EXPAND | wx.ALL, 2)
        btn_sizer.Add(self.btn_block, 1, wx.EXPAND | wx.ALL, 2); btn_sizer.Add(self.btn_add, 1, wx.EXPAND | wx.ALL, 2)
        btn_sizer.Add(self.btn_close, 1, wx.EXPAND | wx.ALL, 2)
        s.Add(btn_sizer, 0, wx.EXPAND | wx.ALL, 5)
        panel.SetSizer(s)
        esc_id = wx.NewIdRef()
        self.Bind(wx.EVT_MENU, lambda e: self.Close(), id=esc_id)
        self.SetAcceleratorTable(wx.AcceleratorTable([(wx.ACCEL_NORMAL, wx.WXK_ESCAPE, esc_id)]))
        self.Bind(wx.EVT_CLOSE, self.on_close)
        self._populate_all_tabs(); self.update_button_states()
    def _get_active_list(self):
        page = self.notebook.GetSelection()
        return self.notebook.GetPage(page) if page != wx.NOT_FOUND else None
    def _get_selected_user(self):
        lv = self._get_active_list()
        if not lv: return None
        sel = lv.GetFirstSelected()
        if sel >= 0:
            self._selected_user = lv.GetItemText(sel)
            return self._selected_user
        return None
    def _populate_all_tabs(self):
        query = self.search_box.GetValue().strip().lower()
        for tab_name, lv in self.tabs.items():
            lv.DeleteAllItems()
            for u in self._all_users:
                if query and query not in u["user"].lower(): continue
                if tab_name == "Online" and not u["online"]: continue
                if tab_name == "Offline" and u["online"]: continue
                if tab_name == "Admins" and not u["is_admin"]: continue
                info_parts = []
                if u["user"] == self.my_username: info_parts.append("You")
                if u["is_admin"]: info_parts.append("Admin")
                if u["is_contact"]: info_parts.append("Contact")
                if u["is_blocked"]: info_parts.append("Blocked")
                idx = lv.InsertItem(lv.GetItemCount(), u["user"])
                lv.SetItem(idx, 1, u["status_text"])
                lv.SetItem(idx, 2, ", ".join(info_parts))
                if u["is_blocked"]: lv.SetItemTextColour(idx, wx.Colour(150, 150, 150))
        self.update_button_states()
    def update_button_states(self):
        user = self._get_selected_user()
        if not user or user == self.my_username:
            self.btn_chat.Disable(); self.btn_file.Disable(); self.btn_block.Disable(); self.btn_add.Disable()
            self.btn_block.SetLabel("&Block"); return
        self.btn_chat.Enable(); self.btn_file.Enable()
        is_contact = user in self.contact_states
        self.btn_add.Enable(not is_contact); self.btn_add.SetLabel("&Add to Contacts")
        self.btn_block.Enable(is_contact)
        if is_contact:
            blocked = self.contact_states.get(user, 0) == 1
            self.btn_block.SetLabel("&Unblock" if blocked else "&Block")
        else:
            self.btn_block.SetLabel("&Block")
    def on_search(self, event): self._populate_all_tabs()
    def on_tab_changed(self, event): self._selected_user = None; self.update_button_states(); event.Skip()
    def on_selection_changed(self, event): self.update_button_states(); event.Skip()
    def on_item_activated(self, event):
        self.on_selection_changed(event); self.on_start_chat(None)
    def on_start_chat(self, _):
        user = self._selected_user
        if not user or user == self.my_username: return
        app = wx.GetApp(); logging_config = app.user_config.get('chat_logging', {}); is_logging_enabled = logging_config.get(user, False)
        is_contact = user in self.contact_states
        dlg = self.parent_frame.get_chat(user) or ChatDialog(self.parent_frame, user, self.parent_frame.sock, self.parent_frame.user, is_logging_enabled, is_contact=is_contact)
        dlg.Show(); dlg.input_ctrl.SetFocus()
    def on_send_file(self, _):
        if self._selected_user: wx.GetApp().send_file_to(self._selected_user)
    def on_block_toggle(self, _):
        user = self._selected_user
        if not user or user not in self.contact_states: return
        blocked = self.contact_states.get(user, 0) == 1
        action = "unblock_contact" if blocked else "block_contact"
        self.parent_frame.sock.sendall(json.dumps({"action": action, "to": user}).encode() + b"\n")
        self.contact_states[user] = 0 if blocked else 1
        for entry in self.parent_frame._all_contacts:
            if entry["user"] == user: entry["blocked"] = 0 if blocked else 1; break
        self.parent_frame._apply_search_filter()
        for u in self._all_users:
            if u["user"] == user: u["is_blocked"] = not blocked; break
        self._populate_all_tabs()
    def on_add_to_contacts(self, _):
        user = self._selected_user
        if not user: return
        self.parent_frame.sock.sendall(json.dumps({"action": "add_contact", "to": user}).encode() + b"\n")
        self.btn_add.Disable(); self.btn_add.SetLabel("Adding...")
    def on_list_key(self, event):
        if event.GetKeyCode() == wx.WXK_TAB:
            if event.ShiftDown():
                self.notebook.SetFocus()
            else:
                self.btn_chat.SetFocus()
            return
        event.Skip()
    def on_close(self, event):
        if self.parent_frame: self.parent_frame._directory_dlg = None
        self.Destroy()

class MainFrame(wx.Frame):
    def update_contact_status(self, user, online, status_text=None):
        was_online = False
        for c in self._all_contacts:
            if c["user"] == user:
                was_online = c["status"] != "offline" and not c["status"].startswith("offline")
                is_admin = "(Admin)" in c["status"]
                new_status = status_text if status_text else ("online" if online else "offline")
                if not online: new_status = "offline"
                if is_admin: new_status += " (Admin)"
                c["status"] = new_status
                break
        self._apply_search_filter()
        app = wx.GetApp()
        if online and not was_online:
            app.play_sound("contact_online.wav")
            if app.user_config.get('announce_status', False):
                speak(f"{user} has come online.")
            else:
                show_notification("Contact online", f"{user} has come online.")
        elif not online and was_online:
            app.play_sound("contact_offline.wav")
            if app.user_config.get('announce_status', False):
                speak(f"{user} has gone offline.")
            else:
                show_notification("Contact offline", f"{user} has gone offline.")

    def __init__(self, user, sock):
        super().__init__(None, title=f"Thrive Messenger – {user}", size=(400,380)); self.user, self.sock = user, sock; self.task_bar_icon = None; self.is_exiting = False; self._directory_dlg = None; self._conversations_dlg = None; self._noncontact_senders = load_noncontact_senders(user)
        self.current_status = wx.GetApp().user_config.get('status', 'online')
        self.notifications = []; self.Bind(wx.EVT_CLOSE, self.on_close_window); panel = wx.Panel(self)

        dark_mode_on = is_windows_dark_mode()
        if dark_mode_on:
            dark_color = wx.Colour(40, 40, 40); light_text_color = wx.WHITE
            WxMswDarkMode().enable(self); self.SetBackgroundColour(dark_color); panel.SetBackgroundColour(dark_color)

        self._all_contacts = []
        box_contacts = wx.StaticBoxSizer(wx.VERTICAL, panel, "&Contacts")
        search_label = wx.StaticText(box_contacts.GetStaticBox(), label="Searc&h contacts:")
        self.search_box = wx.TextCtrl(box_contacts.GetStaticBox(), style=wx.TE_PROCESS_ENTER)
        self.search_box.Bind(wx.EVT_TEXT, self.on_search)
        self.lv = wx.ListCtrl(box_contacts.GetStaticBox(), style=wx.LC_REPORT); self.lv.InsertColumn(0, "Username", width=120); self.lv.InsertColumn(1, "Status", width=160)
        self.lv.Bind(wx.EVT_CHAR_HOOK, self.on_key); self.lv.Bind(wx.EVT_LIST_ITEM_SELECTED, self.update_button_states); self.lv.Bind(wx.EVT_LIST_ITEM_DESELECTED, self.update_button_states)

        if dark_mode_on:
            box_contacts.GetStaticBox().SetForegroundColour(light_text_color)
            box_contacts.GetStaticBox().SetBackgroundColour(dark_color)
            search_label.SetForegroundColour(light_text_color)
            self.search_box.SetBackgroundColour(dark_color); self.search_box.SetForegroundColour(light_text_color)
            self.lv.SetBackgroundColour(dark_color); self.lv.SetForegroundColour(light_text_color)

        search_sizer = wx.BoxSizer(wx.HORIZONTAL)
        search_sizer.Add(search_label, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 5)
        search_sizer.Add(self.search_box, 1, wx.EXPAND)
        box_contacts.Add(search_sizer, 0, wx.EXPAND|wx.LEFT|wx.RIGHT|wx.TOP, 5)
        box_contacts.Add(self.lv, 1, wx.EXPAND|wx.ALL, 5)
        self.btn_block = wx.Button(panel, label="&Block"); self.btn_add = wx.Button(panel, label="&Add Contact"); self.btn_send = wx.Button(panel, label="&Start Chat"); self.btn_delete = wx.Button(panel, label="&Delete Contact")
        self.btn_send_file = wx.Button(panel, label="Send &File")
        self.btn_info = wx.Button(panel, label="Server &Info")
        self.btn_status = wx.Button(panel, label="Set Stat&us...")
        self.btn_directory = wx.Button(panel, label="User Director&y")
        self.btn_admin = wx.Button(panel, label="Use Ser&ver Side Commands"); self.btn_settings = wx.Button(panel, label="Se&ttings...")
        self.btn_update = wx.Button(panel, label="Check for U&pdates")
        self.btn_conv = wx.Button(panel, label="&Conversations...")
        self.btn_logout = wx.Button(panel, label="L&ogout"); self.btn_exit = wx.Button(panel, label="E&xit")

        if dark_mode_on:
            buttons = [self.btn_block, self.btn_add, self.btn_send, self.btn_delete, self.btn_send_file, self.btn_info, self.btn_status, self.btn_directory, self.btn_conv, self.btn_admin, self.btn_settings, self.btn_update, self.btn_logout, self.btn_exit]
            for btn in buttons:
                btn.SetBackgroundColour(dark_color)
                btn.SetForegroundColour(light_text_color)
                
        self.btn_block.Bind(wx.EVT_BUTTON, self.on_block_toggle); self.btn_add.Bind(wx.EVT_BUTTON, self.on_add); self.btn_send.Bind(wx.EVT_BUTTON, self.on_send); self.btn_delete.Bind(wx.EVT_BUTTON, self.on_delete)
        self.btn_send_file.Bind(wx.EVT_BUTTON, self.on_send_file)
        self.btn_info.Bind(wx.EVT_BUTTON, self.on_server_info)
        self.btn_status.Bind(wx.EVT_BUTTON, self.on_set_status)
        self.btn_directory.Bind(wx.EVT_BUTTON, self.on_user_directory)
        self.btn_admin.Bind(wx.EVT_BUTTON, self.on_admin); self.btn_settings.Bind(wx.EVT_BUTTON, self.on_settings)
        self.btn_update.Bind(wx.EVT_BUTTON, self.on_check_updates)
        self.btn_conv.Bind(wx.EVT_BUTTON, self.on_conversations)
        self.btn_logout.Bind(wx.EVT_BUTTON, self.on_logout); self.btn_exit.Bind(wx.EVT_BUTTON, self.on_exit)
        accel_entries = [(wx.ACCEL_ALT, ord('B'), self.btn_block.GetId()), (wx.ACCEL_ALT, ord('A'), self.btn_add.GetId()), (wx.ACCEL_ALT, ord('S'), self.btn_send.GetId()), (wx.ACCEL_ALT, ord('D'), self.btn_delete.GetId()), (wx.ACCEL_ALT, ord('F'), self.btn_send_file.GetId()), (wx.ACCEL_ALT, ord('I'), self.btn_info.GetId()), (wx.ACCEL_ALT, ord('U'), self.btn_status.GetId()), (wx.ACCEL_ALT, ord('Y'), self.btn_directory.GetId()), (wx.ACCEL_ALT, ord('C'), self.btn_conv.GetId()), (wx.ACCEL_ALT, ord('V'), self.btn_admin.GetId()), (wx.ACCEL_ALT, ord('T'), self.btn_settings.GetId()), (wx.ACCEL_ALT, ord('P'), self.btn_update.GetId()), (wx.ACCEL_ALT, ord('O'), self.btn_logout.GetId()), (wx.ACCEL_ALT, ord('X'), self.btn_exit.GetId()),]
        accel_tbl = wx.AcceleratorTable(accel_entries); self.SetAcceleratorTable(accel_tbl)
        gs_main = wx.GridSizer(1, 5, 5, 5); gs_main.Add(self.btn_block, 0, wx.EXPAND); gs_main.Add(self.btn_add, 0, wx.EXPAND); gs_main.Add(self.btn_send, 0, wx.EXPAND); gs_main.Add(self.btn_send_file, 0, wx.EXPAND); gs_main.Add(self.btn_delete, 0, wx.EXPAND)
        gs_util = wx.GridSizer(1, 9, 5, 5); gs_util.Add(self.btn_info, 0, wx.EXPAND); gs_util.Add(self.btn_status, 0, wx.EXPAND); gs_util.Add(self.btn_directory, 0, wx.EXPAND); gs_util.Add(self.btn_conv, 0, wx.EXPAND); gs_util.Add(self.btn_admin, 0, wx.EXPAND); gs_util.Add(self.btn_settings, 0, wx.EXPAND); gs_util.Add(self.btn_update, 0, wx.EXPAND); gs_util.Add(self.btn_logout, 0, wx.EXPAND); gs_util.Add(self.btn_exit, 0, wx.EXPAND)
        s = wx.BoxSizer(wx.VERTICAL); s.Add(box_contacts, 1, wx.EXPAND|wx.ALL, 5); s.Add(gs_main, 0, wx.CENTER|wx.ALL, 5); s.Add(gs_util, 0, wx.CENTER|wx.ALL, 5); panel.SetSizer(s)
        self.update_button_states()
    def on_settings(self, event):
        app = wx.GetApp()
        with SettingsDialog(self, app.user_config) as dlg:
            if dlg.ShowModal() == wx.ID_OK:
                selected_pack = dlg.choice.GetStringSelection(); app.user_config['soundpack'] = selected_pack
                app.user_config['tts_enabled'] = dlg.tts_cb.IsChecked(); app.user_config['announce_status'] = dlg.announce_status_cb.IsChecked(); app.user_config['announce_files'] = dlg.announce_files_cb.IsChecked(); app.user_config['interrupt_speech'] = dlg.interrupt_speech_cb.IsChecked(); save_user_config(app.user_config)
                wx.MessageBox("Settings have been applied.", "Settings Saved", wx.OK | wx.ICON_INFORMATION)
    def on_conversations(self, _):
        if self._conversations_dlg:
            self._conversations_dlg.Raise(); self._conversations_dlg.SetFocus(); return
        dlg = ConversationsDialog(self); self._conversations_dlg = dlg; dlg.Show()
    def on_change_password_result(self, msg):
        if msg.get("ok"):
            wx.MessageBox("Password changed successfully.", "Success", wx.OK | wx.ICON_INFORMATION)
        else:
            reason = msg.get("reason", "Unknown error.")
            wx.MessageBox(f"Could not change password: {reason}", "Error", wx.ICON_ERROR)
    def on_user_directory(self, _):
        if self._directory_dlg:
            self._directory_dlg.Raise(); self._directory_dlg.SetFocus(); return
        self.sock.sendall(json.dumps({"action": "user_directory"}).encode() + b"\n")
    def on_user_directory_response(self, msg):
        users = msg.get("users", [])
        dlg = UserDirectoryDialog(self, users, self.user, self.contact_states)
        self._directory_dlg = dlg
        dlg.Show()
    def on_server_info(self, _):
        self.sock.sendall(json.dumps({"action": "server_info"}).encode() + b"\n")
    def on_server_info_response(self, msg):
        encrypted = isinstance(self.sock, ssl.SSLSocket)
        size_limit = msg.get("size_limit", 0)
        size_str = format_size(size_limit) if size_limit > 0 else "No limit"
        blackfiles = msg.get("blackfiles", [])
        blackfiles_str = ", ".join(f".{ext}" for ext in blackfiles) if blackfiles else "None"
        max_status_len = msg.get("max_status_length", "N/A")
        info = [("Hostname", SERVER_CONFIG['host']), ("Port", str(msg.get("port", SERVER_CONFIG['port']))), ("Encrypted", "Yes" if encrypted else "No"), ("Registered Users", str(msg.get("total_users", "N/A"))), ("Users Online", str(msg.get("online_users", "N/A"))), ("File Size Limit", size_str), ("Blacklisted Extensions", blackfiles_str), ("Max Status Length", str(max_status_len))]
        with ServerInfoDialog(self, info) as dlg: dlg.ShowModal()
    def update_button_states(self, event=None):
        is_selection = self.lv.GetSelectedItemCount() > 0
        self.btn_send.Enable(is_selection); self.btn_delete.Enable(is_selection); self.btn_block.Enable(is_selection); self.btn_send_file.Enable(is_selection)
        if is_selection:
            sel_idx = self.lv.GetFirstSelected(); contact_name = self.lv.GetItemText(sel_idx)
            is_blocked = self.contact_states.get(contact_name, 0); self.btn_block.SetLabel("&Unblock" if is_blocked else "&Block")
        else: self.btn_block.SetLabel("&Block")
        if event: event.Skip()
    def on_set_status(self, event):
        with StatusDialog(self, self.current_status) as dlg:
            if dlg.ShowModal() == wx.ID_OK:
                sel = dlg.choice.GetStringSelection()
                status = dlg.status_text.GetValue().strip() if sel == "Custom..." else sel
                if not status: return
                self.current_status = status
                app = wx.GetApp(); app.user_config['status'] = status; save_user_config(app.user_config)
                try: self.sock.sendall((json.dumps({"action": "set_status", "status_text": status}) + "\n").encode())
                except Exception as e: print(f"Error setting status: {e}")
    def on_check_updates(self, event=None, silent=False):
        self.btn_update.Disable()
        def _callback(tag, version_str, error):
            self.btn_update.Enable()
            if tag:
                result = wx.MessageBox(
                    f"A new version is available: {tag}\nYou are currently running {VERSION_TAG}.\n\nWould you like to download and install it?",
                    "Update Available", wx.YES_NO | wx.ICON_INFORMATION, self)
                if result == wx.YES:
                    self._start_update_download(tag)
            elif error and not silent:
                wx.MessageBox(f"Could not check for updates:\n{error}", "Update Check Failed", wx.ICON_ERROR)
            elif not error and not silent:
                wx.MessageBox(f"You are running the latest version, {VERSION_TAG}.", "No Updates", wx.ICON_INFORMATION)
        check_for_update(_callback)
    def _start_update_download(self, tag):
        import urllib.request
        api_url = f"https://api.github.com/repos/G4p-Studios/ThriveMessenger/releases/tags/{tag}"
        try:
            req = urllib.request.Request(api_url, headers={"Accept": "application/vnd.github+json", "User-Agent": "ThriveMessenger/" + VERSION_TAG})
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode())
        except Exception as e:
            wx.MessageBox(f"Failed to fetch release info:\n{e}", "Update Error", wx.ICON_ERROR); return
        assets = data.get("assets", [])
        use_installer = is_installer_install()
        target_name = "thrive_messenger_installer.exe" if use_installer else "thrive_messenger.zip"
        asset_url = None
        for a in assets:
            if a["name"] == target_name:
                asset_url = a["browser_download_url"]; break
        if not asset_url:
            wx.MessageBox(f"Could not find {target_name} in release assets.", "Update Error", wx.ICON_ERROR); return
        ext = ".exe" if use_installer else ".zip"
        dest = os.path.join(tempfile.gettempdir(), f"thrive_update{ext}")
        progress = wx.ProgressDialog("Downloading Update", "Starting download...", maximum=100, parent=self,
            style=wx.PD_APP_MODAL | wx.PD_AUTO_HIDE | wx.PD_CAN_ABORT | wx.PD_SMOOTH)
        def _done(success, error):
            progress.Destroy()
            if success:
                if use_installer:
                    apply_installer_update(dest)
                else:
                    apply_zip_update(dest)
                app = wx.GetApp(); app.intentional_disconnect = True
                try: self.sock.sendall(json.dumps({"action":"logout"}).encode()+b"\n")
                except: pass
                try: self.sock.close()
                except: pass
                if self.task_bar_icon: self.task_bar_icon.Destroy()
                self.is_exiting = True; self.Destroy()
                app.ExitMainLoop()
            else:
                wx.MessageBox(f"Download failed:\n{error}", "Update Error", wx.ICON_ERROR)
        download_update(asset_url, dest, progress, _done)
    def on_add_contact_failed(self, reason): wx.MessageBox(reason, "Add Contact Failed", wx.ICON_ERROR)
    def on_add_contact_success(self, contact_data):
        c = contact_data; self.contact_states[c["user"]] = c["blocked"]
        status = c.get("status_text", "online") if c["online"] and not c["blocked"] else "offline"
        if c.get("is_admin"): status += " (Admin)"
        self._all_contacts.append({"user": c["user"], "status": status, "blocked": c["blocked"]})
        self._noncontact_senders.discard(c["user"])
        save_noncontact_senders(self.user, self._noncontact_senders)
        self._apply_search_filter()
        if self._conversations_dlg: self._conversations_dlg.refresh()
        chat = self.get_chat(c["user"])
        if chat: chat.hide_add_button()
        if self._directory_dlg:
            for u in self._directory_dlg._all_users:
                if u["user"] == c["user"]: u["is_contact"] = True; u["is_blocked"] = c["blocked"] == 1; break
            self._directory_dlg._populate_all_tabs(); self._directory_dlg.update_button_states()
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
        self.contact_states = {c["user"]: c["blocked"] for c in contacts}
        self._all_contacts = []
        for c in contacts:
            status = c.get("status_text", "online") if c["online"] and not c["blocked"] else "offline"
            if c.get("is_admin"): status += " (Admin)"
            self._all_contacts.append({"user": c["user"], "status": status, "blocked": c["blocked"]})
        self._apply_search_filter()
    def _apply_search_filter(self):
        query = self.search_box.GetValue().strip().lower()
        self.lv.DeleteAllItems()
        for c in self._all_contacts:
            if query and query not in c["user"].lower(): continue
            idx = self.lv.InsertItem(self.lv.GetItemCount(), c["user"])
            self.lv.SetItem(idx, 1, c["status"])
            if c["blocked"]: self.lv.SetItemTextColour(idx, wx.Colour(150,150,150))
        self.update_button_states()
    def on_search(self, event):
        self._apply_search_filter()
    def on_admin_status_change(self, user, is_admin):
        for c in self._all_contacts:
            if c["user"] == user:
                base_status = c["status"].replace(" (Admin)", "")
                c["status"] = base_status + " (Admin)" if is_admin else base_status; break
        self._apply_search_filter()
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
        else:
            if sys.platform == 'win32':
                # While we are still the foreground process, hand focus to the
                # topmost visible window belonging to another process.  This
                # prevents Windows from promoting any of our owned windows when
                # we hide, because focus already belongs to someone else.
                # We intentionally skip the IsWindowEnabled check because apps
                # like VMware Workstation report as disabled when the VM has
                # input capture, but can still legitimately receive foreground.
                our_pid = ctypes.windll.kernel32.GetCurrentProcessId()
                EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
                def _find_other(hwnd, _):
                    if not ctypes.windll.user32.IsWindowVisible(hwnd): return True
                    pid = ctypes.c_ulong(0)
                    ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                    if pid.value != our_pid:
                        ctypes.windll.user32.SetForegroundWindow(hwnd)
                        return False
                    return True
                ctypes.windll.user32.EnumWindows(EnumWindowsProc(_find_other), None)
            for child in self.GetChildren():
                if isinstance(child, ChatDialog) and child.IsShown():
                    child._restore_from_tray = True; child.Hide()
            self.Hide(); self.task_bar_icon = ThriveTaskBarIcon(self)
    def restore_from_tray(self):
        if self.task_bar_icon: self.task_bar_icon.Destroy(); self.task_bar_icon = None
        self.Show(); self.Raise()
        if self._directory_dlg and self._directory_dlg.IsShown(): self._directory_dlg.Raise()
        for child in self.GetChildren():
            if isinstance(child, ChatDialog) and getattr(child, '_restore_from_tray', False):
                child._restore_from_tray = False; child.Show()
            if isinstance(child, (ChatDialog, AdminDialog)) and child.IsShown(): child.Raise()
    def on_exit(self, _):
        print("Exiting application...");
        app = wx.GetApp(); app.intentional_disconnect = True
        try: self.sock.sendall(json.dumps({"action":"logout"}).encode()+b"\n")
        except: pass
        try: self.sock.close()
        except: pass
        if self._directory_dlg: self._directory_dlg.Destroy(); self._directory_dlg = None
        if self._conversations_dlg: self._conversations_dlg.Destroy(); self._conversations_dlg = None
        if self.task_bar_icon: self.task_bar_icon.Destroy()
        self.is_exiting = True; self.Destroy()
        app.ExitMainLoop()
    def on_logout(self, _):
        self.is_exiting = True; app = wx.GetApp(); app.intentional_disconnect = True
        try: self.sock.sendall(json.dumps({"action":"logout"}).encode()+b"\n")
        except: pass
        try: self.sock.close()
        except: pass
        if self._directory_dlg: self._directory_dlg.Destroy(); self._directory_dlg = None
        if self._conversations_dlg: self._conversations_dlg.Destroy(); self._conversations_dlg = None
        app.play_sound("logout.wav"); self.Destroy()
        app.show_login_dialog()
    def on_key(self, evt):
        if evt.GetKeyCode() == wx.WXK_RETURN: self.on_send(None)
        elif evt.GetKeyCode() == wx.WXK_DELETE: self.on_delete(None)
        else: evt.Skip()
    def on_block_toggle(self, _):
        sel = self.lv.GetFirstSelected()
        if sel < 0: return
        c = self.lv.GetItemText(sel); blocked = self.contact_states.get(c,0) == 1; action = "unblock_contact" if blocked else "block_contact"; self.sock.sendall(json.dumps({"action":action,"to":c}).encode()+b"\n"); self.contact_states[c] = 0 if blocked else 1
        for entry in self._all_contacts:
            if entry["user"] == c: entry["blocked"] = 0 if blocked else 1; break
        self._apply_search_filter()
    def on_delete(self, _):
        sel = self.lv.GetFirstSelected()
        if sel < 0: return
        c = self.lv.GetItemText(sel); self.sock.sendall(json.dumps({"action":"delete_contact","to":c}).encode()+b"\n"); self.contact_states.pop(c, None)
        self._all_contacts = [entry for entry in self._all_contacts if entry["user"] != c]
        self._apply_search_filter()
    def on_send(self, _):
        sel = self.lv.GetFirstSelected()
        if sel < 0: return
        c = self.lv.GetItemText(sel);
        app = wx.GetApp(); logging_config = app.user_config.get('chat_logging', {}); is_logging_enabled = logging_config.get(c, False)
        dlg = self.get_chat(c) or ChatDialog(self, c, self.sock, self.user, is_logging_enabled)
        dlg.Show(); dlg.input_ctrl.SetFocus()
    def on_send_file(self, _):
        sel = self.lv.GetFirstSelected()
        if sel < 0: return
        c = self.lv.GetItemText(sel)
        wx.GetApp().send_file_to(c)
    def receive_message(self, msg):
        wx.GetApp().play_sound("receive.wav");
        app = wx.GetApp(); logging_config = app.user_config.get('chat_logging', {}); is_logging_enabled = logging_config.get(msg["from"], False)
        is_contact = msg["from"] in self.contact_states
        dlg = self.get_chat(msg["from"])
        if not dlg:
            dlg = ChatDialog(self, msg["from"], self.sock, self.user, is_logging_enabled, is_contact=is_contact)
        if not is_contact and msg["from"] not in self._noncontact_senders:
            self._noncontact_senders.add(msg["from"]); self._apply_search_filter()
            save_noncontact_senders(self.user, self._noncontact_senders)
        if wx.GetActiveWindow() is not None:
            dlg.Show()
        elif sys.platform == 'win32':
            _shown = False
            try:
                hwnd = dlg.GetHandle()
                # CBT hook blocks HCBT_ACTIVATE for this window during Show() so wx
                # fully initialises it but WM_ACTIVATE is never sent — no focus change,
                # no screen reader announcement.  No WS_EX_APPWINDOW: keeping the dialog
                # as a plain owned window means Windows auto-hides it with the owner and
                # won't promote it to foreground when the owner is hidden to tray.
                _CBTProc = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_int, ctypes.WPARAM, ctypes.LPARAM)
                def _no_activate(nCode, wParam, lParam):
                    if nCode == 5 and wParam == hwnd:  # HCBT_ACTIVATE for our window
                        return 1
                    return ctypes.windll.user32.CallNextHookEx(None, nCode, wParam, lParam)
                _cb = _CBTProc(_no_activate)
                tid = ctypes.windll.kernel32.GetCurrentThreadId()
                hook = ctypes.windll.user32.SetWindowsHookExW(5, _cb, None, tid)  # WH_CBT
                try:
                    dlg.Show(); _shown = True
                finally:
                    if hook: ctypes.windll.user32.UnhookWindowsHookEx(hook)
            except Exception:
                pass
            if not _shown:
                dlg.Show()
        else:
            dlg.Show()
        dlg.append(msg["msg"], msg["from"], msg["time"])
        if app.user_config.get('tts_enabled', True):
            speak(f"{msg['from']}: {msg['msg']}")
    def on_message_failed(self, to, reason): chat_dlg = self.get_chat(to); (chat_dlg.append_error(reason) if chat_dlg else wx.MessageBox(reason, "Message Failed", wx.OK | wx.ICON_ERROR))
    def on_offline_messages(self, messages):
        if not messages: return
        by_sender = {}
        for m in messages:
            by_sender.setdefault(m["from"], []).append(m)
        app = wx.GetApp(); logging_config = app.user_config.get('chat_logging', {})
        any_new_noncontact = False
        for sender, msgs in by_sender.items():
            is_contact = sender in self.contact_states
            dlg = self.get_chat(sender) or ChatDialog(self, sender, self.sock, self.user, logging_config.get(sender, False), is_contact=is_contact)
            for m in msgs:
                dlg.append(m["msg"], m["from"], m["time"])
            if not is_contact and sender not in self._noncontact_senders:
                self._noncontact_senders.add(sender); any_new_noncontact = True
        if any_new_noncontact:
            save_noncontact_senders(self.user, self._noncontact_senders)
        n_msgs = len(messages); n_senders = len(by_sender)
        summary = ", ".join(sorted(by_sender.keys()))
        result = wx.MessageBox(
            f"You received {n_msgs} message{'s' if n_msgs != 1 else ''} while offline "
            f"from {n_senders} user{'s' if n_senders != 1 else ''} ({summary}).\n\nWould you like to view {'them' if n_msgs != 1 else 'it'}?",
            "Missed Messages", wx.YES_NO | wx.ICON_INFORMATION)
        if result == wx.YES:
            OfflineMessagesDialog(self, by_sender).Show()
    def get_chat(self, contact):
        for child in self.GetChildren():
            if isinstance(child, ChatDialog) and child.contact == contact: return child
        return None

def get_day_with_suffix(d): return str(d) + "th" if 11 <= d <= 13 else str(d) + {1: "st", 2: "nd", 3: "rd"}.get(d % 10, "th")
def format_timestamp(ts):
    try:
        if isinstance(ts, (int, float)):
            dt = datetime.datetime.fromtimestamp(ts)
        else:
            try:
                dt = datetime.datetime.fromtimestamp(float(ts))
            except (ValueError, TypeError):
                dt = datetime.datetime.fromisoformat(ts)  # backward compat with old ISO strings on disk
        day_with_suffix = get_day_with_suffix(dt.day)
        formatted_hour = dt.strftime('%I:%M %p').lstrip('0')
        return dt.strftime(f'%A, %B {day_with_suffix}, %Y at {formatted_hour}')
    except (ValueError, TypeError, OSError): return str(ts)

class AdminDialog(wx.Dialog):
    def __init__(self, parent, sock):
        super().__init__(parent, title="Server Side Commands", size=(450, 300)); self.sock = sock; self.Bind(wx.EVT_CHAR_HOOK, self.on_key); s = wx.BoxSizer(wx.VERTICAL)
        
        dark_mode_on = is_windows_dark_mode()
        if dark_mode_on:
            dark_color = wx.Colour(40, 40, 40); light_text_color = wx.WHITE
            WxMswDarkMode().enable(self); self.SetBackgroundColour(dark_color)
            
        self.hist = wx.ListCtrl(self, style=wx.LC_REPORT)
        self.hist.InsertColumn(0, "Server Response", width=200); self.hist.InsertColumn(1, "Time", width=220)
        box_msg = wx.StaticBoxSizer(wx.VERTICAL, self, "&Enter command (e.g., /create user pass [email])"); self.input_ctrl = wx.TextCtrl(box_msg.GetStaticBox(), style=wx.TE_PROCESS_ENTER)
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
        ts = time.time(); idx = self.hist.GetItemCount(); self.hist.InsertItem(idx, text); self.hist.SetItem(idx, 1, format_timestamp(ts))
        if text.lower().startswith('error'): self.hist.SetItemTextColour(idx, wx.RED)
        if wx.GetApp().user_config.get('tts_enabled', False): speak(text)

class ChatDialog(wx.Dialog):
    def __init__(self, parent, contact, sock, user, logging_enabled=False, is_contact=True):
        super().__init__(parent, title=f"Chat with {contact}", size=(450, 450))
        self.contact, self.sock, self.user = contact, sock, user
        self.is_contact = is_contact
        self._msg_log = []
        self.Bind(wx.EVT_CHAR_HOOK, self.on_key)
        self.Bind(wx.EVT_CLOSE, self.on_close)

        dark_mode_on = is_windows_dark_mode()
        if dark_mode_on:
            dark_color = wx.Colour(40, 40, 40); light_text_color = wx.WHITE
            WxMswDarkMode().enable(self); self.SetBackgroundColour(dark_color)

        s = wx.BoxSizer(wx.VERTICAL)
        self.btn_add_contact = wx.Button(self, label="&Add to Contacts")
        self.btn_add_contact.Bind(wx.EVT_BUTTON, self.on_add_contact)
        if dark_mode_on:
            self.btn_add_contact.SetBackgroundColour(dark_color); self.btn_add_contact.SetForegroundColour(light_text_color)
        s.Add(self.btn_add_contact, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 5)
        if is_contact: self.btn_add_contact.Hide()
        self.hist = wx.ListCtrl(self, style=wx.LC_REPORT)
        self.hist.InsertColumn(0, "Sender", width=80); self.hist.InsertColumn(1, "Message", width=160); self.hist.InsertColumn(2, "Time", width=180)
        self.save_hist_cb = wx.CheckBox(self, label="Sa&ve chat history")
        self.save_hist_cb.SetValue(logging_enabled); self.save_hist_cb.Bind(wx.EVT_CHECKBOX, self.on_toggle_save)
        box_msg = wx.StaticBoxSizer(wx.VERTICAL, self, "Type &message (Shift+Enter for newline)")
        self.input_ctrl = wx.TextCtrl(box_msg.GetStaticBox(), style=wx.TE_MULTILINE)
        btn = wx.Button(self, label="&Send")
        btn_file = wx.Button(self, label="Send &File")

        if dark_mode_on:
            self.hist.SetBackgroundColour(dark_color); self.hist.SetForegroundColour(light_text_color)
            box_msg.GetStaticBox().SetForegroundColour(light_text_color)
            box_msg.GetStaticBox().SetBackgroundColour(dark_color)
            self.input_ctrl.SetBackgroundColour(dark_color); self.input_ctrl.SetForegroundColour(light_text_color)
            btn.SetBackgroundColour(dark_color); btn.SetForegroundColour(light_text_color)
            btn_file.SetBackgroundColour(dark_color); btn_file.SetForegroundColour(light_text_color)

        s.Add(self.hist, 1, wx.EXPAND|wx.ALL, 5)
        s.Add(self.save_hist_cb, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)
        self.input_ctrl.Bind(wx.EVT_KEY_DOWN, self.on_input_key)
        box_msg.Add(self.input_ctrl, 1, wx.EXPAND|wx.ALL, 5)
        s.Add(box_msg, 1, wx.EXPAND|wx.ALL, 5)

        btn.Bind(wx.EVT_BUTTON, self.on_send)
        btn_file.Bind(wx.EVT_BUTTON, self.on_send_file)
        btn_sizer = wx.BoxSizer(wx.HORIZONTAL)
        btn_sizer.Add(btn, 1, wx.EXPAND | wx.ALL, 5)
        btn_sizer.Add(btn_file, 1, wx.EXPAND | wx.ALL, 5)
        s.Add(btn_sizer, 0, wx.EXPAND|wx.ALL, 5)
        self.SetSizer(s)
        if not is_contact:
            self._load_saved_messages()
    def _load_saved_messages(self):
        for m in load_noncontact_messages(self.user, self.contact):
            self._msg_log.append(m)
            idx = self.hist.GetItemCount()
            self.hist.InsertItem(idx, m['sender']); self.hist.SetItem(idx, 1, m['text'])
            self.hist.SetItem(idx, 2, format_timestamp(m['ts']))
            if m.get('is_error'): self.hist.SetItemTextColour(idx, wx.RED)
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
    def on_close(self, event):
        parent = self.GetParent()
        if parent and hasattr(parent, '_directory_dlg') and parent._directory_dlg and parent._directory_dlg.IsShown():
            wx.CallAfter(parent._directory_dlg.Raise)
            wx.CallAfter(parent._directory_dlg.SetFocus)
        if self.is_contact:
            event.Skip()
        else:
            self.Hide()
            frame = self.GetParent()
            if frame._conversations_dlg:
                frame._conversations_dlg.Raise(); frame._conversations_dlg.SetFocus()
    def on_key(self, event):
        if event.GetKeyCode() == wx.WXK_ESCAPE:
            self.Close()
        elif event.GetKeyCode() == ord('C') and event.ControlDown():
            if wx.Window.FindFocus() is not self.input_ctrl:
                sel = self.hist.GetFirstSelected()
                if sel >= 0:
                    text = self.hist.GetItemText(sel, 1)
                    if wx.TheClipboard.Open():
                        wx.TheClipboard.SetData(wx.TextDataObject(text))
                        wx.TheClipboard.Close()
                        if wx.GetApp().user_config.get('tts_enabled', False):
                            speak("Copied.")
            else:
                event.Skip()
        else:
            event.Skip()
    def on_send(self, _):
        txt = self.input_ctrl.GetValue().strip()
        if not txt: return
        ts = time.time()
        msg = {"action":"msg","to":self.contact,"from":self.user,"msg":txt,"time":ts}
        self.sock.sendall(json.dumps(msg).encode()+b"\n")
        self.append(txt, self.user, ts)
        wx.GetApp().play_sound("send.wav")
        self.input_ctrl.Clear(); self.input_ctrl.SetFocus()
    def on_send_file(self, _):
        wx.GetApp().send_file_to(self.contact)
    def on_add_contact(self, _):
        self.sock.sendall(json.dumps({"action": "add_contact", "to": self.contact}).encode() + b"\n")
        self.btn_add_contact.Disable(); self.btn_add_contact.SetLabel("Adding...")
    def hide_add_button(self):
        self.is_contact = True
        self.btn_add_contact.Hide(); self.GetSizer().Layout()
    def append(self, text, sender, ts, is_error=False):
        idx = self.hist.GetItemCount(); self.hist.InsertItem(idx, sender); self.hist.SetItem(idx, 1, text)
        formatted_time = format_timestamp(ts); self.hist.SetItem(idx, 2, formatted_time)
        if is_error: self.hist.SetItemTextColour(idx, wx.RED)
        if self.save_hist_cb.IsChecked():
            log_line = f"[{formatted_time}] {sender}: {text}\n"
            self._save_message_to_log(log_line)
        if not self.is_contact:
            self._msg_log.append({'sender': sender, 'text': text, 'ts': ts, 'is_error': is_error})
            save_noncontact_messages(self.user, self.contact, self._msg_log)
    def append_error(self, reason):
        ts = time.time()
        self.append(reason, "System", ts, is_error=True)
        self.input_ctrl.SetFocus()

class ConversationsDialog(wx.Dialog):
    def __init__(self, parent):
        super().__init__(parent, title="Conversations", size=(300, 350))
        self.Bind(wx.EVT_CHAR_HOOK, self.on_key)
        self.Bind(wx.EVT_CLOSE, self.on_close)

        dark_mode_on = is_windows_dark_mode()
        if dark_mode_on:
            dark_color = wx.Colour(40, 40, 40); light_text_color = wx.WHITE
            WxMswDarkMode().enable(self); self.SetBackgroundColour(dark_color)

        s = wx.BoxSizer(wx.VERTICAL)
        lbl = wx.StaticText(self, label="Users who have messaged you (not in contacts):")
        self.lv = wx.ListCtrl(self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL)
        self.lv.InsertColumn(0, "User", width=260)
        self.lv.Bind(wx.EVT_LIST_ITEM_ACTIVATED, self.on_open_chat)
        self.lv.Bind(wx.EVT_LIST_ITEM_SELECTED, lambda e: (e.Skip(), self._update_buttons()))
        self.lv.Bind(wx.EVT_LIST_ITEM_DESELECTED, lambda e: (e.Skip(), self._update_buttons()))
        self.btn_open = wx.Button(self, label="&Open Chat")
        self.btn_add = wx.Button(self, label="&Add to Contacts")
        self.btn_block = wx.Button(self, label="&Block")
        self.btn_close = wx.Button(self, label="C&lose")
        self.btn_open.Bind(wx.EVT_BUTTON, self.on_open_chat)
        self.btn_add.Bind(wx.EVT_BUTTON, self.on_add_contact)
        self.btn_block.Bind(wx.EVT_BUTTON, self.on_block)
        self.btn_close.Bind(wx.EVT_BUTTON, lambda e: self.Close())

        if dark_mode_on:
            for w in [self.lv, self.btn_open, self.btn_add, self.btn_block, self.btn_close]:
                w.SetBackgroundColour(dark_color); w.SetForegroundColour(light_text_color)
            lbl.SetForegroundColour(light_text_color)

        btn_sizer = wx.BoxSizer(wx.HORIZONTAL)
        btn_sizer.Add(self.btn_open, 1, wx.EXPAND | wx.ALL, 5)
        btn_sizer.Add(self.btn_add, 1, wx.EXPAND | wx.ALL, 5)
        btn_sizer.Add(self.btn_block, 1, wx.EXPAND | wx.ALL, 5)
        btn_sizer.Add(self.btn_close, 1, wx.EXPAND | wx.ALL, 5)
        s.Add(lbl, 0, wx.LEFT | wx.TOP | wx.RIGHT, 10)
        s.Add(self.lv, 1, wx.EXPAND | wx.ALL, 5)
        s.Add(btn_sizer, 0, wx.EXPAND | wx.BOTTOM, 5)
        self.SetSizer(s)
        self.refresh()
        self._update_buttons()

    def refresh(self):
        frame = self.GetParent()
        self.lv.DeleteAllItems()
        for username in sorted(frame._noncontact_senders):
            if username in frame.contact_states: continue
            self.lv.InsertItem(self.lv.GetItemCount(), username)
        self._update_buttons()

    def _update_buttons(self):
        has_sel = self.lv.GetSelectedItemCount() > 0
        self.btn_open.Enable(has_sel); self.btn_add.Enable(has_sel); self.btn_block.Enable(has_sel)

    def on_open_chat(self, _):
        sel = self.lv.GetFirstSelected()
        if sel < 0: return
        username = self.lv.GetItemText(sel); frame = self.GetParent()
        app = wx.GetApp(); logging_config = app.user_config.get('chat_logging', {}); is_logging_enabled = logging_config.get(username, False)
        dlg = frame.get_chat(username) or ChatDialog(frame, username, frame.sock, frame.user, is_logging_enabled, is_contact=False)
        dlg.Show(); dlg.Raise(); dlg.input_ctrl.SetFocus()

    def on_add_contact(self, _):
        sel = self.lv.GetFirstSelected()
        if sel < 0: return
        username = self.lv.GetItemText(sel)
        self.GetParent().sock.sendall(json.dumps({"action": "add_contact", "to": username}).encode() + b"\n")

    def on_block(self, _):
        sel = self.lv.GetFirstSelected()
        if sel < 0: return
        username = self.lv.GetItemText(sel); frame = self.GetParent()
        frame.sock.sendall(json.dumps({"action": "block_contact", "to": username}).encode() + b"\n")
        chat = frame.get_chat(username)
        if chat: chat.Destroy()
        frame._noncontact_senders.discard(username)
        save_noncontact_senders(frame.user, frame._noncontact_senders)
        delete_noncontact_messages(frame.user, username)
        self.refresh()

    def on_close(self, event):
        self.GetParent()._conversations_dlg = None; event.Skip()

    def on_key(self, event):
        if event.GetKeyCode() == wx.WXK_ESCAPE: self.Close()
        else: event.Skip()

class OfflineMessagesDialog(wx.Dialog):
    def __init__(self, parent, by_sender):
        super().__init__(parent, title="Missed Messages", size=(350, 400))
        self._by_sender = by_sender
        self.Bind(wx.EVT_CHAR_HOOK, self.on_key)

        dark_mode_on = is_windows_dark_mode()
        if dark_mode_on:
            dark_color = wx.Colour(40, 40, 40); light_text_color = wx.WHITE
            WxMswDarkMode().enable(self); self.SetBackgroundColour(dark_color)

        s = wx.BoxSizer(wx.VERTICAL)
        lbl = wx.StaticText(self, label="Messages received while you were offline:")
        self.lv = wx.ListCtrl(self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL)
        self.lv.InsertColumn(0, "User", width=200); self.lv.InsertColumn(1, "Messages", width=80)
        self.lv.Bind(wx.EVT_LIST_ITEM_ACTIVATED, self.on_open_chat)
        self.lv.Bind(wx.EVT_LIST_ITEM_SELECTED, lambda e: (e.Skip(), self._update_buttons()))
        self.lv.Bind(wx.EVT_LIST_ITEM_DESELECTED, lambda e: (e.Skip(), self._update_buttons()))
        for sender in sorted(by_sender.keys()):
            idx = self.lv.InsertItem(self.lv.GetItemCount(), sender)
            self.lv.SetItem(idx, 1, str(len(by_sender[sender])))
        self.btn_open = wx.Button(self, label="&Open Chat")
        self.btn_add = wx.Button(self, label="&Add to Contacts")
        self.btn_close = wx.Button(self, label="C&lose")
        self.btn_open.Bind(wx.EVT_BUTTON, self.on_open_chat)
        self.btn_add.Bind(wx.EVT_BUTTON, self.on_add_contact)
        self.btn_close.Bind(wx.EVT_BUTTON, lambda e: self.Close())

        if dark_mode_on:
            for w in [self.lv, self.btn_open, self.btn_add, self.btn_close]:
                w.SetBackgroundColour(dark_color); w.SetForegroundColour(light_text_color)
            lbl.SetForegroundColour(light_text_color)

        btn_sizer = wx.BoxSizer(wx.HORIZONTAL)
        btn_sizer.Add(self.btn_open, 1, wx.EXPAND | wx.ALL, 5)
        btn_sizer.Add(self.btn_add, 1, wx.EXPAND | wx.ALL, 5)
        btn_sizer.Add(self.btn_close, 1, wx.EXPAND | wx.ALL, 5)
        s.Add(lbl, 0, wx.LEFT | wx.TOP | wx.RIGHT, 10)
        s.Add(self.lv, 1, wx.EXPAND | wx.ALL, 5)
        s.Add(btn_sizer, 0, wx.EXPAND | wx.BOTTOM, 5)
        self.SetSizer(s)
        self._update_buttons()

    def _update_buttons(self):
        sel = self.lv.GetFirstSelected()
        has_sel = sel >= 0
        self.btn_open.Enable(has_sel)
        if has_sel:
            sender = self.lv.GetItemText(sel)
            self.btn_add.Enable(sender not in self.GetParent().contact_states)
        else:
            self.btn_add.Enable(False)

    def on_open_chat(self, _):
        sel = self.lv.GetFirstSelected()
        if sel < 0: return
        sender = self.lv.GetItemText(sel); frame = self.GetParent()
        dlg = frame.get_chat(sender)
        if dlg: dlg.Show(); dlg.Raise(); dlg.input_ctrl.SetFocus()

    def on_add_contact(self, _):
        sel = self.lv.GetFirstSelected()
        if sel < 0: return
        self.GetParent().sock.sendall(json.dumps({"action": "add_contact", "to": self.lv.GetItemText(sel)}).encode() + b"\n")

    def on_key(self, event):
        if event.GetKeyCode() == wx.WXK_ESCAPE: self.Close()
        else: event.Skip()

def main():
    app = ClientApp(False); app.MainLoop()

if __name__ == "__main__": main()