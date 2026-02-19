import wx, socket, json, threading, datetime, wx.adv, configparser, ssl, sys, os, base64, uuid, subprocess, tempfile, re, random, shutil
import urllib.request, urllib.parse
import traceback, platform
import keyring
try:
    import wx.html2 as wxhtml2
except Exception:
    wxhtml2 = None

VERSION_TAG = "v2026-alpha15.1"
URL_REGEX = re.compile(r'(https?://[^\s<>()]+)')
KEYRING_SERVICE = "ThriveMessenger"
DEFAULT_SOUNDPACK_BASE_URL = "https://im.tappedin.fm/thrive/sounds"
DEFAULT_LOG_SUBMIT_URL = "https://im.tappedin.fm/thrive/logs"
DEMO_VIDEOS = {
    "onboarding": {
        "filename": "promo-onboarding.mp4",
        "title": "Onboarding Demo",
        "description": "Shows the first-run experience: launching the app, signing in, and landing on the contact list."
    },
    "chat_files": {
        "filename": "promo-chat-files.mp4",
        "title": "Chat and File Demo",
        "description": "Shows selecting a contact, sending a message, and sending files with transfer confirmation."
    },
    "admin_tools": {
        "filename": "promo-admin-tools.mp4",
        "title": "Admin Tools Demo",
        "description": "Shows opening Server Manager, reviewing multiple servers, and updating primary server settings."
    },
}
_KEYRING_WRITE_CACHE = {}
_SOUND_DOWNLOAD_NOTICE_CACHE = set()
_SOUND_DOWNLOAD_FAILURE_CACHE = set()
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
    }

def load_server_entries_from_client_conf():
    config = configparser.ConfigParser(interpolation=None)
    config.read('client.conf')
    entries = []
    if config.has_section('server'):
        entries.append({
            'name': config.get('server', 'name', fallback='Default Server'),
            'host': config.get('server', 'host', fallback='msg.thecubed.cc'),
            'port': config.getint('server', 'port', fallback=2005),
            'cafile': config.get('server', 'cafile', fallback=''),
            'primary': config.getboolean('server', 'primary', fallback=False),
        })
    for section in config.sections():
        if section == 'server':
            continue
        if section.startswith('server ') or section.startswith('server:'):
            entries.append({
                'name': config.get(section, 'name', fallback=section.replace('server', '', 1).strip(' :') or 'Server'),
                'host': config.get(section, 'host', fallback='msg.thecubed.cc'),
                'port': config.getint(section, 'port', fallback=2005),
                'cafile': config.get(section, 'cafile', fallback=''),
                'primary': config.getboolean(section, 'primary', fallback=False),
            })
    return dedupe_server_entries(entries)

def normalize_server_entry(entry):
    host = str(entry.get('host', '')).strip()
    name = str(entry.get('name', '')).strip() or host or 'Server'
    cafile = str(entry.get('cafile', '')).strip()
    try:
        port = int(entry.get('port', 2005))
    except Exception:
        port = 2005
    if port <= 0:
        port = 2005
    primary = bool(entry.get('primary', False))
    return {'name': name, 'host': host, 'port': port, 'cafile': cafile, 'primary': primary}

def dedupe_server_entries(entries):
    out = []
    seen = set()
    for entry in entries:
        normalized = normalize_server_entry(entry)
        if not normalized['host']:
            continue
        key = (normalized['host'].lower(), normalized['port'])
        if key in seen:
            continue
        seen.add(key)
        out.append(normalized)
    if out and not any(e.get('primary') for e in out):
        out[0]['primary'] = True
    return out

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

def _resolve_server_for_credentials(settings):
    entries = dedupe_server_entries(settings.get('server_entries', []))
    if not entries:
        entries = [normalize_server_entry(load_server_config())]
    preferred_name = settings.get('last_server_name') or settings.get('primary_server_name', '')
    for entry in entries:
        if entry.get('name') == preferred_name:
            return normalize_server_entry(entry)
    return normalize_server_entry(entries[0])

def _keyring_account_for(username, settings):
    server = _resolve_server_for_credentials(settings)
    return f"{username}@{server.get('host', '').lower()}:{server.get('port', 2005)}"

def _load_password_from_keyring(username, settings):
    if not username:
        return ''
    account = _keyring_account_for(username, settings)
    # Server-aware key first, then legacy account for backward compatibility.
    candidates = [(KEYRING_SERVICE, account), (KEYRING_SERVICE, username)]
    for service, key_account in candidates:
        try:
            value = keyring.get_password(service, key_account)
            if value:
                return value
        except Exception as e:
            print(f"Keyring error (load): {e}")
            return ''
    return ''

def _encode_password_fallback(password):
    if not password:
        return ''
    try:
        return base64.b64encode(password.encode('utf-8')).decode('ascii')
    except Exception:
        return ''

def _decode_password_fallback(value):
    if not value:
        return ''
    try:
        return base64.b64decode(value.encode('ascii')).decode('utf-8')
    except Exception:
        return ''

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
    file_entries = load_server_entries_from_client_conf()
    fallback_entry = normalize_server_entry(load_server_config())
    if not file_entries:
        file_entries = [fallback_entry]

    settings = {
        'remember': False,
        'autologin': False,
        'username': '',
        'password': '',
        'soundpack': 'default',
        'default_soundpack': 'default',
        'soundpack_base_url': DEFAULT_SOUNDPACK_BASE_URL,
        'log_submit_url': DEFAULT_LOG_SUBMIT_URL,
        'sound_volume': 80,
        'call_input_volume': 80,
        'call_output_volume': 80,
        'show_main_action_buttons': True,
        'chat_logging': {},
        'server_entries': file_entries,
        'last_server_name': file_entries[0]['name'] if file_entries else 'Default Server',
        'primary_server_name': next((e['name'] for e in file_entries if e.get('primary')), file_entries[0]['name'] if file_entries else 'Default Server'),
        'auto_open_received_files': True,
        'read_messages_aloud': False,
        'typing_indicators': True,
        'announce_typing': True,
        'enter_key_action': 'send',
        'escape_main_action': 'none',
        'save_chat_history_default': False
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

    # 2. Normalize and merge server entries from both user settings and client.conf
    user_entries = settings.get('server_entries', []) if isinstance(settings.get('server_entries', []), list) else []
    merged_entries = dedupe_server_entries(file_entries + user_entries)
    if not merged_entries:
        merged_entries = [fallback_entry]
    settings['server_entries'] = merged_entries
    if settings.get('primary_server_name') not in [e['name'] for e in merged_entries]:
        primary = next((e['name'] for e in merged_entries if e.get('primary')), merged_entries[0]['name'])
        settings['primary_server_name'] = primary
    if settings.get('last_server_name') not in [e['name'] for e in merged_entries]:
        settings['last_server_name'] = settings.get('primary_server_name') or merged_entries[0]['name']

    # 3. Load password from Keyring if "Remember me" is active
    if settings.get('username') and settings.get('remember'):
        stored_pass = _load_password_from_keyring(settings['username'], settings)
        if stored_pass:
            settings['password'] = stored_pass
        elif settings.get('password_fallback'):
            settings['password'] = _decode_password_fallback(settings.get('password_fallback', ''))
            
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
    if remember and password:
        data_to_save['password_fallback'] = _encode_password_fallback(password)
    else:
        data_to_save.pop('password_fallback', None)

    try:
        with open(get_settings_path(), 'w') as f:
            json.dump(data_to_save, f, indent=4)
    except Exception as e:
        print(f"Error saving settings file: {e}")

    # 2. Manage Keyring
    if username:
        account = _keyring_account_for(username, settings)
        if remember and password:
            cache_key = (KEYRING_SERVICE, account)
            if _KEYRING_WRITE_CACHE.get(cache_key) != password:
                try:
                    keyring.set_password(KEYRING_SERVICE, account, password)
                    _KEYRING_WRITE_CACHE[cache_key] = password
                except Exception as e:
                    print(f"Keyring error (save): {e}")
        else:
            # If remember is False, ensure we remove the credential from the OS manager
            try:
                if keyring.get_password(KEYRING_SERVICE, account):
                    keyring.delete_password(KEYRING_SERVICE, account)
                    _KEYRING_WRITE_CACHE.pop((KEYRING_SERVICE, account), None)
            except Exception:
                # Password might not exist, ignore
                pass

SERVER_CONFIG = load_server_config()
ADDR = (SERVER_CONFIG['host'], SERVER_CONFIG['port'])
_IPC_PORT = 48951
_LOG_FILE_NAME = "thrive_client.log"

def get_logs_dir():
    p = os.path.join(os.path.dirname(get_settings_path()), "logs")
    os.makedirs(p, exist_ok=True)
    return p

def get_log_path():
    return os.path.join(get_logs_dir(), _LOG_FILE_NAME)

def _trim_log_file(path, max_bytes=2 * 1024 * 1024):
    try:
        if os.path.getsize(path) <= max_bytes:
            return
        with open(path, "rb") as f:
            f.seek(-max_bytes, os.SEEK_END)
            tail = f.read()
        with open(path, "wb") as f:
            f.write(tail)
    except Exception:
        pass

def log_event(level, message, extra=None):
    try:
        payload = {
            "ts": datetime.datetime.utcnow().isoformat() + "Z",
            "level": str(level).lower(),
            "message": str(message),
        }
        if extra is not None:
            payload["extra"] = extra
        with open(get_log_path(), "a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=True) + "\n")
        _trim_log_file(get_log_path())
    except Exception:
        pass

def _read_log_tail(path, max_bytes=256 * 1024):
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            if size > max_bytes:
                f.seek(-max_bytes, os.SEEK_END)
            data = f.read()
        return data.decode("utf-8", errors="replace")
    except Exception:
        return ""

def submit_logs_payload(config_dict, reason="manual"):
    base_url = str(config_dict.get("log_submit_url", DEFAULT_LOG_SUBMIT_URL) or "").strip().rstrip("/")
    if not base_url:
        return False, "Log submit URL is not configured."
    payload = {
        "app": "Thrive Messenger",
        "version": VERSION_TAG,
        "reason": reason,
        "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
        "username": str(config_dict.get("username", "") or ""),
        "server": normalize_server_entry(config_dict.get("server_entries", [{}])[0] if config_dict.get("server_entries") else SERVER_CONFIG).get("name", "unknown"),
        "platform": platform.platform(),
        "log_tail": _read_log_tail(get_log_path()),
    }
    body = json.dumps(payload).encode("utf-8")
    file_name = f"log-{datetime.datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}.json"
    url = f"{base_url}/{urllib.parse.quote(file_name)}"
    req = urllib.request.Request(
        url,
        data=body,
        method="PUT",
        headers={
            "Content-Type": "application/json",
            "User-Agent": f"ThriveMessenger/{VERSION_TAG}",
            "X-Thrive-Client": "desktop",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            code = int(getattr(resp, "status", 200))
        if 200 <= code < 300:
            return True, None
        return False, f"Server returned status {code}"
    except Exception as e:
        return False, str(e)

def prompt_submit_logs(parent, config_dict, reason, intro="A diagnostic report can be submitted to help troubleshoot this issue. Submit now?"):
    res = wx.MessageBox(intro, "Submit Diagnostics", wx.YES_NO | wx.ICON_QUESTION, parent)
    if res != wx.YES:
        return False
    ok, err = submit_logs_payload(config_dict, reason=reason)
    if ok:
        show_notification("Diagnostics", "Logs submitted successfully.", timeout=4)
        wx.MessageBox("Diagnostic logs submitted successfully.", "Logs Submitted", wx.OK | wx.ICON_INFORMATION, parent)
        log_event("info", "logs_submitted_prompted", {"reason": reason})
        return True
    wx.MessageBox(f"Could not submit logs:\n{err}", "Log Submit Failed", wx.OK | wx.ICON_ERROR, parent)
    log_event("error", "logs_submit_failed", {"reason": reason, "error": str(err)})
    return False

def set_active_server_config(server_entry):
    global SERVER_CONFIG, ADDR
    normalized = normalize_server_entry(server_entry)
    SERVER_CONFIG = {
        'host': normalized['host'],
        'port': normalized['port'],
        'cafile': normalized['cafile'] or None,
    }
    ADDR = (SERVER_CONFIG['host'], SERVER_CONFIG['port'])

def resolve_default_server_entry(user_config):
    entries = dedupe_server_entries(user_config.get('server_entries', []))
    if not entries:
        entries = [normalize_server_entry(load_server_config())]
    preferred_name = user_config.get('primary_server_name') or user_config.get('last_server_name', '')
    for entry in entries:
        if entry['name'] == preferred_name:
            return entry
    return entries[0]

def fetch_server_welcome(server_entry):
    try:
        ssock = create_secure_socket(server_entry)
        ssock.sendall((json.dumps({"action": "get_welcome"}) + "\n").encode())
        line = ssock.makefile().readline()
        ssock.close()
        payload = json.loads(line or "{}")
        if payload.get("action") == "welcome_info":
            return payload
    except Exception:
        pass
    return {"enabled": False, "pre_login": "", "post_login": ""}

def _format_uptime(value):
    if value is None:
        return "Unknown"
    try:
        if isinstance(value, (int, float)):
            total = int(value)
        else:
            text = str(value).strip()
            if text.isdigit():
                total = int(text)
            else:
                return text or "Unknown"
        days, rem = divmod(total, 86400)
        hours, rem = divmod(rem, 3600)
        mins, secs = divmod(rem, 60)
        parts = []
        if days:
            parts.append(f"{days}d")
        if hours or days:
            parts.append(f"{hours}h")
        if mins or hours or days:
            parts.append(f"{mins}m")
        parts.append(f"{secs}s")
        return " ".join(parts)
    except Exception:
        return "Unknown"

def fetch_server_snapshot(server_entry):
    snapshot = {
        "status": "Unreachable",
        "online_users": "Unknown",
        "online_admin_users": "Unknown",
        "total_users": "Unknown",
        "uptime": "Unknown",
    }
    try:
        ssock = create_secure_socket(server_entry)
        ssock.sendall((json.dumps({"action": "server_info"}) + "\n").encode())
        line = ssock.makefile().readline()
        ssock.close()
        payload = json.loads(line or "{}")
        if payload.get("action") == "server_info_response":
            snapshot["status"] = "Online"
            snapshot["online_users"] = str(payload.get("online_users", "Unknown"))
            snapshot["online_admin_users"] = str(payload.get("online_admin_users", "Unknown"))
            snapshot["total_users"] = str(payload.get("total_users", "Unknown"))
            uptime_raw = (
                payload.get("uptime")
                or payload.get("server_uptime")
                or payload.get("uptime_seconds")
            )
            snapshot["uptime"] = _format_uptime(uptime_raw)
            return snapshot
    except Exception:
        pass
    return snapshot

def extract_urls(text):
    if not text:
        return []
    return URL_REGEX.findall(text)

def open_path_or_url(target):
    try:
        if sys.platform == 'win32':
            os.startfile(target)
        elif sys.platform == 'darwin':
            subprocess.Popen(['open', target])
        else:
            subprocess.Popen(['xdg-open', target])
        return True
    except Exception as e:
        print(f"Could not open target '{target}': {e}")
        return False

def get_help_doc_path():
    candidates = [
        os.path.join(get_program_dir(), "F1_HELP.md"),
        os.path.join(get_program_dir(), "README.md"),
    ]
    for candidate in candidates:
        if os.path.exists(candidate):
            return candidate
    return ""

def open_help_docs():
    open_help_docs_for_context("general", None)

def _help_docs_dir():
    path = os.path.join(get_config_dir(), "help_docs")
    os.makedirs(path, exist_ok=True)
    return path

def ensure_help_docs():
    docs = {
        "general": "<h1>Thrive Messenger Help</h1><p>Press F1 in each window for contextual help. Press Escape or Command+W to close this help window and return.</p>",
        "login": "<h1>Login Help</h1><p>Use Server dropdown to pick a server. Use Manage Servers to add/edit endpoints. Use Set as Primary to choose your default server. Then enter username and password and sign in.</p>",
        "main": "<h1>Contacts Window Help</h1><p>Manage contacts, statuses, files, and chats. Default action is Start Chat for the focused contact. User actions are available from User and context menus. File Transfers window shows sent/received files and their saved locations.</p>",
        "chat": "<h1>Chat Window Help</h1><p>Enter sends message, Ctrl+Enter sends file, and Cmd+Enter inserts a new line. Message history is keyboard navigable and links can be activated from selected items. Typing indicators and readout can be toggled in Settings.</p>",
        "directory": "<h1>User Directory Help</h1><p>Shows users from current and configured servers with server labels. Use Sort and Filter options for contacts. If a selected server does not support a feature, the related action is dimmed and explains why.</p>",
        "admin": "<h1>Admin Commands Help</h1><p>Commands start with '/'. Example: /alert message, /create username password, /admin username.</p>",
        "settings": "<h1>Settings Help</h1><p>Configure sound pack, default sound pack selection, sound volume, call input/output levels, and chat accessibility options. Settings are remembered by the app.</p>",
        "server_info": "<h1>Server Info Help</h1><p>Shows active server host, port, encryption state, user counts, and file policy limits.</p>",
    }
    out = {}
    for key, html in docs.items():
        path = os.path.join(_help_docs_dir(), f"{key}.html")
        with open(path, "w", encoding="utf-8") as f:
            f.write(f"<!doctype html><html><head><meta charset='utf-8'><title>Help</title></head><body>{html}</body></html>")
        out[key] = path
    return out

def speak_text(text):
    try:
        if not text:
            return
        if sys.platform == 'darwin':
            subprocess.Popen(['say', text])
        elif sys.platform == 'win32':
            safe_text = text.replace("'", "''")
            cmd = "Add-Type -AssemblyName System.Speech; $s=New-Object System.Speech.Synthesis.SpeechSynthesizer; $s.Speak('{0}')".format(safe_text)
            subprocess.Popen(["powershell", "-NoProfile", "-Command", cmd], creationflags=0x08000000)
    except Exception as e:
        print(f"TTS speak failed: {e}")

def open_help_docs_for_context(context, parent):
    docs = ensure_help_docs()
    target = docs.get(context) or docs.get("general")
    if wxhtml2 is not None:
        try:
            dlg = wx.Dialog(parent, title="Help", size=(760, 560))
            def _on_key(event):
                key = event.GetKeyCode()
                if key == wx.WXK_ESCAPE or (event.CmdDown() and key == ord('W')):
                    dlg.EndModal(wx.ID_OK)
                    return
                event.Skip()
            dlg.Bind(wx.EVT_CHAR_HOOK, _on_key)
            web = wxhtml2.WebView.New(dlg)
            web.LoadURL("file://" + target)
            s = wx.BoxSizer(wx.VERTICAL)
            s.Add(web, 1, wx.EXPAND | wx.ALL, 0)
            dlg.SetSizer(s)
            dlg.ShowModal()
            dlg.Destroy()
            return
        except Exception as e:
            print(f"WebView help fallback: {e}")
    open_path_or_url(target)

def parse_github_tag(tag):
    m = re.match(r'^v(\d{4})-alpha(\d+)(?:\.(\d+))?$', tag)
    if not m: return None
    return (int(m.group(1)) - 2000, 0, int(m.group(2)), int(m.group(3)) if m.group(3) else 0)

def get_program_dir():
    if getattr(sys, 'frozen', False): return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))

def get_bundle_resources_dir():
    if not getattr(sys, 'frozen', False):
        return None
    exe_dir = os.path.dirname(sys.executable)
    if sys.platform == 'darwin':
        # PyInstaller macOS app bundle: Contents/MacOS/<binary> and Contents/Resources/<assets>.
        resources = os.path.abspath(os.path.join(exe_dir, '..', 'Resources'))
        if os.path.isdir(resources):
            return resources
    # Windows one-folder build typically places assets next to the executable.
    return exe_dir

def is_installer_install():
    return os.path.exists(os.path.join(get_program_dir(), 'unins000.exe'))

def get_sounds_dir():
    resources_dir = get_bundle_resources_dir()
    if resources_dir:
        bundled = os.path.join(resources_dir, 'sounds')
        if os.path.isdir(bundled):
            return bundled
    local_sounds = os.path.join(get_program_dir(), 'sounds')
    if os.path.isdir(local_sounds):
        return local_sounds
    return os.path.join(os.getcwd(), 'sounds')

def get_downloaded_sounds_dir():
    sounds_dir = os.path.join(os.path.dirname(get_settings_path()), 'sounds')
    os.makedirs(sounds_dir, exist_ok=True)
    return sounds_dir

def get_demo_videos_dir():
    resources_dir = get_bundle_resources_dir()
    if resources_dir:
        bundled = os.path.join(resources_dir, 'assets', 'videos')
        if os.path.isdir(bundled):
            return bundled
    local_videos = os.path.join(get_program_dir(), 'assets', 'videos')
    if os.path.isdir(local_videos):
        return local_videos
    fallback = os.path.join(os.getcwd(), 'assets', 'videos')
    os.makedirs(fallback, exist_ok=True)
    return fallback

def get_soundpack_base_url(config_dict):
    base = str(config_dict.get('soundpack_base_url', DEFAULT_SOUNDPACK_BASE_URL) or DEFAULT_SOUNDPACK_BASE_URL).strip()
    return base.rstrip('/')

def get_sound_fetch_headers():
    return {
        "User-Agent": f"ThriveMessenger/{VERSION_TAG}",
        "X-Thrive-Client": "desktop",
        "Accept": "application/json, audio/wav, application/octet-stream, */*",
    }

def _safe_sound_name(name):
    return bool(re.match(r'^[A-Za-z0-9._ -]+$', str(name or '')))

def get_remote_sound_manifest(config_dict):
    base = get_soundpack_base_url(config_dict)
    url = f"{base}/index.json"
    req = urllib.request.Request(url, headers=get_sound_fetch_headers())
    try:
        with urllib.request.urlopen(req, timeout=4) as resp:
            data = json.loads(resp.read().decode('utf-8', errors='replace'))
        packs = data.get('packs', {})
        return packs if isinstance(packs, dict) else {}
    except Exception:
        # Fallback to autoindex parsing so newly added packs/files appear without manifest updates.
        result = {}
        try:
            root_req = urllib.request.Request(f"{base}/", headers=get_sound_fetch_headers())
            with urllib.request.urlopen(root_req, timeout=5) as resp:
                listing = resp.read().decode('utf-8', errors='replace')
            pack_names = []
            for href in re.findall(r'href="([^"]+)"', listing):
                if href in ('../', '/'):
                    continue
                name = href.strip('/').strip()
                if name and _safe_sound_name(name):
                    pack_names.append(name)
            for pack in sorted(set(pack_names)):
                try:
                    pack_req = urllib.request.Request(f"{base}/{urllib.parse.quote(pack)}/", headers=get_sound_fetch_headers())
                    with urllib.request.urlopen(pack_req, timeout=5) as presp:
                        p_listing = presp.read().decode('utf-8', errors='replace')
                    wavs = []
                    for href in re.findall(r'href="([^"]+)"', p_listing):
                        candidate = href.split('?', 1)[0].strip()
                        if candidate.lower().endswith('.wav'):
                            fname = os.path.basename(candidate)
                            if _safe_sound_name(fname):
                                wavs.append(fname)
                    if wavs:
                        result[pack] = sorted(set(wavs))
                except Exception:
                    continue
        except Exception:
            return {}
        return result

def list_available_sound_packs(config_dict):
    packs = {'none', 'default'}
    for root in (get_sounds_dir(), get_downloaded_sounds_dir()):
        if not os.path.isdir(root):
            continue
        try:
            for d in os.listdir(root):
                if os.path.isdir(os.path.join(root, d)):
                    packs.add(d)
        except Exception:
            pass
    for pack_name in get_remote_sound_manifest(config_dict).keys():
        if _safe_sound_name(pack_name):
            packs.add(pack_name)
    return sorted(packs)

def find_local_sound_path(pack, sound_file):
    for root in (get_sounds_dir(), get_downloaded_sounds_dir()):
        p = os.path.join(root, pack, sound_file)
        if os.path.exists(p):
            return p
    return None

def download_sound_file_if_missing(config_dict, pack, sound_file):
    if not (_safe_sound_name(pack) and _safe_sound_name(sound_file)):
        return None
    existing = find_local_sound_path(pack, sound_file)
    if existing:
        return existing
    base = get_soundpack_base_url(config_dict)
    url = f"{base}/{urllib.parse.quote(pack)}/{urllib.parse.quote(sound_file)}"
    req = urllib.request.Request(url, headers=get_sound_fetch_headers())
    key = f"{pack}/{sound_file}"
    if key not in _SOUND_DOWNLOAD_NOTICE_CACHE:
        _SOUND_DOWNLOAD_NOTICE_CACHE.add(key)
        show_notification("Sound pack", f"Downloading sound: {pack}/{sound_file}", timeout=3)
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            blob = resp.read()
        if not blob:
            return None
        out_dir = os.path.join(get_downloaded_sounds_dir(), pack)
        os.makedirs(out_dir, exist_ok=True)
        out_path = os.path.join(out_dir, sound_file)
        tmp_path = out_path + ".part"
        with open(tmp_path, 'wb') as f:
            f.write(blob)
        os.replace(tmp_path, out_path)
        show_notification("Sound pack", f"Downloaded sound: {pack}/{sound_file}", timeout=3)
        return out_path
    except Exception:
        if key not in _SOUND_DOWNLOAD_FAILURE_CACHE:
            _SOUND_DOWNLOAD_FAILURE_CACHE.add(key)
            show_notification("Sound pack", f"Could not download sound: {pack}/{sound_file}", timeout=4)
        return None

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
        super().__init__(parent, title="Settings", size=(430, 540)); self.config = current_config
        self.Bind(wx.EVT_CHAR_HOOK, self.on_key)
        panel = wx.Panel(self); main_sizer = wx.BoxSizer(wx.VERTICAL); sound_box = wx.StaticBoxSizer(wx.VERTICAL, panel, "&Sound Pack")
        call_audio_box = wx.StaticBoxSizer(wx.VERTICAL, panel, "Call Audio Levels")
        accessibility_box = wx.StaticBoxSizer(wx.VERTICAL, panel, "&Chat Accessibility")
        
        dark_mode_on = is_windows_dark_mode()
        if dark_mode_on:
            dark_color = wx.Colour(40, 40, 40); light_text_color = wx.WHITE
            WxMswDarkMode().enable(self); self.SetBackgroundColour(dark_color); panel.SetBackgroundColour(dark_color)
            sound_box.GetStaticBox().SetForegroundColour(light_text_color)
            sound_box.GetStaticBox().SetBackgroundColour(dark_color)
            call_audio_box.GetStaticBox().SetForegroundColour(light_text_color)
            call_audio_box.GetStaticBox().SetBackgroundColour(dark_color)
            accessibility_box.GetStaticBox().SetForegroundColour(light_text_color)
            accessibility_box.GetStaticBox().SetBackgroundColour(dark_color)
        
        sound_packs = list_available_sound_packs(self.config)
        self.choice = wx.Choice(sound_box.GetStaticBox(), choices=sound_packs)
        current_pack = self.config.get('soundpack', 'default')
        if not current_pack:
            current_pack = 'none'
        if current_pack in sound_packs:
            self.choice.SetStringSelection(current_pack)
        else:
            self.choice.SetStringSelection('default')
        self.default_soundpack_label = wx.StaticText(sound_box.GetStaticBox(), label=f"Current default pack: {self.config.get('default_soundpack', 'default')}")
        self.set_selected_default_cb = wx.CheckBox(sound_box.GetStaticBox(), label="Set selected pack as default sound pack")
        self.choice.Bind(wx.EVT_CHOICE, self.on_sound_pack_changed)
        self.sound_volume_label = wx.StaticText(sound_box.GetStaticBox(), label="Sound pack volume")
        self.sound_volume_slider = wx.Slider(sound_box.GetStaticBox(), value=int(self.config.get('sound_volume', 80)), minValue=0, maxValue=100, style=wx.SL_HORIZONTAL | wx.SL_LABELS)

        self.call_in_label = wx.StaticText(call_audio_box.GetStaticBox(), label="Call input volume")
        self.call_input_slider = wx.Slider(call_audio_box.GetStaticBox(), value=int(self.config.get('call_input_volume', 80)), minValue=0, maxValue=100, style=wx.SL_HORIZONTAL | wx.SL_LABELS)
        self.call_out_label = wx.StaticText(call_audio_box.GetStaticBox(), label="Call output volume")
        self.call_output_slider = wx.Slider(call_audio_box.GetStaticBox(), value=int(self.config.get('call_output_volume', 80)), minValue=0, maxValue=100, style=wx.SL_HORIZONTAL | wx.SL_LABELS)
        self.auto_open_files_cb = wx.CheckBox(accessibility_box.GetStaticBox(), label="Auto-open received files after save")
        self.auto_open_files_cb.SetValue(bool(self.config.get('auto_open_received_files', True)))
        self.read_aloud_cb = wx.CheckBox(accessibility_box.GetStaticBox(), label="Read incoming chat messages aloud")
        self.read_aloud_cb.SetValue(bool(self.config.get('read_messages_aloud', False)))
        self.global_chat_logging_cb = wx.CheckBox(accessibility_box.GetStaticBox(), label="Save chat history by default")
        self.global_chat_logging_cb.SetValue(bool(self.config.get('save_chat_history_default', False)))
        self.show_main_actions_cb = wx.CheckBox(accessibility_box.GetStaticBox(), label="Show action buttons in main window")
        self.show_main_actions_cb.SetValue(bool(self.config.get('show_main_action_buttons', True)))
        self.typing_indicator_cb = wx.CheckBox(accessibility_box.GetStaticBox(), label="Show typing indicators")
        self.typing_indicator_cb.SetValue(bool(self.config.get('typing_indicators', True)))
        self.announce_typing_cb = wx.CheckBox(accessibility_box.GetStaticBox(), label="Announce typing start/stop")
        self.announce_typing_cb.SetValue(bool(self.config.get('announce_typing', True)))
        enter_row = wx.BoxSizer(wx.HORIZONTAL)
        enter_row.Add(wx.StaticText(accessibility_box.GetStaticBox(), label="Enter key action:"), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 6)
        self.enter_action_choice = wx.Choice(accessibility_box.GetStaticBox(), choices=[
            "Send message on Enter (default)",
            "Insert newline on Enter",
        ])
        self.enter_action_choice.SetSelection(0 if self.config.get('enter_key_action', 'send') == 'send' else 1)
        enter_row.Add(self.enter_action_choice, 1, wx.EXPAND)
        escape_row = wx.BoxSizer(wx.HORIZONTAL)
        escape_row.Add(wx.StaticText(accessibility_box.GetStaticBox(), label="Escape in main window:"), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 6)
        self.escape_action_choice = wx.Choice(accessibility_box.GetStaticBox(), choices=[
            "Do nothing (recommended)",
            "Minimize to tray/status menu",
            "Quit app",
        ])
        esc_val = str(self.config.get('escape_main_action', 'none') or 'none')
        self.escape_action_choice.SetSelection(0 if esc_val == 'none' else (1 if esc_val == 'minimize' else 2))
        escape_row.Add(self.escape_action_choice, 1, wx.EXPAND)
        sound_box.Add(self.choice, 0, wx.EXPAND | wx.ALL, 5)
        sound_box.Add(self.default_soundpack_label, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 5)
        sound_box.Add(self.set_selected_default_cb, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 5)
        sound_box.Add(self.sound_volume_label, 0, wx.LEFT | wx.RIGHT | wx.TOP, 5)
        sound_box.Add(self.sound_volume_slider, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 5)
        call_audio_box.Add(self.call_in_label, 0, wx.LEFT | wx.RIGHT | wx.TOP, 5)
        call_audio_box.Add(self.call_input_slider, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 5)
        call_audio_box.Add(self.call_out_label, 0, wx.LEFT | wx.RIGHT | wx.TOP, 5)
        call_audio_box.Add(self.call_output_slider, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 5)
        accessibility_box.Add(self.auto_open_files_cb, 0, wx.ALL, 5)
        accessibility_box.Add(self.read_aloud_cb, 0, wx.ALL, 5)
        accessibility_box.Add(self.global_chat_logging_cb, 0, wx.ALL, 5)
        accessibility_box.Add(self.show_main_actions_cb, 0, wx.ALL, 5)
        accessibility_box.Add(self.typing_indicator_cb, 0, wx.ALL, 5)
        accessibility_box.Add(self.announce_typing_cb, 0, wx.ALL, 5)
        accessibility_box.Add(enter_row, 0, wx.EXPAND | wx.ALL, 5)
        accessibility_box.Add(escape_row, 0, wx.EXPAND | wx.ALL, 5)
        main_sizer.Add(sound_box, 0, wx.EXPAND | wx.ALL, 5)
        main_sizer.Add(call_audio_box, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 5)
        main_sizer.Add(accessibility_box, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 5)
        self.btn_chpass = wx.Button(panel, label="C&hange Password...")
        self.btn_chpass.Bind(wx.EVT_BUTTON, self.on_change_password)
        main_sizer.Add(self.btn_chpass, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)
        btn_sizer = wx.StdDialogButtonSizer()
        ok_btn = wx.Button(panel, wx.ID_OK, label="&Apply"); ok_btn.SetDefault(); cancel_btn = wx.Button(panel, wx.ID_CANCEL)
        
        if dark_mode_on:
            self.choice.SetBackgroundColour(dark_color); self.choice.SetForegroundColour(light_text_color)
            self.default_soundpack_label.SetForegroundColour(light_text_color)
            self.set_selected_default_cb.SetForegroundColour(light_text_color)
            self.sound_volume_label.SetForegroundColour(light_text_color)
            self.call_in_label.SetForegroundColour(light_text_color)
            self.call_out_label.SetForegroundColour(light_text_color)
            for cb in [self.auto_open_files_cb, self.read_aloud_cb, self.global_chat_logging_cb, self.show_main_actions_cb, self.typing_indicator_cb, self.announce_typing_cb]:
                cb.SetForegroundColour(light_text_color)
            self.enter_action_choice.SetBackgroundColour(dark_color); self.enter_action_choice.SetForegroundColour(light_text_color)
            self.escape_action_choice.SetBackgroundColour(dark_color); self.escape_action_choice.SetForegroundColour(light_text_color)
            self.btn_chpass.SetBackgroundColour(dark_color); self.btn_chpass.SetForegroundColour(light_text_color)
            ok_btn.SetBackgroundColour(dark_color); ok_btn.SetForegroundColour(light_text_color)
            cancel_btn.SetBackgroundColour(dark_color); cancel_btn.SetForegroundColour(light_text_color)
            
        btn_sizer.AddButton(ok_btn); btn_sizer.AddButton(cancel_btn); btn_sizer.Realize(); main_sizer.Add(btn_sizer, 0, wx.ALIGN_CENTER | wx.ALL, 10); panel.SetSizer(main_sizer)
        self.on_sound_pack_changed(None)
    def on_sound_pack_changed(self, _):
        selected = self.choice.GetStringSelection().strip().lower()
        self.set_selected_default_cb.Enable(selected not in ("none", "default"))
    def on_key(self, event):
        if event.GetKeyCode() == wx.WXK_F1:
            open_help_docs_for_context("settings", self)
            return
        event.Skip()
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

def create_secure_socket(server_entry=None):
    active = SERVER_CONFIG if server_entry is None else {
        'host': normalize_server_entry(server_entry)['host'],
        'port': normalize_server_entry(server_entry)['port'],
        'cafile': normalize_server_entry(server_entry)['cafile'] or None,
    }
    addr = (active['host'], active['port'])
    sock = socket.create_connection(addr)
    if active['cafile'] and os.path.exists(active['cafile']):
        context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH, cafile=active['cafile'])
    else: context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
    try: return context.wrap_socket(sock, server_hostname=active['host'])
    except ssl.SSLCertVerificationError:
        sock.close(); sock = socket.create_connection(addr)
        context = ssl.create_default_context(); context.check_hostname = False; context.verify_mode = ssl.CERT_NONE
        return context.wrap_socket(sock, server_hostname=active['host'])
    except (ssl.SSLError, OSError):
        sock.close(); return socket.create_connection(addr)

class ClientApp(wx.App):
    def _signal_existing_instance(self):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(1.0)
            s.connect(('127.0.0.1', _IPC_PORT))
            s.sendall(b'restore')
            s.close()
            return True
        except Exception:
            return False

    def _activate_existing_app(self):
        if sys.platform != 'darwin':
            return False
        try:
            p = subprocess.run(
                ["osascript", "-e", 'tell application "Thrive Messenger" to activate'],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            return p.returncode == 0
        except Exception:
            return False

    def OnInit(self):
        log_event("info", "app_start")
        self.instance_checker = wx.SingleInstanceChecker("ThriveMessenger-%s" % wx.GetUserId())
        if self.instance_checker.IsAnotherRunning():
            if self._signal_existing_instance() or self._activate_existing_app():
                return False
            # Stale lock or crashed/background state: continue startup to recover.
            print("Detected stale single-instance state; launching a fresh visible window.")
            log_event("warn", "stale_single_instance_lock_recovered")
        try:
            self._ipc_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._ipc_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._ipc_sock.bind(('127.0.0.1', _IPC_PORT))
            self._ipc_sock.listen(1)
            threading.Thread(target=self._ipc_listener, daemon=True).start()
        except Exception:
            # If IPC binding fails, first try to restore an existing instance.
            # If restore fails, continue startup without IPC to avoid being stuck unable to open.
            self._ipc_sock = None
            if self._signal_existing_instance() or self._activate_existing_app():
                return False
            print("IPC port unavailable and no active instance responded; continuing without IPC listener.")
            log_event("warn", "ipc_bind_unavailable_continuing")
        self.user_config = load_user_config()
        self.session_password = ""
        self.active_server_entry = resolve_default_server_entry(self.user_config)
        self.connected_server_names = set()
        self.transfer_history = []
        if self.user_config.get('autologin') and self.user_config.get('username') and self.user_config.get('password'):
            print("Attempting auto-login...")
            selected_server = resolve_default_server_entry(self.user_config)
            success, sock, sf, reason = self.perform_login(self.user_config['username'], self.user_config['password'], selected_server)
            if success: self.start_main_session(self.user_config['username'], sock, sf); return True
            else:
                wx.MessageBox(f"Auto-login failed: {reason}", "Login Failed", wx.ICON_ERROR)
                log_event("error", "auto_login_failed", {"reason": str(reason)})
                # Keep autologin enabled for transient network/server issues.
                if "invalid credentials" in str(reason).lower():
                    self.user_config['autologin'] = False
                save_user_config(self.user_config)
        return self.show_login_dialog()

    def add_transfer_history(self, direction, user, filename, path="", status="ok"):
        self.transfer_history.append({
            "time": datetime.datetime.now().isoformat(),
            "direction": direction,
            "user": user,
            "filename": filename,
            "path": path,
            "status": status,
        })
    
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
                success, sock, sf, _ = self.perform_login(dlg.username, dlg.password, dlg.selected_server)
                if success:
                    self.user_config['server_entries'] = dlg.server_entries
                    self.user_config['last_server_name'] = dlg.selected_server.get('name', '')
                    self.user_config['primary_server_name'] = dlg.primary_server_name
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
                success, sock, sf, _ = self.perform_login(dlg.new_username, dlg.new_password, dlg.selected_server)
                if success:
                    self.user_config = {
                        'username': dlg.new_username,
                        'password': dlg.new_password,
                        'remember': True,
                        'autologin': True,
                        'soundpack': 'default',
                        'chat_logging': {},
                        'server_entries': dlg.server_entries,
                        'last_server_name': dlg.selected_server.get('name', ''),
                        'primary_server_name': dlg.primary_server_name
                    }
                    save_user_config(self.user_config); self.start_main_session(dlg.new_username, sock, sf); return True
            else: return False
    
    def perform_login(self, username, password, server_entry=None):
        try:
            if server_entry:
                set_active_server_config(server_entry)
            ssock = create_secure_socket(server_entry)
            ssock.sendall(json.dumps({"action":"login","user":username,"pass":password}).encode()+b"\n")
            sf = ssock.makefile()
            resp = json.loads(sf.readline() or "{}")
            if resp.get("status") == "ok":
                self.session_password = password
                self.active_server_entry = normalize_server_entry(server_entry or SERVER_CONFIG)
                info = fetch_server_welcome(server_entry or SERVER_CONFIG)
                post_login = str(info.get('post_login', '') or '').strip()
                if info.get('enabled') and post_login:
                    show_notification("Server Message", post_login, timeout=8)
                return True, ssock, sf, "Success"
            else:
                reason = resp.get("reason", "Unknown error"); log_event("error", "login_failed", {"reason": reason}); wx.MessageBox("Login failed: " + reason, "Login Failed", wx.ICON_ERROR); ssock.close(); return False, None, None, reason
        except Exception as e:
            log_event("error", "login_connection_error", {"error": str(e)})
            wx.MessageBox(f"A connection error occurred: {e}", "Connection Error", wx.ICON_ERROR)
            try:
                prompt_submit_logs(None, self.user_config, reason="login_connection_error")
            except Exception:
                pass
            return False, None, None, str(e)

    def fetch_directory_for_server(self, server_entry, username, password):
        try:
            ssock = create_secure_socket(server_entry)
            ssock.sendall(json.dumps({"action":"login","user":username,"pass":password}).encode()+b"\n")
            sf = ssock.makefile()
            resp = json.loads(sf.readline() or "{}")
            if resp.get("status") != "ok":
                ssock.close()
                return []
            ssock.sendall((json.dumps({"action": "user_directory"}) + "\n").encode())
            directory_resp = json.loads(sf.readline() or "{}")
            try:
                ssock.sendall(json.dumps({"action":"logout"}).encode()+b"\n")
            except Exception:
                pass
            ssock.close()
            if directory_resp.get("action") != "user_directory_response":
                return []
            users = directory_resp.get("users", [])
            tag = normalize_server_entry(server_entry).get("name", "Server")
            for u in users:
                if "server" not in u:
                    u["server"] = tag
            return users
        except Exception as e:
            print(f"Directory fetch failed for {server_entry}: {e}")
            return []
    
    def start_main_session(self, username, sock, sf):
        self.username = username; self.sock = sock; self.sockfile = sf; self.pending_file_paths = {}
        self.intentional_disconnect = False
        active = normalize_server_entry(getattr(self, "active_server_entry", SERVER_CONFIG))
        self.connected_server_names = {active.get("name") or active.get("host") or "Server"}
        self.frame = MainFrame(self.username, self.sock); self.frame.Show()
        if self.frame.current_status != "online":
            try: self.sock.sendall((json.dumps({"action": "set_status", "status_text": self.frame.current_status}) + "\n").encode())
            except Exception: pass
        wx.CallLater(250, self.play_startup_sound)
        threading.Thread(target=self.listen_loop, daemon=True).start()
        self.frame.on_check_updates(silent=True)

    def _resolved_sound_pack(self):
        selected = str(self.user_config.get('soundpack', 'default') or 'default').strip().lower()
        if selected == 'none':
            return 'none'
        if selected == 'default':
            preferred = str(self.user_config.get('default_soundpack', 'default') or 'default').strip().lower()
            return preferred if preferred and preferred != 'none' else 'default'
        return selected

    def _play_path_with_volume(self, path):
        vol = int(self.user_config.get('sound_volume', 80) or 80)
        vol = max(0, min(100, vol))
        if vol == 0:
            return
        if sys.platform == 'darwin':
            afplay = shutil.which('afplay')
            if afplay:
                # afplay accepts linear gain in 0.0-1.0
                subprocess.Popen([afplay, '-v', f"{vol / 100.0:.2f}", path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return
        wx.adv.Sound.PlaySound(path, wx.adv.SOUND_ASYNC)

    def play_sound(self, sound_file):
        pack = self._resolved_sound_pack()
        if pack == 'none':
            return
        path = find_local_sound_path(pack, sound_file) or download_sound_file_if_missing(self.user_config, pack, sound_file)
        if path and os.path.exists(path):
            self._play_path_with_volume(path)
            return
        default_path = find_local_sound_path('default', sound_file) or download_sound_file_if_missing(self.user_config, 'default', sound_file)
        if default_path and os.path.exists(default_path):
            self._play_path_with_volume(default_path)

    def play_startup_sound(self):
        pack = self._resolved_sound_pack()
        if pack == 'none':
            return
        sounds_root = get_sounds_dir()
        try:
            root_wavs = [os.path.join(sounds_root, n) for n in os.listdir(sounds_root) if n.lower().endswith('.wav')]
        except Exception:
            root_wavs = []
        if root_wavs:
            self._play_path_with_volume(random.choice(root_wavs))
            return
        # Final fallback: play standard login sound from selected/default pack.
        self.play_sound("login.wav")
    
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
                elif act == "add_contact_failed": wx.CallAfter(self.frame.on_add_contact_failed, msg)
                elif act == "add_contact_success": wx.CallAfter(self.frame.on_add_contact_success, msg["contact"])
                elif act == "admin_response": wx.CallAfter(self.frame.on_admin_response, msg["response"])
                elif act == "server_info_response": wx.CallAfter(self.frame.on_server_info_response, msg)
                elif act == "user_directory_response": wx.CallAfter(self.frame.on_user_directory_response, msg)
                elif act == "admin_status_change": wx.CallAfter(self.frame.on_admin_status_change, msg["user"], msg["is_admin"])
                elif act == "server_alert": wx.CallAfter(self.frame.on_server_alert, msg["message"])
                elif act == "typing": wx.CallAfter(self.frame.on_typing_event, msg)
                elif act == "file_offer": wx.CallAfter(self.on_file_offer, msg)
                elif act == "file_offer_failed": wx.CallAfter(self.on_file_offer_failed, msg)
                elif act == "file_accepted": wx.CallAfter(self.on_file_accepted, msg)
                elif act == "file_declined": wx.CallAfter(self.on_file_declined, msg)
                elif act == "file_data": wx.CallAfter(self.on_file_data, msg)
                elif act == "invite_result": wx.CallAfter(self.frame.on_invite_result, msg)
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
        self._return_to_login("Connection to the server was lost.", "Connection Lost")

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
                self.sock.sendall((json.dumps({"action": "file_data", "transfer_id": transfer_id, "to": to, "files": files_data}) + "\n").encode())
                names = [os.path.basename(fp) for fp in file_paths]
                wx.CallAfter(self._on_files_sent, to, names, file_paths)
            except Exception as e:
                wx.CallAfter(self._on_file_send_error, to, e)
        threading.Thread(target=_send, daemon=True).start()

    def _on_files_sent(self, to, filenames, file_paths=None):
        self.play_sound("file_send.wav")
        path_map = {os.path.basename(p): p for p in (file_paths or [])}
        for name in filenames:
            wx.GetApp().add_transfer_history("sent", to, name, path_map.get(name, ""), "sent")
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
        saved_paths = []
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
                saved_paths.append(save_path)
                wx.GetApp().add_transfer_history("received", sender, os.path.basename(save_path), save_path, "received")
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
                show_notification("Files Received", f"{sender} sent you {len(saved)} file(s)")
            if self.user_config.get('auto_open_received_files', True):
                for path in saved_paths:
                    open_path_or_url(path)

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

class ServerManagerDialog(wx.Dialog):
    def __init__(self, parent, server_entries, primary_server_name=""):
        super().__init__(parent, title="Server Manager", size=(520, 360))
        self.entries = [normalize_server_entry(e) for e in server_entries]
        entry_names = [e.get('name', '') for e in self.entries]
        if primary_server_name in entry_names:
            self.primary_server_name = primary_server_name
        else:
            self.primary_server_name = self.entries[0]['name'] if self.entries else ""

        panel = wx.Panel(self)
        s = wx.BoxSizer(wx.VERTICAL)

        self.list = wx.ListBox(panel, style=wx.LB_SINGLE)
        self._refresh_list()
        s.Add(self.list, 1, wx.EXPAND | wx.ALL, 8)

        form = wx.FlexGridSizer(2, 4, 6, 6)
        form.AddGrowableCol(1, 1)
        form.AddGrowableCol(3, 1)
        self.name_txt = wx.TextCtrl(panel)
        self.host_txt = wx.TextCtrl(panel)
        self.port_txt = wx.TextCtrl(panel, value="2005")
        self.cafile_txt = wx.TextCtrl(panel)
        form.Add(wx.StaticText(panel, label="Name"), 0, wx.ALIGN_CENTER_VERTICAL)
        form.Add(self.name_txt, 1, wx.EXPAND)
        form.Add(wx.StaticText(panel, label="Host"), 0, wx.ALIGN_CENTER_VERTICAL)
        form.Add(self.host_txt, 1, wx.EXPAND)
        form.Add(wx.StaticText(panel, label="Port"), 0, wx.ALIGN_CENTER_VERTICAL)
        form.Add(self.port_txt, 1, wx.EXPAND)
        form.Add(wx.StaticText(panel, label="CA file (optional)"), 0, wx.ALIGN_CENTER_VERTICAL)
        form.Add(self.cafile_txt, 1, wx.EXPAND)
        s.Add(form, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)

        btn_row = wx.BoxSizer(wx.HORIZONTAL)
        add_btn = wx.Button(panel, label="Add / Update")
        del_btn = wx.Button(panel, label="Delete")
        primary_btn = wx.Button(panel, label="Set Primary")
        close_btn = wx.Button(panel, wx.ID_OK, label="Done")
        add_btn.Bind(wx.EVT_BUTTON, self.on_add_or_update)
        del_btn.Bind(wx.EVT_BUTTON, self.on_delete)
        primary_btn.Bind(wx.EVT_BUTTON, self.on_set_primary)
        self.list.Bind(wx.EVT_LISTBOX, self.on_select)
        btn_row.Add(add_btn, 0, wx.RIGHT, 6)
        btn_row.Add(del_btn, 0, wx.RIGHT, 6)
        btn_row.Add(primary_btn, 0, wx.RIGHT, 6)
        btn_row.AddStretchSpacer()
        btn_row.Add(close_btn, 0)
        s.Add(btn_row, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)

        panel.SetSizer(s)

    def _refresh_list(self):
        self.list.Clear()
        for entry in self.entries:
            suffix = " [Primary]" if entry.get('name') == self.primary_server_name else ""
            self.list.Append(f"{entry['name']}{suffix}  |  {entry['host']}:{entry['port']}")

    def on_select(self, event):
        index = event.GetSelection()
        if index < 0 or index >= len(self.entries):
            return
        entry = self.entries[index]
        self.name_txt.SetValue(entry['name'])
        self.host_txt.SetValue(entry['host'])
        self.port_txt.SetValue(str(entry['port']))
        self.cafile_txt.SetValue(entry.get('cafile', ''))

    def on_add_or_update(self, _):
        name = self.name_txt.GetValue().strip()
        host = self.host_txt.GetValue().strip()
        port_text = self.port_txt.GetValue().strip() or "2005"
        cafile = self.cafile_txt.GetValue().strip()
        if not name or not host:
            wx.MessageBox("Name and host are required.", "Validation Error", wx.ICON_ERROR)
            return
        try:
            port = int(port_text)
        except Exception:
            wx.MessageBox("Port must be a valid number.", "Validation Error", wx.ICON_ERROR)
            return
        updated = False
        for i, entry in enumerate(self.entries):
            if entry['name'].lower() == name.lower():
                self.entries[i] = {'name': name, 'host': host, 'port': port, 'cafile': cafile}
                updated = True
                break
        if not updated:
            self.entries.append({'name': name, 'host': host, 'port': port, 'cafile': cafile})
        self.entries = dedupe_server_entries(self.entries)
        self._refresh_list()

    def on_delete(self, _):
        idx = self.list.GetSelection()
        if idx == wx.NOT_FOUND:
            return
        if idx < len(self.entries):
            deleted_name = self.entries[idx].get('name', '')
            del self.entries[idx]
            if deleted_name == self.primary_server_name:
                self.primary_server_name = self.entries[0]['name'] if self.entries else ""
            self._refresh_list()

    def on_set_primary(self, _):
        idx = self.list.GetSelection()
        if idx == wx.NOT_FOUND or idx >= len(self.entries):
            return
        self.primary_server_name = self.entries[idx]['name']
        self._refresh_list()

    def get_entries(self):
        return dedupe_server_entries(self.entries)

    def get_primary_server_name(self):
        names = [e.get('name', '') for e in self.entries]
        if self.primary_server_name in names:
            return self.primary_server_name
        return self.entries[0]['name'] if self.entries else ""

class LoginDialog(wx.Dialog):
    def __init__(self, parent, user_config):
        super().__init__(parent, title="Login", size=(390, 470)); self.user_config = user_config
        self.Bind(wx.EVT_CHAR_HOOK, self.on_key)
        panel = wx.Panel(self); s = wx.BoxSizer(wx.VERTICAL)
        self.server_entries = dedupe_server_entries(self.user_config.get('server_entries', []))
        if not self.server_entries:
            self.server_entries = [normalize_server_entry(load_server_config())]
        self.primary_server_name = self.user_config.get('primary_server_name', '')
        if self.primary_server_name not in [e['name'] for e in self.server_entries]:
            self.primary_server_name = next((e['name'] for e in self.server_entries if e.get('primary')), self.server_entries[0]['name'])
        self.selected_server = self.server_entries[0]
        
        dark_mode_on = is_windows_dark_mode()
        if dark_mode_on:
            dark_color = wx.Colour(40, 40, 40); light_text_color = wx.WHITE
            WxMswDarkMode().enable(self); self.SetBackgroundColour(dark_color); panel.SetBackgroundColour(dark_color)
            
        server_box = wx.StaticBoxSizer(wx.VERTICAL, panel, "&Server")
        self.server_choice = wx.Choice(server_box.GetStaticBox(), choices=[])
        self.server_choice.Bind(wx.EVT_CHOICE, self.on_server_choice)
        manage_servers_btn = wx.Button(server_box.GetStaticBox(), label="Manage Servers...")
        manage_servers_btn.Bind(wx.EVT_BUTTON, self.on_manage_servers)
        set_primary_btn = wx.Button(server_box.GetStaticBox(), label="Set as Primary")
        set_primary_btn.Bind(wx.EVT_BUTTON, self.on_set_primary_server)
        server_row = wx.BoxSizer(wx.HORIZONTAL)
        server_row.Add(self.server_choice, 1, wx.EXPAND | wx.RIGHT, 4)
        server_row.Add(manage_servers_btn, 0, wx.EXPAND | wx.RIGHT, 4)
        server_row.Add(set_primary_btn, 0, wx.EXPAND)
        server_box.Add(server_row, 0, wx.EXPAND | wx.ALL, 5)
        self.welcome_preview = wx.StaticText(server_box.GetStaticBox(), label="Welcome: (loading...)")
        self.welcome_preview.Wrap(330)
        server_box.Add(self.welcome_preview, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 5)

        user_box = wx.StaticBoxSizer(wx.VERTICAL, panel, "&Username")
        self.u = wx.TextCtrl(user_box.GetStaticBox()); user_box.Add(self.u, 0, wx.EXPAND | wx.ALL, 5)
        pass_box = wx.StaticBoxSizer(wx.VERTICAL, panel, "&Password"); self.p = wx.TextCtrl(pass_box.GetStaticBox(), style=wx.TE_PASSWORD | wx.TE_PROCESS_ENTER)
        pass_box.Add(self.p, 0, wx.EXPAND | wx.ALL, 5); self.u.SetValue(self.user_config.get('username', ''));
        if self.user_config.get('remember'): self.p.SetValue(self.user_config.get('password', ''))
        self.remember_cb = wx.CheckBox(panel, label="&Remember me")
        self.autologin_cb = wx.CheckBox(panel, label="Log in &automatically")
        remember_default = self.user_config.get('remember', True)
        autologin_default = self.user_config.get('autologin', True)
        self.remember_cb.SetValue(remember_default)
        self.autologin_cb.SetValue(autologin_default if remember_default else False)
        self.remember_cb.Bind(wx.EVT_CHECKBOX, self.on_check_remember)
        
        login_btn = wx.Button(panel, label="&Login"); login_btn.Bind(wx.EVT_BUTTON, self.on_login)
        create_btn = wx.Button(panel, label="&Create Account..."); create_btn.Bind(wx.EVT_BUTTON, self.on_create_account)
        forgot_btn = wx.Button(panel, label="&Forgot Password?"); forgot_btn.Bind(wx.EVT_BUTTON, self.on_forgot)

        if dark_mode_on:
            for box in [server_box, user_box, pass_box]:
                box.GetStaticBox().SetForegroundColour(light_text_color)
                box.GetStaticBox().SetBackgroundColour(dark_color)
            for ctrl in [self.server_choice, self.u, self.p]:
                ctrl.SetBackgroundColour(dark_color); ctrl.SetForegroundColour(light_text_color)
            for btn in [manage_servers_btn, set_primary_btn, login_btn, create_btn, forgot_btn]:
                btn.SetBackgroundColour(dark_color); btn.SetForegroundColour(light_text_color)
            self.remember_cb.SetForegroundColour(light_text_color); self.autologin_cb.SetForegroundColour(light_text_color)

        self.populate_server_choice()
        self.refresh_welcome_preview()
        s.Add(server_box, 0, wx.EXPAND | wx.ALL, 5)
        s.Add(user_box, 0, wx.EXPAND | wx.ALL, 5); s.Add(pass_box, 0, wx.EXPAND | wx.ALL, 5)
        s.Add(self.remember_cb, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 10); s.Add(self.autologin_cb, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)
        btn_sizer = wx.BoxSizer(wx.HORIZONTAL); 
        btn_sizer.Add(login_btn, 1, wx.EXPAND | wx.ALL, 2); btn_sizer.Add(create_btn, 1, wx.EXPAND | wx.ALL, 2)
        s.Add(btn_sizer, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 5)
        s.Add(forgot_btn, 0, wx.ALIGN_CENTER | wx.ALL, 5)
        self.p.Bind(wx.EVT_TEXT_ENTER, self.on_login); panel.SetSizer(s); self.on_check_remember(None)

    def populate_server_choice(self):
        self.server_choice.Clear()
        labels = []
        for e in self.server_entries:
            suffix = " [Primary]" if e['name'] == self.primary_server_name else ""
            labels.append(f"{e['name']}{suffix} ({e['host']}:{e['port']})")
        for label in labels:
            self.server_choice.Append(label)
        preferred_name = self.user_config.get('last_server_name', '') or self.primary_server_name
        index = 0
        for i, entry in enumerate(self.server_entries):
            if entry['name'] == preferred_name:
                index = i
                break
        self.server_choice.SetSelection(index if self.server_entries else wx.NOT_FOUND)
        self.selected_server = self.server_entries[index] if self.server_entries else normalize_server_entry(load_server_config())

    def on_server_choice(self, _):
        idx = self.server_choice.GetSelection()
        if 0 <= idx < len(self.server_entries):
            self.selected_server = self.server_entries[idx]
            self.refresh_welcome_preview()

    def on_manage_servers(self, _):
        with ServerManagerDialog(self, self.server_entries, self.primary_server_name) as dlg:
            if dlg.ShowModal() == wx.ID_OK:
                entries = dlg.get_entries()
                if not entries:
                    wx.MessageBox("At least one server entry is required.", "Server Manager", wx.ICON_INFORMATION)
                    return
                self.server_entries = entries
                self.user_config['server_entries'] = self.server_entries
                updated_primary = dlg.get_primary_server_name()
                if updated_primary:
                    self.primary_server_name = updated_primary
                if self.primary_server_name not in [e['name'] for e in self.server_entries]:
                    self.primary_server_name = self.server_entries[0]['name']
                # Keep selection stable where possible
                current_name = self.selected_server.get('name', '')
                self.user_config['last_server_name'] = current_name if any(e['name'] == current_name for e in self.server_entries) else self.server_entries[0]['name']
                self.populate_server_choice()
                self.refresh_welcome_preview()

    def on_set_primary_server(self, _):
        if not self.selected_server:
            return
        self.primary_server_name = self.selected_server.get('name', self.primary_server_name)
        self.populate_server_choice()
        wx.MessageBox(f"{self.primary_server_name} is now your default server.", "Primary Server Updated", wx.OK | wx.ICON_INFORMATION)

    def refresh_welcome_preview(self):
        info = fetch_server_welcome(self.selected_server)
        snapshot = fetch_server_snapshot(self.selected_server)
        pre = str(info.get('pre_login', '') or '').strip()
        guide = (
            "Connection help: Use Manage Servers to add more servers. "
            "Set one as Primary for default login. You can switch servers any time from this menu."
        )
        motd = pre if (info.get('enabled') and pre) else "Welcome to Thrive Messenger."
        stats = (
            f"Server status: {snapshot.get('status', 'Unknown')}\n"
            f"Users online: {snapshot.get('online_users', 'Unknown')}\n"
            f"Admins online: {snapshot.get('online_admin_users', 'Unknown')}\n"
            f"Total users: {snapshot.get('total_users', 'Unknown')}\n"
            f"Server uptime: {snapshot.get('uptime', 'Unknown')}"
        )
        self.welcome_preview.SetLabel(f"{motd}\n\n{stats}\n\n{guide}")
        self.Layout()
    
    def on_forgot(self, event):
        set_active_server_config(self.selected_server)
        with ForgotPasswordDialog(self) as dlg: dlg.ShowModal()

    def on_create_account(self, event):
        set_active_server_config(self.selected_server)
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
        self.username = u
        self.password = p
        self.remember_checked = self.remember_cb.IsChecked()
        self.autologin_checked = self.autologin_cb.IsChecked()
        for entry in self.server_entries:
            entry['primary'] = (entry.get('name') == self.primary_server_name)
        self.user_config['server_entries'] = self.server_entries
        self.user_config['last_server_name'] = self.selected_server.get('name', '')
        self.user_config['primary_server_name'] = self.primary_server_name
        self.EndModal(wx.ID_OK)

    def on_key(self, event):
        if event.GetKeyCode() == wx.WXK_F1:
            open_help_docs_for_context("login", self)
            return
        event.Skip()

def format_size(size_bytes):
    if size_bytes <= 0: return "No limit"
    if size_bytes < 1024: return f"{size_bytes} bytes"
    elif size_bytes < 1048576: return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1073741824: return f"{size_bytes / 1048576:.1f} MB"
    else: return f"{size_bytes / 1073741824:.1f} GB"

def format_duration(total_seconds):
    total_seconds = max(0, int(total_seconds or 0))
    days, rem = divmod(total_seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, seconds = divmod(rem, 60)
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours or parts:
        parts.append(f"{hours}h")
    if minutes or parts:
        parts.append(f"{minutes}m")
    parts.append(f"{seconds}s")
    return " ".join(parts)

def is_chat_logging_enabled(config, username):
    per_user = config.get('chat_logging', {}) if isinstance(config.get('chat_logging', {}), dict) else {}
    if username in per_user:
        return bool(per_user.get(username))
    return bool(config.get('save_chat_history_default', False))

class ServerInfoDialog(wx.Dialog):
    def __init__(self, parent, details_text):
        super().__init__(parent, title="Server Information", size=(560, 420))
        dark_mode_on = is_windows_dark_mode()
        if dark_mode_on:
            dark_color = wx.Colour(40, 40, 40); light_text_color = wx.WHITE
            WxMswDarkMode().enable(self); self.SetBackgroundColour(dark_color)
        s = wx.BoxSizer(wx.VERTICAL)
        self.details = wx.TextCtrl(self, style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_RICH2)
        self.details.SetValue(details_text)
        if dark_mode_on:
            self.details.SetBackgroundColour(dark_color); self.details.SetForegroundColour(light_text_color)
        btn = wx.Button(self, wx.ID_OK, label="&Close")
        if dark_mode_on:
            btn.SetBackgroundColour(dark_color); btn.SetForegroundColour(light_text_color)
        self.details.SetToolTip("Server details view. Read-only information about the connected server.")
        s.Add(self.details, 1, wx.EXPAND | wx.ALL, 8); s.Add(btn, 0, wx.ALIGN_CENTER | wx.ALL, 6); self.SetSizer(s)
        self.Bind(wx.EVT_CHAR_HOOK, self.on_key)
    def on_key(self, event):
        if event.GetKeyCode() == wx.WXK_F1:
            open_help_docs_for_context("server_info", self)
        elif event.GetKeyCode() == wx.WXK_ESCAPE: self.Close()
        else: event.Skip()

class FileTransfersDialog(wx.Dialog):
    def __init__(self, parent, history):
        super().__init__(parent, title="File Transfers", size=(760, 420))
        self.history = list(history or [])
        self.panel = wx.Panel(self)
        s = wx.BoxSizer(wx.VERTICAL)
        self.lv = wx.ListCtrl(self.panel, style=wx.LC_REPORT | wx.LC_SINGLE_SEL)
        self.lv.InsertColumn(0, "Time", width=170)
        self.lv.InsertColumn(1, "Direction", width=80)
        self.lv.InsertColumn(2, "User", width=130)
        self.lv.InsertColumn(3, "File", width=160)
        self.lv.InsertColumn(4, "Status", width=90)
        self.lv.InsertColumn(5, "Path", width=260)
        for row in reversed(self.history):
            idx = self.lv.InsertItem(self.lv.GetItemCount(), format_timestamp(row.get("time")))
            self.lv.SetItem(idx, 1, str(row.get("direction", "")))
            self.lv.SetItem(idx, 2, str(row.get("user", "")))
            self.lv.SetItem(idx, 3, str(row.get("filename", "")))
            self.lv.SetItem(idx, 4, str(row.get("status", "")))
            self.lv.SetItem(idx, 5, str(row.get("path", "")))
        s.Add(self.lv, 1, wx.EXPAND | wx.ALL, 8)
        btns = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_open = wx.Button(self.panel, label="Open File")
        self.btn_folder = wx.Button(self.panel, label="Open Folder")
        self.btn_close = wx.Button(self.panel, wx.ID_CLOSE, label="Close")
        self.btn_open.Bind(wx.EVT_BUTTON, self.on_open_file)
        self.btn_folder.Bind(wx.EVT_BUTTON, self.on_open_folder)
        self.btn_close.Bind(wx.EVT_BUTTON, lambda e: self.Close())
        self.lv.Bind(wx.EVT_LIST_ITEM_SELECTED, self.on_select)
        btns.Add(self.btn_open, 0, wx.RIGHT, 6)
        btns.Add(self.btn_folder, 0, wx.RIGHT, 6)
        btns.AddStretchSpacer()
        btns.Add(self.btn_close, 0)
        s.Add(btns, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)
        self.panel.SetSizer(s)
        self.on_select(None)

    def _selected_path(self):
        idx = self.lv.GetFirstSelected()
        if idx == -1:
            return ""
        return self.lv.GetItemText(idx, 5).strip()

    def on_select(self, _):
        p = self._selected_path()
        exists = bool(p and os.path.exists(p))
        self.btn_open.Enable(exists and os.path.isfile(p))
        self.btn_folder.Enable(exists)

    def on_open_file(self, _):
        p = self._selected_path()
        if p and os.path.isfile(p):
            open_path_or_url(p)

    def on_open_folder(self, _):
        p = self._selected_path()
        if p and os.path.exists(p):
            open_path_or_url(os.path.dirname(p) if os.path.isfile(p) else p)

class UserDirectoryDialog(wx.Dialog):
    def __init__(self, parent_frame, users, my_username, contact_states):
        super().__init__(parent_frame, title="User Directory", size=(550, 500), style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.Bind(wx.EVT_CHAR_HOOK, self.on_key)
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
        sort_sizer = wx.BoxSizer(wx.HORIZONTAL)
        sort_label = wx.StaticText(panel, label="S&ort:")
        self.sort_choice = wx.Choice(panel, choices=["Name (A-Z)", "Name (Z-A)", "Status (Online first)"])
        self.sort_choice.SetSelection(0)
        self.sort_choice.Bind(wx.EVT_CHOICE, self.on_sort_changed)
        filter_label = wx.StaticText(panel, label="Filter:")
        self.filter_choice = wx.Choice(panel, choices=["All users", "In my contacts", "Not in my contacts"])
        self.filter_choice.SetSelection(0)
        self.filter_choice.Bind(wx.EVT_CHOICE, self.on_sort_changed)
        sort_sizer.Add(sort_label, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 5)
        sort_sizer.Add(self.sort_choice, 0, wx.RIGHT, 8)
        sort_sizer.Add(filter_label, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 5)
        sort_sizer.Add(self.filter_choice, 0, wx.RIGHT, 8)
        s.Add(sort_sizer, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 5)
        self.notebook = wx.Notebook(panel)
        self.tabs = {}
        self.tab_display_map = {}
        for tab_name in ["Everyone", "Online", "Offline", "Admins"]:
            lv = wx.ListBox(self.notebook, style=wx.LB_SINGLE)
            lv.Bind(wx.EVT_LISTBOX, self.on_selection_changed)
            lv.Bind(wx.EVT_LISTBOX_DCLICK, self.on_item_activated)
            lv.Bind(wx.EVT_CHAR_HOOK, self.on_list_key)
            lv.Bind(wx.EVT_CONTEXT_MENU, self.on_list_context_menu)
            if dark_mode_on: lv.SetBackgroundColour(dc); lv.SetForegroundColour(lt)
            self.notebook.AddPage(lv, tab_name)
            self.tabs[tab_name] = lv
            self.tab_display_map[tab_name] = []
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
        self.search_box.SetToolTip("Search all users in the current directory tab.")
        self.sort_choice.SetToolTip("Sort users by name or online status.")
        self.filter_choice.SetToolTip("Filter directory users by contact state.")
        self.notebook.SetToolTip("Directory tabs for Everyone, Online, Offline, and Admins.")
        self.btn_chat.SetToolTip("Start chat with selected user.")
        self.btn_file.SetToolTip("Send a file to selected user.")
        self.btn_block.SetToolTip("Block or unblock selected contact.")
        self.btn_add.SetToolTip("Send a contact request to selected user.")
        self.btn_close.SetToolTip("Close directory window.")
        self._populate_all_tabs(); self.update_button_states()
    def _get_active_list(self):
        page = self.notebook.GetSelection()
        return self.notebook.GetPage(page) if page != wx.NOT_FOUND else None
    def _get_selected_user(self):
        lv = self._get_active_list()
        if not lv: return None
        sel = lv.GetSelection()
        tab_name = self.notebook.GetPageText(self.notebook.GetSelection())
        mapping = self.tab_display_map.get(tab_name, [])
        if sel != wx.NOT_FOUND and 0 <= sel < len(mapping):
            self._selected_user = mapping[sel].get("user")
            return self._selected_user
        return None
    def _selected_entry(self):
        lv = self._get_active_list()
        if not lv:
            return None
        sel = lv.GetSelection()
        tab_name = self.notebook.GetPageText(self.notebook.GetSelection())
        mapping = self.tab_display_map.get(tab_name, [])
        if sel != wx.NOT_FOUND and 0 <= sel < len(mapping):
            return mapping[sel]
        return None
    def _is_selected_external_server(self):
        entry = self._selected_entry()
        if not entry:
            return False
        current_server = normalize_server_entry(getattr(wx.GetApp(), "active_server_entry", {})).get("name", "")
        selected_server = str(entry.get("server", current_server)).strip()
        return bool(selected_server and current_server and selected_server != current_server)
    def _populate_all_tabs(self):
        query = self.search_box.GetValue().strip().lower()
        filter_mode = self.filter_choice.GetSelection() if hasattr(self, "filter_choice") else 0
        for tab_name, lv in self.tabs.items():
            lv.Clear()
            self.tab_display_map[tab_name] = []
            tab_users = []
            for u in self._all_users:
                if query and query not in u["user"].lower(): continue
                if tab_name == "Online" and not u["online"]: continue
                if tab_name == "Offline" and u["online"]: continue
                if tab_name == "Admins" and not u["is_admin"]: continue
                if filter_mode == 1 and not u.get("is_contact", False): continue
                if filter_mode == 2 and u.get("is_contact", False): continue
                tab_users.append(u)
            mode = self.sort_choice.GetSelection()
            if mode == 1:
                tab_users = sorted(tab_users, key=lambda u: u["user"].lower(), reverse=True)
            elif mode == 2:
                tab_users = sorted(tab_users, key=lambda u: (not u.get("online", False), u["user"].lower()))
            else:
                tab_users = sorted(tab_users, key=lambda u: u["user"].lower())
            for u in tab_users:
                info_parts = []
                if u["user"] == self.my_username: info_parts.append("You")
                if u["is_admin"]: info_parts.append("Admin")
                if u["is_contact"]: info_parts.append("Contact")
                if u["is_blocked"]: info_parts.append("Blocked")
                info_text = ", ".join(info_parts)
                display = f"{u['user']}  |  {u['status_text']}  |  {u.get('server', 'Current')}"
                if info_text:
                    display += f"  |  {info_text}"
                lv.Append(display)
                self.tab_display_map[tab_name].append(u)
        self.update_button_states()
    def on_sort_changed(self, _):
        self._populate_all_tabs()
    def update_button_states(self):
        user = self._get_selected_user()
        external = self._is_selected_external_server()
        if not user or user == self.my_username:
            self.btn_chat.Disable(); self.btn_file.Disable(); self.btn_block.Disable(); self.btn_add.Disable()
            self.btn_block.SetLabel("&Block"); return
        self.btn_chat.Enable(not external)
        self.btn_file.Enable(not external)
        is_contact = user in self.contact_states
        self.btn_add.Enable((not is_contact) and (not external)); self.btn_add.SetLabel("&Add to Contacts")
        self.btn_block.Enable(is_contact and (not external))
        if external:
            self.btn_chat.SetToolTip("This server does not support cross-server chat from the current connection.")
            self.btn_file.SetToolTip("This server does not support cross-server file transfer from the current connection.")
            self.btn_add.SetToolTip("This server does not support cross-server contacts from the current connection.")
            self.btn_block.SetToolTip("This server does not support cross-server contact blocking from the current connection.")
        else:
            self.btn_chat.SetToolTip("Start chat with selected user.")
            self.btn_file.SetToolTip("Send a file to selected user.")
            self.btn_add.SetToolTip("Send a contact request to selected user.")
            self.btn_block.SetToolTip("Block or unblock selected contact.")
        if is_contact:
            blocked = self.contact_states.get(user, 0) == 1
            self.btn_block.SetLabel("&Unblock" if blocked else "&Block")
        else:
            self.btn_block.SetLabel("&Block")
    def on_search(self, event): self._populate_all_tabs()
    def on_tab_changed(self, event): self._selected_user = None; self.update_button_states(); event.Skip()
    def on_selection_changed(self, event): self.update_button_states(); event.Skip()
    def on_item_activated(self, event):
        self.on_selection_changed(event)
        self.on_start_chat(None)
    def on_start_chat(self, _):
        user = self._selected_user
        if self._is_selected_external_server():
            wx.MessageBox("This server does not support cross-server chat from the current connection.", "Feature Not Supported", wx.OK | wx.ICON_INFORMATION)
            return
        if not user or user == self.my_username: return
        app = wx.GetApp(); is_logging_enabled = is_chat_logging_enabled(app.user_config, user)
        is_contact = user in self.contact_states
        dlg = self.parent_frame.get_chat(user) or ChatDialog(self.parent_frame, user, self.parent_frame.sock, self.parent_frame.user, is_logging_enabled, is_contact=is_contact)
        dlg.Show(); wx.CallAfter(dlg.input_ctrl.SetFocus)
    def on_send_file(self, _):
        if self._is_selected_external_server():
            wx.MessageBox("This server does not support cross-server file transfer from the current connection.", "Feature Not Supported", wx.OK | wx.ICON_INFORMATION)
            return
        if self._selected_user: wx.GetApp().send_file_to(self._selected_user)
    def on_block_toggle(self, _):
        user = self._selected_user
        if self._is_selected_external_server():
            wx.MessageBox("This server does not support cross-server contact blocking from the current connection.", "Feature Not Supported", wx.OK | wx.ICON_INFORMATION)
            return
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
        if self._is_selected_external_server():
            wx.MessageBox("This server does not support cross-server contacts from the current connection.", "Feature Not Supported", wx.OK | wx.ICON_INFORMATION)
            return
        if not user: return
        self.parent_frame.sock.sendall(json.dumps({"action": "add_contact", "to": user}).encode() + b"\n")
        self.btn_add.Disable(); self.btn_add.SetLabel("Adding...")
    def _select_user_from_context_event(self, event):
        lv = self._get_active_list()
        if not lv:
            return
        try:
            pos = event.GetPosition()
        except Exception:
            pos = wx.DefaultPosition
        try:
            if isinstance(pos, wx.Point) and pos.x >= 0 and pos.y >= 0:
                idx = lv.HitTest(lv.ScreenToClient(pos))
                if idx != wx.NOT_FOUND:
                    lv.SetSelection(idx)
        except Exception:
            pass
        if lv.GetSelection() == wx.NOT_FOUND and lv.GetCount() > 0:
            lv.SetSelection(0)
    def on_list_context_menu(self, event):
        self._select_user_from_context_event(event)
        self._selected_user = self._get_selected_user()
        selected = bool(self._selected_user and self._selected_user != self.my_username)
        external = self._is_selected_external_server()
        is_contact = bool(self._selected_user and self._selected_user in self.contact_states)
        menu = wx.Menu()
        mi_chat = menu.Append(wx.ID_ANY, "Start Chat")
        mi_add = menu.Append(wx.ID_ANY, "Add to Contacts")
        mi_block = menu.Append(wx.ID_ANY, "Block/Unblock")
        mi_file = menu.Append(wx.ID_ANY, "Send File")
        menu.AppendSeparator()
        mi_refresh = menu.Append(wx.ID_ANY, "Refresh Directory")
        mi_chat.Enable(selected and not external)
        mi_add.Enable(selected and (not is_contact) and not external)
        mi_block.Enable(selected and is_contact and not external)
        mi_file.Enable(selected and not external)
        self.Bind(wx.EVT_MENU, self.on_start_chat, id=mi_chat.GetId())
        self.Bind(wx.EVT_MENU, self.on_add_to_contacts, id=mi_add.GetId())
        self.Bind(wx.EVT_MENU, self.on_block_toggle, id=mi_block.GetId())
        self.Bind(wx.EVT_MENU, self.on_send_file, id=mi_file.GetId())
        self.Bind(wx.EVT_MENU, lambda e: self.parent_frame.on_user_directory(None), id=mi_refresh.GetId())
        self.PopupMenu(menu)
        menu.Destroy()
    def on_list_key(self, event):
        if event.GetKeyCode() == wx.WXK_F1:
            open_help_docs_for_context("directory", self)
            return
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
    def on_key(self, event):
        if event.GetKeyCode() == wx.WXK_F1:
            open_help_docs_for_context("directory", self)
            return
        event.Skip()

    def merge_external_users(self, users):
        merged = {(u.get("user"), u.get("server", "Current")): u for u in self._all_users}
        for u in users:
            key = (u.get("user"), u.get("server", "External"))
            if key not in merged:
                merged[key] = u
        self._all_users = list(merged.values())
        self._populate_all_tabs()

class InviteUserDialog(wx.Dialog):
    def __init__(self, parent, username, methods=None):
        super().__init__(parent, title=f"Invite {username}", size=(460, 260))
        self.username = username
        panel = wx.Panel(self)
        s = wx.BoxSizer(wx.VERTICAL)
        s.Add(wx.StaticText(panel, label=f"Invite '{username}' to this server"), 0, wx.ALL, 8)
        row2 = wx.BoxSizer(wx.HORIZONTAL)
        row2.Add(wx.StaticText(panel, label="Email or phone:"), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 6)
        self.target = wx.TextCtrl(panel)
        row2.Add(self.target, 1, wx.EXPAND)
        s.Add(row2, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)
        self.include_link = wx.CheckBox(panel, label="Include setup link in invite message")
        self.include_link.SetValue(True)
        s.Add(self.include_link, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)
        hint = wx.StaticText(panel, label="Enter an email address or phone number. The app sends the invite automatically.")
        s.Add(hint, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)
        buttons = wx.StdDialogButtonSizer()
        ok_btn = wx.Button(panel, wx.ID_OK, label="Send Invite")
        cancel_btn = wx.Button(panel, wx.ID_CANCEL)
        ok_btn.SetDefault()
        buttons.AddButton(ok_btn)
        buttons.AddButton(cancel_btn)
        buttons.Realize()
        s.Add(buttons, 0, wx.ALIGN_CENTER | wx.ALL, 8)
        panel.SetSizer(s)

    def get_method(self):
        target = self.get_target()
        return "email" if "@" in target else "sms"

    def get_target(self):
        return self.target.GetValue().strip()

    def should_include_link(self):
        return self.include_link.IsChecked()

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
        if online and not was_online:
            wx.GetApp().play_sound("contact_online.wav")
            show_notification("Contact online", f"{user} has come online.")
        elif not online and was_online:
            wx.GetApp().play_sound("contact_offline.wav")
            show_notification("Contact offline", f"{user} has gone offline.")

    def __init__(self, user, sock):
        app = wx.GetApp()
        active_server = normalize_server_entry(getattr(app, "active_server_entry", {}))
        server_name = active_server.get("name") or active_server.get("host") or SERVER_CONFIG.get("host", "Server")
        connected_names = set(getattr(app, "connected_server_names", set()) or {server_name})
        others = max(0, len(connected_names) - 1)
        suffix = f", and {others} other server{'s' if others != 1 else ''}" if others > 0 else ""
        super().__init__(None, title=f"Thrive Messenger – {user} – connected to {server_name}{suffix}", size=(400,380)); self.user, self.sock = user, sock; self.task_bar_icon = None; self.is_exiting = False; self._directory_dlg = None
        self.current_status = wx.GetApp().user_config.get('status', 'online')
        self._empty_prompt_shown = False
        self._sort_mode = "name_asc"
        self.notifications = []; self.Bind(wx.EVT_CLOSE, self.on_close_window); panel = wx.Panel(self)

        dark_mode_on = is_windows_dark_mode()
        if dark_mode_on:
            dark_color = wx.Colour(40, 40, 40); light_text_color = wx.WHITE
            WxMswDarkMode().enable(self); self.SetBackgroundColour(dark_color); panel.SetBackgroundColour(dark_color)

        self._all_contacts = []
        self._contact_display_map = []
        box_contacts = wx.StaticBoxSizer(wx.VERTICAL, panel, "&Contacts")
        search_label = wx.StaticText(box_contacts.GetStaticBox(), label="Searc&h contacts:")
        self.search_box = wx.TextCtrl(box_contacts.GetStaticBox(), style=wx.TE_PROCESS_ENTER)
        self.search_box.Bind(wx.EVT_TEXT, self.on_search)
        self.lv = wx.ListBox(box_contacts.GetStaticBox(), style=wx.LB_SINGLE)
        self.lv.Bind(wx.EVT_CHAR_HOOK, self.on_key)
        self.lv.Bind(wx.EVT_LISTBOX, self.update_button_states)
        self.lv.Bind(wx.EVT_LISTBOX_DCLICK, self.on_contact_activated)
        self.lv.Bind(wx.EVT_CONTEXT_MENU, self.on_contact_context_menu)

        if dark_mode_on:
            box_contacts.GetStaticBox().SetForegroundColour(light_text_color)
            box_contacts.GetStaticBox().SetBackgroundColour(dark_color)
            search_label.SetForegroundColour(light_text_color)
            self.search_box.SetBackgroundColour(dark_color); self.search_box.SetForegroundColour(light_text_color)
            self.lv.SetBackgroundColour(dark_color); self.lv.SetForegroundColour(light_text_color)

        # Keep search controls stacked so assistive tech presents list navigation clearly.
        box_contacts.Add(search_label, 0, wx.LEFT | wx.RIGHT | wx.TOP, 5)
        box_contacts.Add(self.search_box, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 5)
        box_contacts.Add(self.lv, 1, wx.EXPAND|wx.ALL, 5)
        self.btn_block = wx.Button(panel, label="&Block"); self.btn_add = wx.Button(panel, label="&Add Contact"); self.btn_send = wx.Button(panel, label="&Start Chat"); self.btn_delete = wx.Button(panel, label="&Delete Contact")
        self.btn_send_file = wx.Button(panel, label="Send &File")
        self.btn_info = wx.Button(panel, label="Server &Info")
        self.btn_status = wx.Button(panel, label="Set Stat&us...")
        self.btn_directory = wx.Button(panel, label="User Director&y")
        self.btn_admin = wx.Button(panel, label="Use Ser&ver Side Commands"); self.btn_settings = wx.Button(panel, label="Se&ttings...")
        self.btn_update = wx.Button(panel, label="Check for U&pdates")
        self.btn_logout = wx.Button(panel, label="L&ogout"); self.btn_exit = wx.Button(panel, label="E&xit")

        if dark_mode_on:
            buttons = [self.btn_block, self.btn_add, self.btn_send, self.btn_delete, self.btn_send_file, self.btn_info, self.btn_status, self.btn_directory, self.btn_admin, self.btn_settings, self.btn_update, self.btn_logout, self.btn_exit]
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
        self.btn_logout.Bind(wx.EVT_BUTTON, self.on_logout); self.btn_exit.Bind(wx.EVT_BUTTON, self.on_exit)
        accel_entries = [(wx.ACCEL_ALT, ord('B'), self.btn_block.GetId()), (wx.ACCEL_ALT, ord('A'), self.btn_add.GetId()), (wx.ACCEL_ALT, ord('S'), self.btn_send.GetId()), (wx.ACCEL_ALT, ord('D'), self.btn_delete.GetId()), (wx.ACCEL_ALT, ord('F'), self.btn_send_file.GetId()), (wx.ACCEL_ALT, ord('I'), self.btn_info.GetId()), (wx.ACCEL_ALT, ord('U'), self.btn_status.GetId()), (wx.ACCEL_ALT, ord('Y'), self.btn_directory.GetId()), (wx.ACCEL_ALT, ord('V'), self.btn_admin.GetId()), (wx.ACCEL_ALT, ord('T'), self.btn_settings.GetId()), (wx.ACCEL_ALT, ord('P'), self.btn_update.GetId()), (wx.ACCEL_ALT, ord('O'), self.btn_logout.GetId()), (wx.ACCEL_ALT, ord('X'), self.btn_exit.GetId()),]
        accel_tbl = wx.AcceleratorTable(accel_entries); self.SetAcceleratorTable(accel_tbl)
        self.gs_main = wx.GridSizer(1, 5, 5, 5); self.gs_main.Add(self.btn_block, 0, wx.EXPAND); self.gs_main.Add(self.btn_add, 0, wx.EXPAND); self.gs_main.Add(self.btn_send, 0, wx.EXPAND); self.gs_main.Add(self.btn_send_file, 0, wx.EXPAND); self.gs_main.Add(self.btn_delete, 0, wx.EXPAND)
        self.gs_util = wx.GridSizer(1, 8, 5, 5); self.gs_util.Add(self.btn_info, 0, wx.EXPAND); self.gs_util.Add(self.btn_status, 0, wx.EXPAND); self.gs_util.Add(self.btn_directory, 0, wx.EXPAND); self.gs_util.Add(self.btn_admin, 0, wx.EXPAND); self.gs_util.Add(self.btn_settings, 0, wx.EXPAND); self.gs_util.Add(self.btn_update, 0, wx.EXPAND); self.gs_util.Add(self.btn_logout, 0, wx.EXPAND); self.gs_util.Add(self.btn_exit, 0, wx.EXPAND)
        s = wx.BoxSizer(wx.VERTICAL); s.Add(box_contacts, 1, wx.EXPAND|wx.ALL, 5); s.Add(self.gs_main, 0, wx.CENTER|wx.ALL, 5); s.Add(self.gs_util, 0, wx.CENTER|wx.ALL, 5); panel.SetSizer(s)
        self._root_sizer = s
        self._build_menu_bar()
        self._apply_voiceover_hints(search_label)
        self.Bind(wx.EVT_CHAR_HOOK, self.on_key)
        self.apply_action_button_layout()
        self.update_button_states()

    def apply_action_button_layout(self):
        show_actions = bool(wx.GetApp().user_config.get('show_main_action_buttons', True))
        for btn in [self.btn_block, self.btn_send, self.btn_send_file, self.btn_delete, self.btn_info, self.btn_status, self.btn_directory, self.btn_settings, self.btn_update, self.btn_logout, self.btn_exit]:
            btn.Show(show_actions)
        # Keep add contact and admin visible for keyboard/tab workflow.
        self.btn_add.Show(True)
        self.btn_admin.Show(True)
        if self._root_sizer:
            self._root_sizer.Layout()

    def _build_menu_bar(self):
        menubar = wx.MenuBar()
        file_menu = wx.Menu()
        self.mi_start_chat = file_menu.Append(wx.ID_ANY, "Start Chat\tReturn")
        self.mi_add_contact = file_menu.Append(wx.ID_ANY, "Add Contact\tAlt+A")
        self.mi_delete_contact = file_menu.Append(wx.ID_ANY, "Delete Contact\tDelete")
        self.mi_send_file = file_menu.Append(wx.ID_ANY, "Send File\tAlt+F")
        self.mi_file_transfers = file_menu.Append(wx.ID_ANY, "File Transfers")
        file_menu.AppendSeparator()
        self.mi_user_directory = file_menu.Append(wx.ID_ANY, "User Directory\tAlt+Y")
        self.mi_server_info = file_menu.Append(wx.ID_ANY, "Server Info\tAlt+I")
        self.mi_server_manager = file_menu.Append(wx.ID_ANY, "Server Manager")
        self.mi_settings = file_menu.Append(wx.ID_ANY, "Settings\tAlt+T")
        file_menu.AppendSeparator()
        self.mi_logout = file_menu.Append(wx.ID_ANY, "Logout\tAlt+O")
        self.mi_exit = file_menu.Append(wx.ID_ANY, "Exit\tAlt+X")

        contacts_menu = wx.Menu()
        self.mi_block_toggle = contacts_menu.Append(wx.ID_ANY, "Block/Unblock\tAlt+B")
        self.mi_toggle_chat_log = contacts_menu.Append(wx.ID_ANY, "Toggle Chat History For Selected Contact")
        self.mi_refresh_directory = contacts_menu.Append(wx.ID_ANY, "Refresh Directory")
        user_menu = wx.Menu()
        self.mi_user_start_chat = user_menu.Append(wx.ID_ANY, "Start Chat")
        self.mi_user_send_file = user_menu.Append(wx.ID_ANY, "Send File")
        self.mi_user_transfers = user_menu.Append(wx.ID_ANY, "File Transfers")
        self.mi_user_add_contact = user_menu.Append(wx.ID_ANY, "Add Contact")
        self.mi_user_toggle_block = user_menu.Append(wx.ID_ANY, "Block/Unblock")
        self.mi_user_toggle_history = user_menu.Append(wx.ID_ANY, "Toggle Chat History")
        self.mi_user_delete_contact = user_menu.Append(wx.ID_ANY, "Delete Contact")

        view_menu = wx.Menu()
        self.mi_sort_name_asc = view_menu.AppendRadioItem(wx.ID_ANY, "Sort: Name (A-Z)")
        self.mi_sort_name_desc = view_menu.AppendRadioItem(wx.ID_ANY, "Sort: Name (Z-A)")
        self.mi_sort_status = view_menu.AppendRadioItem(wx.ID_ANY, "Sort: Status (Online first)")
        self.mi_sort_name_asc.Check(True)

        help_menu = wx.Menu()
        self.mi_help = help_menu.Append(wx.ID_ANY, "Help\tF1")
        demo_menu = wx.Menu()
        self.mi_demo_onboarding = demo_menu.Append(wx.ID_ANY, "Watch Onboarding Demo")
        self.mi_demo_chat_files = demo_menu.Append(wx.ID_ANY, "Watch Chat and File Demo")
        self.mi_demo_admin_tools = demo_menu.Append(wx.ID_ANY, "Watch Admin Tools Demo")
        demo_menu.AppendSeparator()
        self.mi_demo_videos = demo_menu.Append(wx.ID_ANY, "Open Demo Videos Folder")
        help_menu.AppendSubMenu(demo_menu, "Watch Demo Videos")
        self.mi_submit_logs = help_menu.Append(wx.ID_ANY, "Submit Diagnostic Logs")

        menubar.Append(file_menu, "&File")
        menubar.Append(contacts_menu, "&Contacts")
        menubar.Append(user_menu, "&User")
        menubar.Append(view_menu, "&View")
        menubar.Append(help_menu, "&Help")
        self.SetMenuBar(menubar)

        self.Bind(wx.EVT_MENU, self.on_send, self.mi_start_chat)
        self.Bind(wx.EVT_MENU, self.on_add, self.mi_add_contact)
        self.Bind(wx.EVT_MENU, self.on_delete, self.mi_delete_contact)
        self.Bind(wx.EVT_MENU, self.on_send_file, self.mi_send_file)
        self.Bind(wx.EVT_MENU, self.on_file_transfers, self.mi_file_transfers)
        self.Bind(wx.EVT_MENU, self.on_user_directory, self.mi_user_directory)
        self.Bind(wx.EVT_MENU, self.on_server_info, self.mi_server_info)
        self.Bind(wx.EVT_MENU, self.on_server_manager, self.mi_server_manager)
        self.Bind(wx.EVT_MENU, self.on_settings, self.mi_settings)
        self.Bind(wx.EVT_MENU, self.on_logout, self.mi_logout)
        self.Bind(wx.EVT_MENU, self.on_exit, self.mi_exit)
        self.Bind(wx.EVT_MENU, self.on_block_toggle, self.mi_block_toggle)
        self.Bind(wx.EVT_MENU, self.on_toggle_selected_chat_logging, self.mi_toggle_chat_log)
        self.Bind(wx.EVT_MENU, self.on_user_directory, self.mi_refresh_directory)
        self.Bind(wx.EVT_MENU, self.on_send, self.mi_user_start_chat)
        self.Bind(wx.EVT_MENU, self.on_send_file, self.mi_user_send_file)
        self.Bind(wx.EVT_MENU, self.on_file_transfers, self.mi_user_transfers)
        self.Bind(wx.EVT_MENU, self.on_add, self.mi_user_add_contact)
        self.Bind(wx.EVT_MENU, self.on_block_toggle, self.mi_user_toggle_block)
        self.Bind(wx.EVT_MENU, self.on_toggle_selected_chat_logging, self.mi_user_toggle_history)
        self.Bind(wx.EVT_MENU, self.on_delete, self.mi_user_delete_contact)
        self.Bind(wx.EVT_MENU, lambda e: self._set_sort_mode("name_asc"), self.mi_sort_name_asc)
        self.Bind(wx.EVT_MENU, lambda e: self._set_sort_mode("name_desc"), self.mi_sort_name_desc)
        self.Bind(wx.EVT_MENU, lambda e: self._set_sort_mode("status"), self.mi_sort_status)
        self.Bind(wx.EVT_MENU, lambda e: open_help_docs_for_context("main", self), self.mi_help)
        self.Bind(wx.EVT_MENU, lambda e: self.on_watch_demo_video("onboarding"), self.mi_demo_onboarding)
        self.Bind(wx.EVT_MENU, lambda e: self.on_watch_demo_video("chat_files"), self.mi_demo_chat_files)
        self.Bind(wx.EVT_MENU, lambda e: self.on_watch_demo_video("admin_tools"), self.mi_demo_admin_tools)
        self.Bind(wx.EVT_MENU, self.on_open_demo_videos, self.mi_demo_videos)
        self.Bind(wx.EVT_MENU, self.on_submit_logs, self.mi_submit_logs)

    def _apply_voiceover_hints(self, search_label):
        search_label.SetToolTip("Type a username to filter your contact list.")
        self.search_box.SetToolTip("Search contacts. Press Return to start chat with selected contact.")
        self.lv.SetToolTip("Contacts list. Use arrow keys to select a contact, then press Return to chat.")
        self.btn_add.SetToolTip("Add a contact by username.")
        self.btn_send.SetToolTip("Start chat with selected contact.")
        self.btn_send_file.SetToolTip("Send a file to selected contact.")
        self.btn_delete.SetToolTip("Remove selected contact.")
        self.btn_block.SetToolTip("Block or unblock selected contact.")
        self.btn_directory.SetToolTip("Browse all users and add contacts from the directory.")
        self.btn_settings.SetToolTip("Open preferences and accessibility options.")
        self.btn_admin.SetToolTip("Open server-side command console if you are an admin.")
        self.btn_status.SetToolTip("Set your current status message.")
        self.btn_update.SetToolTip("Check for client updates.")
        self.btn_logout.SetToolTip("Sign out and return to login.")
        self.btn_exit.SetToolTip("Quit the app.")

    def _set_sort_mode(self, mode):
        self._sort_mode = mode
        self._apply_search_filter()

    def _show_add_contact_prompt(self):
        result = wx.MessageBox(
            "No contacts are available yet. Would you like to add a contact now?",
            "No Contacts",
            wx.YES_NO | wx.ICON_INFORMATION,
            self
        )
        if result == wx.YES:
            self.on_add(None)

    def _selected_contact_name(self):
        sel = self.lv.GetSelection()
        if sel == wx.NOT_FOUND or sel >= len(self._contact_display_map):
            return None
        return self._contact_display_map[sel]
    def on_submit_logs(self, _):
        app = wx.GetApp()
        ok, err = submit_logs_payload(app.user_config, reason="manual_submit")
        if ok:
            show_notification("Diagnostics", "Logs submitted successfully.", timeout=4)
            wx.MessageBox("Diagnostic logs submitted successfully.", "Logs Submitted", wx.OK | wx.ICON_INFORMATION)
            log_event("info", "logs_submitted_manual")
        else:
            wx.MessageBox(f"Could not submit logs:\n{err}", "Log Submit Failed", wx.OK | wx.ICON_ERROR)
            log_event("error", "logs_submit_failed", {"error": str(err)})

    def on_open_demo_videos(self, _):
        open_path_or_url(get_demo_videos_dir())

    def on_watch_demo_video(self, key):
        meta = DEMO_VIDEOS.get(key)
        if not meta:
            return
        demo_dir = get_demo_videos_dir()
        clip_path = os.path.join(demo_dir, meta["filename"])
        description = meta["description"]
        if wx.GetApp().user_config.get('read_messages_aloud', False):
            speak_text(f"{meta['title']}. {description}")
        if not os.path.isfile(clip_path):
            wx.MessageBox(
                f"{meta['title']} is not available yet.\n\nExpected file:\n{clip_path}\n\nDescription:\n{description}",
                "Demo Video Missing",
                wx.OK | wx.ICON_INFORMATION,
                self,
            )
            return
        choice = wx.MessageBox(
            f"{meta['title']}\n\nDescription:\n{description}\n\nOpen this video now?",
            "Watch Demo Video",
            wx.YES_NO | wx.ICON_QUESTION,
            self,
        )
        if choice == wx.YES:
            open_path_or_url(clip_path)
    def on_settings(self, event):
        app = wx.GetApp()
        with SettingsDialog(self, app.user_config) as dlg:
            if dlg.ShowModal() == wx.ID_OK:
                selected_pack = dlg.choice.GetStringSelection()
                app.user_config['soundpack'] = selected_pack
                if dlg.set_selected_default_cb.IsChecked() and selected_pack not in ("default", "none"):
                    app.user_config['default_soundpack'] = selected_pack
                app.user_config['sound_volume'] = int(dlg.sound_volume_slider.GetValue())
                app.user_config['call_input_volume'] = int(dlg.call_input_slider.GetValue())
                app.user_config['call_output_volume'] = int(dlg.call_output_slider.GetValue())
                app.user_config['auto_open_received_files'] = dlg.auto_open_files_cb.IsChecked()
                app.user_config['read_messages_aloud'] = dlg.read_aloud_cb.IsChecked()
                app.user_config['save_chat_history_default'] = dlg.global_chat_logging_cb.IsChecked()
                app.user_config['show_main_action_buttons'] = dlg.show_main_actions_cb.IsChecked()
                app.user_config['typing_indicators'] = dlg.typing_indicator_cb.IsChecked()
                app.user_config['announce_typing'] = dlg.announce_typing_cb.IsChecked()
                app.user_config['enter_key_action'] = 'send' if dlg.enter_action_choice.GetSelection() == 0 else 'newline'
                app.user_config['escape_main_action'] = ('none' if dlg.escape_action_choice.GetSelection() == 0 else ('minimize' if dlg.escape_action_choice.GetSelection() == 1 else 'quit'))
                save_user_config(app.user_config)
                self.apply_action_button_layout()
                wx.MessageBox("Settings have been applied.", "Settings Saved", wx.OK | wx.ICON_INFORMATION)

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
        app = wx.GetApp()
        current_server_name = getattr(app, "active_server_entry", {}).get("name", "Current Server")
        users = msg.get("users", [])
        for u in users:
            u["server"] = u.get("server", current_server_name)
        dlg = UserDirectoryDialog(self, users, self.user, self.contact_states)
        self._directory_dlg = dlg
        dlg.Show()
        if app.user_config.get("server_entries") and app.session_password:
            def merge_later():
                extras = []
                active = normalize_server_entry(getattr(app, "active_server_entry", {}))
                for entry in app.user_config.get("server_entries", []):
                    normalized = normalize_server_entry(entry)
                    if normalized["host"].lower() == active.get("host", "").lower() and normalized["port"] == active.get("port", 0):
                        continue
                    extras.extend(app.fetch_directory_for_server(normalized, self.user, app.session_password))
                if extras and self._directory_dlg:
                    wx.CallAfter(self._directory_dlg.merge_external_users, extras)
            threading.Thread(target=merge_later, daemon=True).start()
    def on_server_info(self, _):
        self.sock.sendall(json.dumps({"action": "server_info"}).encode() + b"\n")

    def on_server_manager(self, _):
        app = wx.GetApp()
        entries = dedupe_server_entries(app.user_config.get('server_entries', []))
        if not entries:
            entries = [normalize_server_entry(getattr(app, "active_server_entry", SERVER_CONFIG))]
        primary_name = app.user_config.get('primary_server_name', '')
        with ServerManagerDialog(self, entries, primary_name) as dlg:
            if dlg.ShowModal() != wx.ID_OK:
                return
            updated_entries = dlg.get_entries()
            if not updated_entries:
                wx.MessageBox("At least one server entry is required.", "Server Manager", wx.OK | wx.ICON_INFORMATION)
                return
            app.user_config['server_entries'] = updated_entries
            selected_primary = dlg.get_primary_server_name()
            if selected_primary:
                app.user_config['primary_server_name'] = selected_primary
            if app.user_config.get('last_server_name') not in [e.get('name') for e in updated_entries]:
                app.user_config['last_server_name'] = app.user_config.get('primary_server_name') or updated_entries[0].get('name', '')
            save_user_config(app.user_config)
            wx.MessageBox("Server list updated. Changes apply on next login.", "Server Manager", wx.OK | wx.ICON_INFORMATION)
    def on_server_info_response(self, msg):
        encrypted = isinstance(self.sock, ssl.SSLSocket)
        app = wx.GetApp()
        active = normalize_server_entry(getattr(app, "active_server_entry", {}))
        size_limit = msg.get("size_limit", 0)
        size_str = format_size(size_limit) if size_limit > 0 else "No limit"
        blackfiles = msg.get("blackfiles", [])
        blackfiles_str = ", ".join(f".{ext}" for ext in blackfiles) if blackfiles else "None"
        max_status_len = msg.get("max_status_length", "N/A")
        lines = [
            "Connected Server",
            f"Name: {active.get('name', 'Current Server')}",
            f"Host: {active.get('host', SERVER_CONFIG.get('host', 'Unknown'))}",
            f"Port: {msg.get('port', active.get('port', SERVER_CONFIG.get('port', 'N/A')))}",
            f"Encryption: {'Yes' if encrypted else 'No'}",
            "",
            "Server Status",
            f"Registered users: {msg.get('total_users', 'N/A')}",
            f"Users online: {msg.get('online_users', 'N/A')}",
            f"Admins online: {msg.get('online_admin_users', 'N/A')}",
            f"Uptime: {format_duration(int(msg.get('uptime_seconds', 0) or 0))}",
            "",
            "File Policy",
            f"File size limit: {size_str}",
            f"Blocked file extensions: {blackfiles_str}",
            f"Max status length: {max_status_len}",
        ]
        with ServerInfoDialog(self, "\n".join(lines)) as dlg: dlg.ShowModal()
    def update_button_states(self, event=None):
        selected_contact = self._selected_contact_name()
        is_selection = selected_contact is not None
        is_contact_selection = selected_contact is not None and selected_contact in self.contact_states
        self.btn_send.Enable(is_contact_selection)
        self.btn_delete.Enable(is_contact_selection)
        self.btn_block.Enable(is_contact_selection)
        self.btn_send_file.Enable(is_contact_selection)
        if is_selection:
            contact_name = selected_contact
            is_blocked = self.contact_states.get(contact_name, 0); self.btn_block.SetLabel("&Unblock" if is_blocked else "&Block")
        else: self.btn_block.SetLabel("&Block")
        if hasattr(self, "mi_delete_contact"):
            self.mi_delete_contact.Enable(is_contact_selection)
        if hasattr(self, "mi_user_delete_contact"):
            self.mi_user_delete_contact.Enable(is_contact_selection)
        if selected_contact and is_contact_selection:
            self.btn_send.SetLabel(f"&Start Chat with {selected_contact}")
            self.btn_send_file.SetLabel(f"Send &File to {selected_contact}")
            self.btn_block.SetLabel(f"{'&Unblock' if self.contact_states.get(selected_contact, 0) else '&Block'} {selected_contact}")
            self.btn_delete.SetLabel(f"&Delete {selected_contact}")
        else:
            self.btn_send.SetLabel("&Start Chat")
            self.btn_send_file.SetLabel("Send &File")
            self.btn_delete.SetLabel("&Delete Contact")
            if not is_selection:
                self.btn_block.SetLabel("&Block")
        if event: event.Skip()
    def on_set_status(self, event):
        menu = wx.Menu()
        app = wx.GetApp()
        status_preset = app.user_config.get('status_preset', 'online')
        status_custom_by_preset = app.user_config.get('status_custom_by_preset', {})
        if not isinstance(status_custom_by_preset, dict):
            status_custom_by_preset = {}
        status_global_custom = app.user_config.get('status_global_custom', '')

        def _compose_status_text(preset):
            preset_custom = str(status_custom_by_preset.get(preset, '') or '').strip()
            global_custom = str(status_global_custom or '').strip()
            if preset_custom:
                return f"{preset} status, {preset_custom}"
            if global_custom:
                return f"{preset} status, {global_custom}"
            return preset

        current_line = menu.Append(wx.ID_ANY, f"Currently: {_compose_status_text(status_preset)}")
        current_line.Enable(False)
        menu.AppendSeparator()

        preset_items = {}
        for preset in STATUS_PRESETS:
            label = preset
            if preset == status_preset:
                label += " (Selected)"
            mi = menu.Append(wx.ID_ANY, label)
            preset_items[mi.GetId()] = preset

        menu.AppendSeparator()
        mi_custom_selected = menu.Append(wx.ID_ANY, f"Set custom text for selected status ({status_preset})")
        mi_custom_global = menu.Append(wx.ID_ANY, "Set global custom text")
        mi_clear_selected = menu.Append(wx.ID_ANY, f"Clear custom text for selected status ({status_preset})")
        mi_clear_global = menu.Append(wx.ID_ANY, "Clear global custom text")

        def _send_status_text(text):
            self.current_status = text
            app.user_config['status'] = text
            save_user_config(app.user_config)
            try:
                self.sock.sendall((json.dumps({"action": "set_status", "status_text": text}) + "\n").encode())
            except Exception as e:
                print(f"Error setting status: {e}")

        def _on_pick_preset(evt):
            nonlocal status_preset
            preset = preset_items.get(evt.GetId())
            if not preset:
                return
            status_preset = preset
            app.user_config['status_preset'] = preset
            _send_status_text(_compose_status_text(preset))

        def _on_set_custom_selected(_):
            nonlocal status_custom_by_preset
            with wx.TextEntryDialog(self, f"Custom text for '{status_preset}' (leave blank to clear):", "Custom Status") as dlg:
                if dlg.ShowModal() != wx.ID_OK:
                    return
                txt = dlg.GetValue().strip()
                if txt:
                    status_custom_by_preset[status_preset] = txt
                else:
                    status_custom_by_preset.pop(status_preset, None)
                app.user_config['status_custom_by_preset'] = status_custom_by_preset
                _send_status_text(_compose_status_text(status_preset))

        def _on_set_custom_global(_):
            nonlocal status_global_custom
            with wx.TextEntryDialog(self, "Global custom text (used when selected status has no custom text):", "Global Custom Status") as dlg:
                if dlg.ShowModal() != wx.ID_OK:
                    return
                status_global_custom = dlg.GetValue().strip()
                app.user_config['status_global_custom'] = status_global_custom
                _send_status_text(_compose_status_text(status_preset))

        def _on_clear_selected(_):
            status_custom_by_preset.pop(status_preset, None)
            app.user_config['status_custom_by_preset'] = status_custom_by_preset
            _send_status_text(_compose_status_text(status_preset))

        def _on_clear_global(_):
            nonlocal status_global_custom
            status_global_custom = ""
            app.user_config['status_global_custom'] = ""
            _send_status_text(_compose_status_text(status_preset))

        for item_id in preset_items:
            menu.Bind(wx.EVT_MENU, _on_pick_preset, id=item_id)
        menu.Bind(wx.EVT_MENU, _on_set_custom_selected, mi_custom_selected)
        menu.Bind(wx.EVT_MENU, _on_set_custom_global, mi_custom_global)
        menu.Bind(wx.EVT_MENU, _on_clear_selected, mi_clear_selected)
        menu.Bind(wx.EVT_MENU, _on_clear_global, mi_clear_global)
        self.PopupMenu(menu)
        menu.Destroy()
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
    def _prompt_invite_user(self, username, methods=None):
        with InviteUserDialog(self, username, methods=methods) as dlg:
            if dlg.ShowModal() == wx.ID_OK:
                payload = {
                    "action": "invite_user",
                    "username": username,
                    "method": dlg.get_method(),
                    "target": dlg.get_target(),
                    "include_link": dlg.should_include_link(),
                }
                try:
                    self.sock.sendall((json.dumps(payload) + "\n").encode())
                except Exception as e:
                    wx.MessageBox(f"Could not send invite request: {e}", "Invite Failed", wx.OK | wx.ICON_ERROR)
    def on_add_contact_failed(self, payload):
        if isinstance(payload, dict):
            reason = payload.get("reason", "Add contact failed.")
            invite_methods = payload.get("invite_methods", [])
            suggest_invite = bool(payload.get("suggest_invite"))
        else:
            reason = str(payload)
            invite_methods = []
            suggest_invite = False
        wx.MessageBox(reason, "Add Contact Failed", wx.ICON_ERROR)
        match = re.search(r"User '([^']+)' does not exist", str(reason))
        if not match:
            return
        missing_user = match.group(1)
        if not suggest_invite and not invite_methods:
            invite_methods = ["email", "sms"]
        res = wx.MessageBox(
            f"{missing_user} does not have an account on this server yet. Would you like to send an invite?",
            "Invite User",
            wx.YES_NO | wx.ICON_QUESTION,
            self
        )
        if res == wx.YES:
            self._prompt_invite_user(missing_user, invite_methods if invite_methods else None)
    def on_invite_result(self, msg):
        ok = bool(msg.get("ok"))
        method = msg.get("method", "invite")
        target = msg.get("target", "")
        reason = msg.get("reason", "")
        if ok:
            show_notification("Invite Sent", f"{method.upper()} invite sent to {target or 'recipient'}.", timeout=8)
        else:
            wx.MessageBox(reason or "Invite could not be sent.", "Invite Failed", wx.OK | wx.ICON_ERROR)
    def on_add_contact_success(self, contact_data):
        c = contact_data; self.contact_states[c["user"]] = c["blocked"]
        status = c.get("status_text", "online") if c["online"] and not c["blocked"] else "offline"
        if c.get("is_admin"): status += " (Admin)"
        self._all_contacts.append({"user": c["user"], "status": status, "blocked": c["blocked"]})
        self._apply_search_filter()
        chat = self.get_chat(c["user"])
        if chat:
            chat.hide_add_button()
            chat.send_pending_after_contact_added()
        if self._directory_dlg:
            for u in self._directory_dlg._all_users:
                if u["user"] == c["user"]: u["is_contact"] = True; u["is_blocked"] = c["blocked"] == 1; break
            self._directory_dlg._populate_all_tabs(); self._directory_dlg.update_button_states()
    def on_server_alert(self, message):
        wx.GetApp().play_sound("receive.wav")
        show_notification("Server Alert", message, timeout=8)
    def on_file_transfers(self, _):
        with FileTransfersDialog(self, wx.GetApp().transfer_history) as dlg:
            dlg.ShowModal()
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
        if not self._all_contacts and not self._empty_prompt_shown:
            self._empty_prompt_shown = True
            wx.CallAfter(self._show_add_contact_prompt)
            wx.CallAfter(self.on_user_directory, None)
    def _apply_search_filter(self):
        query = self.search_box.GetValue().strip().lower()
        self.lv.Clear()
        self._contact_display_map = []
        first_real_idx = -1
        contacts = list(self._all_contacts)
        if self._sort_mode == "name_desc":
            contacts = sorted(contacts, key=lambda c: c["user"].lower(), reverse=True)
        elif self._sort_mode == "status":
            contacts = sorted(contacts, key=lambda c: (c["status"].startswith("offline"), c["user"].lower()))
        else:
            contacts = sorted(contacts, key=lambda c: c["user"].lower())
        for c in contacts:
            if query and query not in c["user"].lower(): continue
            display = f"{c['user']}  |  {c['status']}"
            self.lv.Append(display)
            idx = self.lv.GetCount() - 1
            self._contact_display_map.append(c["user"])
            if first_real_idx == -1:
                first_real_idx = idx
        if self.lv.GetCount() == 0:
            self.lv.Append("(No contacts)  |  Press Alt+A to add a contact")
            self._contact_display_map.append(None)
        elif first_real_idx >= 0:
            self.lv.SetSelection(first_real_idx)
        self.update_button_states()
    def _select_contact_from_context_event(self, event):
        try:
            pos = event.GetPosition()
        except Exception:
            pos = wx.DefaultPosition
        try:
            if isinstance(pos, wx.Point) and pos.x >= 0 and pos.y >= 0:
                idx = self.lv.HitTest(self.lv.ScreenToClient(pos))
                if idx != wx.NOT_FOUND:
                    self.lv.SetSelection(idx)
        except Exception:
            pass
        if self.lv.GetSelection() == wx.NOT_FOUND and self.lv.GetCount() > 0:
            self.lv.SetSelection(0)
        self.update_button_states()
    def on_contact_context_menu(self, event):
        self._select_contact_from_context_event(event)
        selected = self._selected_contact_name()
        has_contact = bool(selected and selected in self.contact_states)
        menu = wx.Menu()
        mi_chat = menu.Append(wx.ID_ANY, "Start Chat")
        mi_add = menu.Append(wx.ID_ANY, "Add Contact")
        mi_file = menu.Append(wx.ID_ANY, "Send File")
        mi_log = menu.Append(wx.ID_ANY, "Toggle Chat History")
        mi_block = menu.Append(wx.ID_ANY, "Block/Unblock")
        mi_delete = menu.Append(wx.ID_ANY, "Delete Contact")
        menu.AppendSeparator()
        mi_dir = menu.Append(wx.ID_ANY, "Open User Directory")
        mi_chat.Enable(bool(selected))
        mi_add.Enable(True)
        mi_file.Enable(bool(selected))
        mi_log.Enable(bool(selected))
        mi_block.Enable(has_contact)
        mi_delete.Enable(has_contact)
        self.Bind(wx.EVT_MENU, self.on_send, id=mi_chat.GetId())
        self.Bind(wx.EVT_MENU, self.on_add, id=mi_add.GetId())
        self.Bind(wx.EVT_MENU, self.on_send_file, id=mi_file.GetId())
        self.Bind(wx.EVT_MENU, self.on_toggle_selected_chat_logging, id=mi_log.GetId())
        self.Bind(wx.EVT_MENU, self.on_block_toggle, id=mi_block.GetId())
        self.Bind(wx.EVT_MENU, self.on_delete, id=mi_delete.GetId())
        self.Bind(wx.EVT_MENU, self.on_user_directory, id=mi_dir.GetId())
        self.PopupMenu(menu)
        menu.Destroy()
    def on_toggle_selected_chat_logging(self, _):
        c = self._selected_contact_name()
        if not c:
            return
        app = wx.GetApp()
        if 'chat_logging' not in app.user_config or not isinstance(app.user_config.get('chat_logging'), dict):
            app.user_config['chat_logging'] = {}
        current = is_chat_logging_enabled(app.user_config, c)
        app.user_config['chat_logging'][c] = not current
        save_user_config(app.user_config)
        state = "enabled" if not current else "disabled"
        show_notification("Chat history", f"Chat history {state} for {c}.", timeout=5)
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
        if self.is_exiting:
            event.Skip()
            return
        # macOS does not reliably expose a tray restore path for this app build.
        # Closing should fully exit so relaunch always restores a visible window.
        if sys.platform == "darwin":
            self.on_exit(None)
            return
        self.minimize_to_tray()
    def minimize_to_tray(self):
        self.Hide()
        if not self.task_bar_icon:
            self.task_bar_icon = ThriveTaskBarIcon(self)
    def restore_from_tray(self):
        if self.task_bar_icon: self.task_bar_icon.Destroy(); self.task_bar_icon = None
        self.Show(); self.Raise()
        if self._directory_dlg and self._directory_dlg.IsShown(): self._directory_dlg.Raise()
        for child in self.GetChildren():
            if isinstance(child, (ChatDialog, AdminDialog)) and child.IsShown(): child.Raise()
    def on_exit(self, _):
        print("Exiting application...");
        app = wx.GetApp(); app.intentional_disconnect = True
        try: self.sock.sendall(json.dumps({"action":"logout"}).encode()+b"\n")
        except: pass
        try: self.sock.close()
        except: pass
        if self._directory_dlg: self._directory_dlg.Destroy(); self._directory_dlg = None
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
        app.play_sound("logout.wav"); self.Destroy()
        app.show_login_dialog()
    def on_key(self, evt):
        if evt.GetKeyCode() == wx.WXK_F1:
            open_help_docs_for_context("main", self)
        elif evt.CmdDown() and evt.GetKeyCode() == ord(','):
            self.on_settings(None)
            return
        elif evt.GetKeyCode() == wx.WXK_ESCAPE:
            action = str(wx.GetApp().user_config.get('escape_main_action', 'none') or 'none')
            if action == 'quit':
                self.on_exit(None)
            elif action == 'minimize':
                self.minimize_to_tray()
            return
        elif evt.GetKeyCode() == wx.WXK_RETURN: self.on_send(None)
        elif evt.GetKeyCode() == wx.WXK_DELETE: self.on_delete(None)
        else: evt.Skip()
    def on_block_toggle(self, _):
        c = self._selected_contact_name()
        if not c: return
        blocked = self.contact_states.get(c,0) == 1; action = "unblock_contact" if blocked else "block_contact"; self.sock.sendall(json.dumps({"action":action,"to":c}).encode()+b"\n"); self.contact_states[c] = 0 if blocked else 1
        for entry in self._all_contacts:
            if entry["user"] == c: entry["blocked"] = 0 if blocked else 1; break
        self._apply_search_filter()
    def on_delete(self, _):
        c = self._selected_contact_name()
        if not c or c not in self.contact_states:
            return
        self.sock.sendall(json.dumps({"action":"delete_contact","to":c}).encode()+b"\n"); self.contact_states.pop(c, None)
        self._all_contacts = [entry for entry in self._all_contacts if entry["user"] != c]
        self._apply_search_filter()
    def on_send(self, _):
        c = self._selected_contact_name()
        if not c:
            if not self._all_contacts:
                self._show_add_contact_prompt()
            return
        app = wx.GetApp(); is_logging_enabled = is_chat_logging_enabled(app.user_config, c)
        dlg = self.get_chat(c) or ChatDialog(self, c, self.sock, self.user, is_logging_enabled)
        dlg.Show(); wx.CallAfter(dlg.input_ctrl.SetFocus)
    def on_send_file(self, _):
        c = self._selected_contact_name()
        if not c: return
        wx.GetApp().send_file_to(c)
    def on_contact_activated(self, event):
        idx = self.lv.GetSelection()
        if idx == wx.NOT_FOUND:
            return
        contact = self._selected_contact_name()
        if contact not in self.contact_states:
            self._show_add_contact_prompt()
            return
        status = next((c["status"] for c in self._all_contacts if c["user"] == contact), "")
        urls = extract_urls(status)
        if urls:
            open_path_or_url(urls[0])
            return
        self.on_send(None)
    def receive_message(self, msg):
        wx.GetApp().play_sound("receive.wav");
        app = wx.GetApp(); is_logging_enabled = is_chat_logging_enabled(app.user_config, msg["from"])
        dlg = self.get_chat(msg["from"])
        if not dlg:
            is_contact = msg["from"] in self.contact_states
            dlg = ChatDialog(self, msg["from"], self.sock, self.user, is_logging_enabled, is_contact=is_contact)
        dlg.Show()
        dlg.append(msg["msg"], msg["from"], msg["time"])
        if app.user_config.get('tts_enabled', True):
            speak_text(f"{msg['from']}: {msg['msg']}")
    def on_typing_event(self, msg):
        from_user = msg.get("from")
        is_typing = bool(msg.get("typing", False))
        chat = self.get_chat(from_user)
        if chat:
            chat.set_typing_state(from_user, is_typing)
    def on_message_failed(self, to, reason): chat_dlg = self.get_chat(to); (chat_dlg.append_error(reason) if chat_dlg else wx.MessageBox(reason, "Message Failed", wx.OK | wx.ICON_ERROR))
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
        if event.GetKeyCode() == wx.WXK_F1:
            open_help_docs_for_context("admin", self)
        elif event.GetKeyCode() == wx.WXK_ESCAPE: self.Close()
        else: event.Skip()
    def on_send(self, _):
        cmd = self.input_ctrl.GetValue().strip()
        if not cmd: return
        if not cmd.startswith('/'): self.append_response("Error: Commands must start with /"); return
        msg = {"action":"admin_cmd", "cmd": cmd[1:]}; self.sock.sendall(json.dumps(msg).encode()+b"\n"); self.input_ctrl.Clear(); self.input_ctrl.SetFocus()
    def append_response(self, text):
        ts = time.time(); idx = self.hist.GetItemCount(); self.hist.InsertItem(idx, text); self.hist.SetItem(idx, 1, format_timestamp(ts))
        if text.lower().startswith('error'): self.hist.SetItemTextColour(idx, wx.RED)

class ChatDialog(wx.Dialog):
    def __init__(self, parent, contact, sock, user, logging_enabled=False, is_contact=True):
        super().__init__(parent, title=f"Chat with {contact}", size=(450, 450))
        self.contact, self.sock, self.user = contact, sock, user
        self.is_contact = bool(is_contact)
        self._pending_message_after_add = None
        self.Bind(wx.EVT_CHAR_HOOK, self.on_key)
        self.Bind(wx.EVT_CLOSE, self.on_close)
        self.Bind(wx.EVT_SHOW, self.on_show_dialog)
        self.Bind(wx.EVT_ACTIVATE, self.on_activate_dialog)
        self._sent_typing = False
        self._typing_timer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, self.on_typing_timeout, self._typing_timer)

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
        if self.is_contact: self.btn_add_contact.Hide()
        self.logging_enabled = bool(logging_enabled)
        self.hist = wx.ListBox(self, style=wx.LB_SINGLE)
        self._history_rows = []
        self.hist.Bind(wx.EVT_LISTBOX_DCLICK, self.on_history_item_activated)
        self.hist.Bind(wx.EVT_KEY_DOWN, self.on_history_key)
        self.typing_lbl = wx.StaticText(self, label="")
        self.typing_lbl.SetForegroundColour(wx.Colour(120, 180, 255))
        box_msg = wx.StaticBoxSizer(wx.VERTICAL, self, "Type &message")
        self.input_ctrl = wx.TextCtrl(box_msg.GetStaticBox(), style=wx.TE_MULTILINE | wx.TE_PROCESS_ENTER)
        self._consume_next_text_enter = False
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
        s.Add(self.typing_lbl, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 6)
        self.input_ctrl.Bind(wx.EVT_KEY_DOWN, self.on_input_key)
        self.input_ctrl.Bind(wx.EVT_CHAR_HOOK, self.on_input_key)
        self.input_ctrl.Bind(wx.EVT_TEXT_ENTER, self.on_text_enter)
        self.input_ctrl.Bind(wx.EVT_TEXT, self.on_input_text)
        box_msg.Add(self.input_ctrl, 1, wx.EXPAND|wx.ALL, 5)
        s.Add(box_msg, 1, wx.EXPAND|wx.ALL, 5)

        btn.Bind(wx.EVT_BUTTON, self.on_send)
        btn_file.Bind(wx.EVT_BUTTON, self.on_send_file)
        btn_sizer = wx.BoxSizer(wx.HORIZONTAL)
        btn_sizer.Add(btn, 1, wx.EXPAND | wx.ALL, 5)
        btn_sizer.Add(btn_file, 1, wx.EXPAND | wx.ALL, 5)
        s.Add(btn_sizer, 0, wx.EXPAND|wx.ALL, 5)
        self.SetSizer(s)
        self._focus_input()
    def _focus_input(self):
        wx.CallAfter(self.input_ctrl.SetFocus)
        wx.CallLater(120, self.input_ctrl.SetFocus)
    def on_show_dialog(self, event):
        if event.IsShown():
            self._focus_input()
        event.Skip()
    def on_activate_dialog(self, event):
        if event.GetActive():
            self._focus_input()
        event.Skip()
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
            self._consume_next_text_enter = True
            if event.ControlDown():
                self.on_send_file(None)
                return
            elif event.CmdDown() or event.ShiftDown():
                self.input_ctrl.WriteText('\n')
                return
            else:
                # Keep chat input deterministic on macOS and Windows:
                # Enter sends, Cmd/Shift+Enter inserts newline, Ctrl+Enter sends file.
                self.on_send(None)
                return
        event.Skip()

    def on_text_enter(self, event):
        if self._consume_next_text_enter:
            self._consume_next_text_enter = False
            return
        self.on_send(None)
    def on_input_text(self, event):
        app = wx.GetApp()
        if app.user_config.get('typing_indicators', True):
            txt = self.input_ctrl.GetValue().strip()
            if txt and not self._sent_typing:
                try:
                    self.sock.sendall((json.dumps({"action": "typing", "to": self.contact, "typing": True}) + "\n").encode())
                    self._sent_typing = True
                except Exception:
                    pass
            if txt:
                self._typing_timer.Start(1500, oneShot=True)
            elif self._sent_typing:
                self._send_stop_typing()
        event.Skip()
    def _send_stop_typing(self):
        if not self._sent_typing:
            return
        try:
            self.sock.sendall((json.dumps({"action": "typing", "to": self.contact, "typing": False}) + "\n").encode())
        except Exception:
            pass
        self._sent_typing = False
    def on_typing_timeout(self, event):
        if self._sent_typing:
            self._send_stop_typing()
    def on_key(self, event):
        if event.GetKeyCode() == wx.WXK_F1:
            open_help_docs_for_context("chat", self)
        elif event.GetKeyCode() in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER):
            focused = wx.Window.FindFocus()
            is_in_input = False
            w = focused
            while w:
                if w is self.input_ctrl:
                    is_in_input = True
                    break
                w = w.GetParent()
            if is_in_input:
                if event.ControlDown():
                    self.on_send_file(None)
                elif event.CmdDown():
                    self.input_ctrl.WriteText('\n')
                else:
                    enter_action = wx.GetApp().user_config.get('enter_key_action', 'send')
                    if enter_action == 'newline':
                        self.input_ctrl.WriteText('\n')
                    else:
                        self.on_send(None)
                return
        elif event.CmdDown() and event.GetKeyCode() == ord(','):
            parent = self.GetParent()
            if parent and hasattr(parent, "on_settings"):
                wx.CallAfter(parent.on_settings, None)
        elif event.GetKeyCode() == wx.WXK_ESCAPE: self.Close()
        else: event.Skip()
    def on_send(self, _):
        txt = self.input_ctrl.GetValue().strip()
        if not txt: return
        if not self.is_contact:
            res = wx.MessageBox(
                f"{self.contact} is not in your contacts.\n\nAdd this user to contacts before sending?",
                "Add Contact First",
                wx.YES_NO | wx.ICON_QUESTION,
                self,
            )
            if res != wx.YES:
                return
            self._pending_message_after_add = txt
            self.on_add_contact(None)
            return
        self._send_stop_typing()
        ts = datetime.datetime.now().isoformat()
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
    def send_pending_after_contact_added(self):
        if not self._pending_message_after_add:
            return
        pending = self._pending_message_after_add
        self._pending_message_after_add = None
        self.input_ctrl.SetValue(pending)
        self.on_send(None)
    def append(self, text, sender, ts, is_error=False):
        formatted_time = format_timestamp(ts)
        prefix = "Error" if is_error else sender
        display = f"[{formatted_time}] {prefix}: {text}"
        self.hist.Append(display)
        self._history_rows.append({"sender": sender, "text": text, "time": ts, "error": is_error})
        self.hist.SetSelection(self.hist.GetCount() - 1)
        app = wx.GetApp()
        if sender not in (self.user, "System") and app.user_config.get('read_messages_aloud', False):
            speak_text(f"{sender} says {text}")
        if self.logging_enabled:
            log_line = f"[{formatted_time}] {sender}: {text}\n"
            self._save_message_to_log(log_line)
    def append_error(self, reason):
        ts = time.time()
        self.append(reason, "System", ts, is_error=True)
        self.input_ctrl.SetFocus()

    def on_history_item_activated(self, event):
        idx = self.hist.GetSelection()
        if idx == wx.NOT_FOUND or idx < 0:
            return
        if idx >= len(self._history_rows):
            return
        message_text = self._history_rows[idx].get("text", "")
        urls = extract_urls(message_text)
        if not urls:
            return
        if len(urls) == 1:
            open_path_or_url(urls[0])
            return
        chosen = urls[0]
        # Keep interaction simple for screen-reader flow: open first URL and notify user.
        wx.MessageBox(f"Multiple links found. Opening first link:\n{chosen}", "Open Link", wx.OK | wx.ICON_INFORMATION)
        open_path_or_url(chosen)
    def on_history_key(self, event):
        if event.GetKeyCode() in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER):
            idx = self.hist.GetSelection()
            if idx != wx.NOT_FOUND:
                self.on_history_item_activated(event)
            return
        event.Skip()
    def set_typing_state(self, username, is_typing):
        app = wx.GetApp()
        if not app.user_config.get('typing_indicators', True):
            self.typing_lbl.SetLabel("")
            return
        if is_typing:
            self.typing_lbl.SetLabel(f"{username} is typing...")
            if app.user_config.get('announce_typing', True):
                speak_text(f"{username} started typing")
        else:
            self.typing_lbl.SetLabel("")
            if app.user_config.get('announce_typing', True):
                speak_text(f"{username} stopped typing")
    def on_close(self, event):
        self._send_stop_typing()
        event.Skip()

def main():
    app = ClientApp(False); app.MainLoop()

if __name__ == "__main__": main()
