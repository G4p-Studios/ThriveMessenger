"""
XMPP client wrapper for Thrive Messenger.

Wraps slixmpp with an asyncio event loop running in a background thread.
Provides a synchronous API for the wxPython UI, dispatching incoming events
via caller-provided callbacks (typically wrapped in wx.CallAfter).
"""

import asyncio
import os
import re
import tempfile
import urllib.parse
import urllib.request

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
import threading
import time
import logging
from datetime import datetime, timezone

import aiohttp
import slixmpp
from slixmpp.exceptions import IqError, IqTimeout
from slixmpp.plugins.xep_0363 import UploadServiceNotFound
from slixmpp.plugins.xep_0454 import XEP_0454
from slixmpp.stanza import StreamFeatures
from slixmpp.xmlstream import ElementBase, register_stanza_plugin
from slixmpp.xmlstream.handler import CoroutineCallback
from slixmpp.xmlstream.matcher import MatchXPath

import omemo_plugin  # noqa: F401 — registers XEP_0384Impl with slixmpp

log = logging.getLogger(__name__)

# Bytes moved per read/write while encrypting or decrypting.  Memory use is
# a small multiple of this, not of the file size.
CRYPTO_CHUNK_SIZE = 1024 * 1024

# XEP-0454 appends a 16-byte AES-GCM authentication tag to the ciphertext.
_GCM_TAG_BYTES = 16


class _CredentialRedactingFilter(logging.Filter):
    """Strip secrets out of slixmpp's raw stanza logging.

    slixmpp logs whole stanzas at DEBUG ("SEND: ...", "RECV: ..."), and
    several of them carry credentials in the clear:

    * the SASL ``<auth/>`` payload is base64 of ``\\0user\\0password``
    * in-band registration and password reset send ``<password>``
    * verify and reset send the emailed one-time ``<code>``
    * HTTP upload slots carry a bearer token in an Authorization header

    Anyone turning debug logging on is usually about to paste the output
    into a bug report, so redact at the source rather than trusting the
    log to stay private.
    """

    _SASL_NS = "urn:ietf:params:xml:ns:xmpp-sasl"

    # Each pattern keeps the opening tag and drops the element's text.
    _PATTERNS = (
        # SASL handshake: auth/response/challenge/success payloads.
        re.compile(
            r"(<(?:auth|response|challenge|success)\b[^>]*"
            + re.escape(_SASL_NS)
            + r"[^>]*>)[^<]+(?=</)",
            re.IGNORECASE,
        ),
        # Passwords: jabber:iq:register, urn:thrive:reset.
        re.compile(r"(<password\b[^>]*>)[^<]+(?=</)", re.IGNORECASE),
        # One-time codes: urn:thrive:verify, urn:thrive:reset.
        re.compile(r"(<code\b[^>]*>)[^<]+(?=</)", re.IGNORECASE),
        # XEP-0363 upload slot bearer tokens.
        re.compile(
            r"(<header\b[^>]*name=['\"]Authorization['\"][^>]*>)[^<]+(?=</)",
            re.IGNORECASE,
        ),
    )

    @classmethod
    def scrub(cls, value):
        """Redact secrets in *value*, which may be a stanza or a string."""
        text = value if isinstance(value, str) else str(value)
        for pattern in cls._PATTERNS:
            text = pattern.sub(r"\1[redacted]", text)
        return text

    def filter(self, record):
        if isinstance(record.args, tuple):
            record.args = tuple(self.scrub(a) for a in record.args)
        elif record.args:
            record.args = self.scrub(record.args)
        if isinstance(record.msg, str) and "<" in record.msg:
            record.msg = self.scrub(record.msg)
        return True


def encrypt_to_tempfile(src_path, chunk_size=CRYPTO_CHUNK_SIZE):
    """Encrypt *src_path* to a temporary file, XEP-0454 style.

    Returns ``(ciphertext_path, fragment)`` where fragment is the 88 hex
    chars (12-byte IV then 32-byte key) that go in the aesgcm:// URL.

    Streams in fixed-size chunks, so a 2 GB file costs the same memory as a
    2 MB one.  slixmpp's XEP_0454.encrypt builds the entire ciphertext as a
    bytes object and then copies it into a BytesIO, which is why it cannot
    be used for anything close to the server's size limit.

    The wire format is identical to slixmpp's: ciphertext followed by the
    16-byte GCM tag, so Conversations, Gajim, Dino and Monal read it.
    """
    iv = os.urandom(12)
    key = os.urandom(32)
    encryptor = Cipher(algorithms.AES(key), modes.GCM(iv)).encryptor()

    fd, cipher_path = tempfile.mkstemp(prefix="thrive-enc-")
    try:
        with os.fdopen(fd, "wb") as out, open(src_path, "rb") as src:
            while True:
                buf = src.read(chunk_size)
                if not buf:
                    break
                out.write(encryptor.update(buf))
            out.write(encryptor.finalize())
            out.write(encryptor.tag)
    except BaseException:
        _quiet_remove(cipher_path)
        raise

    return cipher_path, iv.hex() + key.hex()


class _GcmStreamDecryptor:
    """Push chunks in, get decrypted bytes written out.

    XEP-0454 puts the 16-byte GCM tag at the end of the stream, so a rolling
    window holds the last 16 bytes back instead of buffering everything to
    find it.  GCM is only authenticated by finalize_with_tag, which raises
    InvalidTag if anything was altered -- so callers must not expose the
    output until finish() returns.
    """

    def __init__(self, out_file, fragment):
        if len(fragment) != 88:
            raise ValueError(
                "Encrypted file link is malformed (bad key length).")
        iv = bytes.fromhex(fragment[:24])
        key = bytes.fromhex(fragment[24:])
        self._decryptor = Cipher(algorithms.AES(key), modes.GCM(iv)).decryptor()
        self._out = out_file
        self._tail = b""

    def feed(self, chunk):
        data = self._tail + chunk
        if len(data) > _GCM_TAG_BYTES:
            self._out.write(self._decryptor.update(data[:-_GCM_TAG_BYTES]))
            self._tail = data[-_GCM_TAG_BYTES:]
        else:
            self._tail = data

    def finish(self):
        if len(self._tail) != _GCM_TAG_BYTES:
            raise ValueError("Encrypted file is truncated.")
        self._out.write(self._decryptor.finalize_with_tag(self._tail))


def decrypt_stream(read_chunk, out_file, fragment, chunk_size=CRYPTO_CHUNK_SIZE):
    """Decrypt a XEP-0454 stream into *out_file*.

    *read_chunk* is a callable returning up to n bytes, or b"" at the end --
    a file object's ``.read`` or an equivalent over a network response.
    """
    decryptor = _GcmStreamDecryptor(out_file, fragment)
    while True:
        buf = read_chunk(chunk_size)
        if not buf:
            break
        decryptor.feed(buf)
    decryptor.finish()


def _quiet_remove(path):
    try:
        os.remove(path)
    except OSError:
        pass


def _human_size(num):
    """Format a byte count for a message a user will read."""
    for unit in ("B", "KB", "MB", "GB"):
        if abs(num) < 1024 or unit == "GB":
            return f"{num:.0f} {unit}" if unit == "B" else f"{num:.1f} {unit}"
        num /= 1024
    return f"{num:.1f} GB"


def is_encrypted_url(url):
    """True if *url* is an XEP-0454 aesgcm:// URI."""
    return str(url).startswith("aesgcm://")


def download_file(url, save_path, timeout=300):
    """Download *url* to *save_path*, decrypting XEP-0454 aesgcm:// URIs.

    Blocking, for callers already on a worker thread.  Plain https:// URLs
    are fetched unchanged so files from older Thrive builds and from other
    XMPP clients still arrive.

    The decryption is deliberately not streamed: AES-GCM is only
    authenticated once the tag at the end has been checked, so writing
    plaintext to disk before then would hand the user bytes an attacker
    could have tampered with.
    """
    url = str(url)
    if not is_encrypted_url(url):
        urllib.request.urlretrieve(url, save_path)
        return save_path

    # aesgcm://host/path#<24 hex iv><64 hex key>
    parsed = urllib.parse.urlparse(url)
    fragment = parsed.fragment
    https_url = urllib.parse.urlunparse(
        ("https",) + tuple(parsed[1:5]) + ("",))

    # Decrypt into a temp file alongside the destination, then move it into
    # place only once the tag verifies.  GCM cannot authenticate until the
    # end, so the destination must never hold bytes we have not checked.
    fd, part_path = tempfile.mkstemp(
        prefix=".thrive-part-", dir=os.path.dirname(save_path) or None)
    try:
        with os.fdopen(fd, "wb") as out:
            with urllib.request.urlopen(https_url, timeout=timeout) as resp:
                decrypt_stream(resp.read, out, fragment)
        os.replace(part_path, save_path)
    except BaseException:
        _quiet_remove(part_path)
        raise
    return save_path


def _install_stanza_redaction():
    """Attach the redactor wherever raw stanzas are logged.

    Filters on a logger run in Logger.handle() before records propagate to
    ancestor handlers, so attaching here covers every handler downstream --
    including one the application installs later, or slixmpp debug logging
    switched on by something other than connect().
    """
    for name in ("slixmpp.xmlstream.xmlstream", "slixmpp"):
        logger = logging.getLogger(name)
        if not any(isinstance(f, _CredentialRedactingFilter)
                   for f in logger.filters):
            logger.addFilter(_CredentialRedactingFilter())


_install_stanza_redaction()

# Stream feature mod_thrive_reset advertises to unauthenticated sessions.
# Its presence means the server will answer verify/reset IQs before login.
THRIVE_PREAUTH_NS = "urn:thrive:preauth"


class ThrivePreauthFeature(ElementBase):
    """<preauth xmlns="urn:thrive:preauth"/> inside <stream:features/>."""

    name = "preauth"
    namespace = THRIVE_PREAUTH_NS
    plugin_attrib = "thrive_preauth"
    interfaces = set()


register_stanza_plugin(StreamFeatures, ThrivePreauthFeature)

# Run after STARTTLS but before SASL (slixmpp orders mechanisms at 100).
_PREAUTH_FEATURE_ORDER = 50

# Map Thrive status names to XMPP presence show values.
# XMPP show values: None (available/online), "away", "xa", "dnd", "chat"
_STATUS_TO_SHOW = {
    "online": "",
    "away": "away",
    "busy": "dnd",
    "on the phone": "dnd",
    "doing homework": "away",
    "in the shower": "xa",
    "watching TV": "away",
    "hiding from the parents": "xa",
    "fixing my PC": "away",
    "battery about to die": "away",
}

# Reverse map: XMPP show value -> Thrive status, for contacts that publish a
# show but no status text.  "" is a bare <presence/>, i.e. plain available.
_SHOW_TO_STATUS = {
    "": "online",
    "chat": "online",
    "away": "away",
    "xa": "away",
    "dnd": "busy",
}


class XMPPClient:
    """Thin wrapper around slixmpp.ClientXMPP.

    * Manages an asyncio event loop in a daemon thread.
    * Exposes synchronous helpers the wx UI can call from the main thread.
    * Fires callbacks for incoming events (messages, presence, roster, etc.).
    """

    def __init__(self, server_host, server_port, domain):
        self._server_host = server_host
        self._server_port = server_port
        self._domain = domain

        self._client: slixmpp.ClientXMPP | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._connected_event = threading.Event()
        self._connect_error: str | None = None
        self._username: str = ""
        self._intentional_disconnect = False

        # ---- Callbacks (set by the UI before calling connect) ----
        # All callbacks are invoked from the asyncio thread, so the UI
        # should wrap them in wx.CallAfter() when setting them.
        self.on_message = None           # (from_user: str, body: str, timestamp: str)
        self.on_presence = None          # (from_user: str, online: bool, status_text: str)
        self.on_roster_loaded = None     # (contacts: list[dict])
        self.on_connected = None         # ()
        self.on_disconnected = None      # (reason: str)
        self.on_connection_failed = None # (reason: str)
        self.on_chat_state = None        # (from_user: str, state: str)  "composing"/"paused"/"active"
        self.on_receipt = None           # (msg_id: str, from_user: str)
        self.on_mam_messages = None      # (messages: list[dict])
        self.on_server_info = None       # (info: dict)
        self.on_user_directory = None    # (users: list[dict])
        self.on_file_message = None      # (from_user: str, files: list[dict])
        self.on_file_uploaded = None     # (to: str, files: list[dict])  upload complete, message sent
        self.on_file_upload_error = None # (to: str, error: str)
        self.on_admin_response = None   # (response: str)
        self.on_server_alert = None    # (message: str)

    # ------------------------------------------------------------------
    # Public API (called from the wx main thread)
    # ------------------------------------------------------------------

    def connect(self, username, password, timeout=15):
        """Connect and authenticate.  Blocks until session starts or fails.

        Returns (True, "") on success, or (False, reason) on failure.
        """
        self._username = username
        self._password = password
        self._intentional_disconnect = False
        self._connected_event.clear()
        self._connect_error = None

        # Stream-level debug logging prints every stanza, including the SASL
        # PLAIN <auth/> element -- which is the user's password in base64.
        # Never on by default: it lands in the console and in anything the
        # user copies out of it.  Opt in with THRIVE_XMPP_DEBUG=1.
        if os.environ.get("THRIVE_XMPP_DEBUG"):
            logging.basicConfig(
                level=logging.DEBUG, format="%(name)s %(levelname)s: %(message)s")
            logging.getLogger("slixmpp").setLevel(logging.DEBUG)
            log.warning(
                "XMPP debug logging is on; output contains your password.")

        # Start the asyncio loop in a background thread.
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

        # Create the client and connect on the asyncio loop so that
        # slixmpp binds to the correct event loop.
        asyncio.run_coroutine_threadsafe(
            self._async_connect(), self._loop
        )

        # Block until we get session_start or a failure signal.
        self._connected_event.wait(timeout=timeout)
        if self._connect_error:
            self._shutdown_loop()
            return False, self._connect_error
        if not self._connected_event.is_set():
            self._shutdown_loop()
            return False, "Connection timed out."
        return True, ""

    def disconnect(self, timeout=5):
        """Gracefully disconnect, waiting for the stream to actually close.

        The wait is the point: without it the loop was torn down in the next
        breath, so the unavailable presence and stream close never went out.
        The server kept the session until the TCP connection died with the
        process, which left a stale resource online after every logout and
        meant contacts saw us online long after we had gone.
        """
        self._intentional_disconnect = True
        if self._client and self._loop:
            future = asyncio.run_coroutine_threadsafe(
                self._async_disconnect(), self._loop
            )
            try:
                future.result(timeout=timeout)
            except Exception as exc:
                log.warning("Graceful disconnect failed: %s", exc)
        self._shutdown_loop()

    def send_message(self, to_username, body):
        """Send an OMEMO-encrypted 1-to-1 chat message."""
        if not self._client or not self._loop:
            return
        asyncio.run_coroutine_threadsafe(
            self._async_send_encrypted(to_username, body), self._loop
        )

    def set_status(self, status_text=""):
        """Send a presence update.

        Maps Thrive status strings to XMPP show values.
        If *status_text* is "offline", sends unavailable presence.
        """
        if not self._client:
            return
        if status_text.lower() == "offline":
            pres = self._client.make_presence(ptype="unavailable")
            pres.send()
            return

        show = _STATUS_TO_SHOW.get(status_text.lower())
        # For custom statuses not in the map, use "away" with the text.
        if show is None:
            show = "away"

        pres = self._client.make_presence(pshow=show or None, pstatus=status_text)
        pres.send()

    def add_contact(self, username):
        """Send a roster add and presence subscription request."""
        if not self._client or not self._loop:
            return
        jid = f"{username}@{self._domain}"
        asyncio.run_coroutine_threadsafe(
            self._async_add_contact(jid), self._loop
        )

    def remove_contact(self, username):
        """Remove a contact from the roster."""
        if not self._client or not self._loop:
            return
        jid = f"{username}@{self._domain}"
        asyncio.run_coroutine_threadsafe(
            self._async_remove_contact(jid), self._loop
        )

    def request_roster(self):
        """Request the full roster from the server."""
        if not self._client or not self._loop:
            return
        asyncio.run_coroutine_threadsafe(
            self._async_get_roster(), self._loop
        )

    @property
    def username(self):
        return self._username

    @property
    def domain(self):
        return self._domain

    @property
    def is_connected(self):
        return self._client is not None and self._client.is_connected()

    # ------------------------------------------------------------------
    # Phase 3: Typing, receipts, MAM, user directory, server info
    # ------------------------------------------------------------------

    def send_chat_state(self, to_username, state="composing"):
        """Send a chat state notification (XEP-0085).

        *state* should be one of: "composing", "paused", "active", "inactive", "gone".
        """
        if not self._client:
            return
        to_jid = f"{to_username}@{self._domain}"
        msg = self._client.make_message(mto=to_jid, mtype="chat")
        msg["chat_state"] = state
        msg.send()

    def query_mam(self, since=None, max_results=200):
        """Query the Message Archive (XEP-0313) for recent messages.

        *since* is an ISO-8601 datetime string.  If None, queries the
        last 24 hours.  Results arrive via the ``on_mam_messages`` callback.
        """
        if not self._client or not self._loop:
            return
        asyncio.run_coroutine_threadsafe(
            self._async_query_mam(since, max_results), self._loop
        )

    def get_server_info(self):
        """Query server version (XEP-0092) and disco info.

        Results arrive via the ``on_server_info`` callback.
        """
        if not self._client or not self._loop:
            return
        asyncio.run_coroutine_threadsafe(
            self._async_get_server_info(), self._loop
        )

    def get_user_directory(self):
        """Query the user directory (custom IQ: urn:thrive:directory).

        Results arrive via the ``on_user_directory`` callback.
        """
        if not self._client or not self._loop:
            return
        asyncio.run_coroutine_threadsafe(
            self._async_get_user_directory(), self._loop
        )

    # ------------------------------------------------------------------
    # Phase 4: File transfers (HTTP Upload, XEP-0363)
    # ------------------------------------------------------------------

    def send_files(self, to_username, file_paths):
        """Upload files via HTTP Upload and send a file message.

        Runs asynchronously.  Fires ``on_file_uploaded`` on success or
        ``on_file_upload_error`` on failure.
        """
        if not self._client or not self._loop:
            return
        asyncio.run_coroutine_threadsafe(
            self._async_send_files(to_username, file_paths), self._loop
        )

    def download_file(self, url, save_dir, filename=None):
        """Download a file from *url* and save to *save_dir*.

        Runs asynchronously.  Returns via callback or can be awaited
        internally.  Returns (save_path, error).
        """
        if not self._loop:
            return
        asyncio.run_coroutine_threadsafe(
            self._async_download_file(url, save_dir, filename), self._loop
        )

    # ------------------------------------------------------------------
    # Phase 7: Admin commands (custom IQ: urn:thrive:admin)
    # ------------------------------------------------------------------

    def send_admin_command(self, cmd_string):
        """Send an admin command to the server via custom IQ.

        *cmd_string* is the raw command text (without the leading '/').
        The server response arrives via the ``on_admin_response`` callback.
        """
        if not self._client or not self._loop:
            return
        asyncio.run_coroutine_threadsafe(
            self._async_send_admin_command(cmd_string), self._loop
        )

    # ------------------------------------------------------------------
    # Phase 2: Registration, password, blocking
    # ------------------------------------------------------------------

    def register(self, username, password, email="", timeout=15):
        """Register a new account via XEP-0077 in-band registration.

        Blocks until the server responds.
        Returns (True, response_dict) on success, (False, reason) on failure.
        The response_dict may contain ``"verify_pending": True`` if the server
        requires email verification.
        """
        jid = f"{username}@{self._domain}"
        result = {"success": False, "reason": "", "verify_pending": False}
        done = threading.Event()
        host, port, domain = self._server_host, self._server_port, self._domain

        def _thread_fn():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            client = slixmpp.ClientXMPP(jid, password)
            client.register_plugin("xep_0030")
            client.register_plugin("xep_0004")
            client.register_plugin("xep_0066")
            client.register_plugin("xep_0077", {"create_account": True})

            async def _on_register(form):
                """Called during stream features when server offers registration."""
                try:
                    iq = client.make_iq_set()
                    iq['to'] = domain
                    iq.enable('register')
                    iq['register']['username'] = username
                    iq['register']['password'] = password
                    if email:
                        iq['register']['email'] = email
                    await iq.send()
                    result["success"] = True
                except IqError as err:
                    condition = err.iq["error"]["condition"]
                    text = err.iq["error"].get("text", "")
                    if condition == "not-acceptable" and "verif" in text.lower():
                        result["success"] = True
                        result["verify_pending"] = True
                    else:
                        result["reason"] = text or condition
                except IqTimeout:
                    result["reason"] = "Request timed out."
                except Exception as exc:
                    result["reason"] = str(exc)
                finally:
                    client.disconnect()
                    done.set()

            client.add_event_handler("register", _on_register)
            client.enable_direct_tls = False
            client.enable_starttls = True
            client.connect(host=host, port=port)
            loop.run_forever()

        thread = threading.Thread(target=_thread_fn, daemon=True)
        thread.start()
        done.wait(timeout=timeout)
        if not done.is_set():
            result["reason"] = "Registration timed out."

        if result["success"]:
            return True, result
        return False, result["reason"]

    def verify_account(self, username, code, timeout=15):
        """Send a verification code to the server (custom IQ).

        Returns (True, "") on success, (False, reason) on failure.
        """
        result = {"success": False, "reason": ""}
        done = threading.Event()

        async def _do_verify():
            try:
                iq = self._client.make_iq_set(ito=self._domain)
                query = slixmpp.ET.SubElement(
                    iq.xml, "{urn:thrive:verify}verify"
                )
                # Namespaced children — see _oneshot_iq_call for why.
                slixmpp.ET.SubElement(
                    query, "{urn:thrive:verify}username").text = username
                slixmpp.ET.SubElement(
                    query, "{urn:thrive:verify}code").text = code
                await iq.send(timeout=max(timeout - 2, 1))
                result["success"] = True
            except IqError as err:
                result["reason"] = err.iq["error"].get("text", "Verification failed.")
            except IqTimeout:
                result["reason"] = "Request timed out."
            except Exception as exc:
                result["reason"] = str(exc)
            finally:
                done.set()

        if self._client and self._loop:
            asyncio.run_coroutine_threadsafe(_do_verify(), self._loop)
            done.wait(timeout=timeout)
        else:
            # Use a one-shot connection for pre-login verification.
            ok, payload = self._oneshot_iq_call(
                "urn:thrive:verify", "verify",
                {"username": username, "code": code},
                timeout=timeout
            )
            return (True, "") if ok else (False, payload)
        if not done.is_set():
            return False, "Verification timed out."
        return (True, "") if result["success"] else (False, result["reason"])

    def request_password_reset(self, identifier, timeout=15):
        """Request a password reset code (custom IQ).

        Succeeds only when a code is actually waiting in the user's inbox,
        so the caller never sends someone off to find a code that was never
        generated.

        Returns (True, username_hint) on success, (False, reason) on failure.
        """
        ok, payload = self._oneshot_iq_call(
            "urn:thrive:reset", "request",
            {"identifier": identifier},
            timeout=timeout
        )
        if not ok:
            return False, payload

        # Servers predating the status element only reply with <user>.
        status = payload.get("status") or (
            "sent" if payload.get("user") else "unavailable"
        )
        if status == "unavailable":
            return False, (
                "No reset code could be sent. Either that account does not "
                "exist, or it has no email address on record."
            )
        return True, payload.get("user", "")

    def reset_password(self, username, code, new_password, timeout=15):
        """Confirm a password reset with code and new password (custom IQ).

        Returns (True, "") on success, (False, reason) on failure.
        """
        ok, payload = self._oneshot_iq_call(
            "urn:thrive:reset", "confirm",
            {"username": username, "code": code, "password": new_password},
            timeout=timeout
        )
        return (True, "") if ok else (False, payload)

    def change_password(self, new_password, timeout=15):
        """Change password for the currently logged-in user (XEP-0077).

        Returns (True, "") on success, (False, reason) on failure.
        """
        if not self._client or not self._loop:
            return False, "Not connected."

        result = {"success": False, "reason": ""}
        done = threading.Event()

        async def _do_change():
            try:
                reg = self._client.plugin["xep_0077"]
                await reg.change_password(new_password)
                result["success"] = True
            except IqError as err:
                result["reason"] = err.iq["error"].get("text", "Password change failed.")
            except IqTimeout:
                result["reason"] = "Request timed out."
            except Exception as exc:
                result["reason"] = str(exc)
            finally:
                done.set()

        asyncio.run_coroutine_threadsafe(_do_change(), self._loop)
        done.wait(timeout=timeout)
        if not done.is_set():
            return False, "Password change timed out."
        return (True, "") if result["success"] else (False, result["reason"])

    def block_contact(self, username):
        """Block a contact via XEP-0191."""
        if not self._client or not self._loop:
            return
        jid = f"{username}@{self._domain}"
        asyncio.run_coroutine_threadsafe(
            self._async_block(jid), self._loop
        )

    def unblock_contact(self, username):
        """Unblock a contact via XEP-0191."""
        if not self._client or not self._loop:
            return
        jid = f"{username}@{self._domain}"
        asyncio.run_coroutine_threadsafe(
            self._async_unblock(jid), self._loop
        )

    # ------------------------------------------------------------------
    # Asyncio internals
    # ------------------------------------------------------------------

    def _run_loop(self):
        """Target for the background thread — runs the asyncio event loop."""
        loop = self._loop
        asyncio.set_event_loop(loop)
        try:
            loop.run_forever()
        finally:
            # Cancel what slixmpp and OMEMO left running (keepalives, key
            # rotation) before closing.  Without this, shutdown prints
            # "Task was destroyed but it is pending" and an "Event loop is
            # closed" traceback from XMLStream.__del__.
            try:
                pending = asyncio.all_tasks(loop)
                for task in pending:
                    task.cancel()
                if pending:
                    loop.run_until_complete(
                        asyncio.gather(*pending, return_exceptions=True))
            except Exception as exc:
                log.debug("Error draining tasks at shutdown: %s", exc)
            finally:
                loop.close()

    async def _async_connect(self):
        """Create the client and connect (called on the asyncio loop).

        The ClientXMPP must be created here — on the background event loop —
        so that slixmpp's internal futures are bound to the correct loop.
        """
        try:
            jid = f"{self._username}@{self._domain}"
            self._client = slixmpp.ClientXMPP(jid, self._password)

            # Register plugins.
            self._client.register_plugin("xep_0030")  # Service Discovery
            self._client.register_plugin("xep_0077")  # In-Band Registration
            self._client.register_plugin("xep_0085")  # Chat State Notifications
            self._client.register_plugin("xep_0092")  # Software Version
            self._client.register_plugin("xep_0184")  # Message Delivery Receipts
            self._client.register_plugin("xep_0191")  # Blocking Command
            self._client.register_plugin("xep_0199", {  # Ping + keepalive
                "keepalive": True, "interval": 60, "timeout": 15,
            })
            self._client.register_plugin("xep_0313")  # Message Archive Management
            self._client.register_plugin("xep_0363")  # HTTP File Upload
            self._client.register_plugin("xep_0454")  # OMEMO Media Sharing
            self._client.register_plugin("xep_0380")  # Explicit Message Encryption

            # OMEMO (XEP-0384) — per-user key storage.
            omemo_dir = os.path.join(os.path.expanduser("~"), ".thrive_messenger")
            os.makedirs(omemo_dir, exist_ok=True)
            omemo_path = os.path.join(omemo_dir, f"omemo_{self._username}.json")
            self._client.register_plugin(
                "xep_0384",
                {"json_file_path": omemo_path},
                module=omemo_plugin,
            )

            # Event handlers.
            self._client.add_event_handler("session_start", self._on_session_start)
            self._client.register_handler(CoroutineCallback(
                "ThriveOMEMOMessage",
                MatchXPath(f"{{{self._client.default_ns}}}message"),
                self._on_message_omemo,
            ))
            self._client.add_event_handler("changed_status", self._on_presence_changed)
            self._client.add_event_handler("got_offline", self._on_got_offline)
            self._client.add_event_handler(
                "roster_subscription_request", self._on_subscription_request)
            self._client.add_event_handler(
                "roster_subscription_authorized", self._on_subscription_authorized)
            self._client.add_event_handler("disconnected", self._on_disconnected)
            self._client.add_event_handler("connection_failed", self._on_connection_failed)
            self._client.add_event_handler("failed_auth", self._on_failed_auth)
            self._client.add_event_handler("chatstate_composing", self._on_chatstate_composing)
            self._client.add_event_handler("chatstate_paused", self._on_chatstate_paused)
            self._client.add_event_handler("chatstate_active", self._on_chatstate_active)
            self._client.add_event_handler("receipt_received", self._on_receipt_received)

            # Debug: log connection lifecycle events.
            self._client.add_event_handler("connected", self._on_tcp_connected)
            self._client.add_event_handler("tls_success", self._on_tls_success)
            self._client.add_event_handler("tls_failed", self._on_tls_failed)

            # Port 5222 uses STARTTLS (not direct TLS).
            self._client.enable_direct_tls = False
            self._client.enable_starttls = True

            log.info("Connecting to %s:%s (domain=%s)",
                     self._server_host, self._server_port, self._domain)
            self._client.connect(
                host=self._server_host,
                port=self._server_port,
            )
        except Exception as exc:
            self._connect_error = f"Connection error: {type(exc).__name__}: {exc}"
            self._connected_event.set()

    async def _async_disconnect(self):
        # Tell contacts we are going before closing, then await the close so
        # the caller can shut the loop down without cutting it short.
        try:
            self._client.send_presence(ptype="unavailable")
        except Exception as exc:
            log.debug("Could not send unavailable presence: %s", exc)
        await self._client.disconnect()

    async def _async_add_contact(self, jid):
        try:
            self._client.send_presence_subscription(pto=jid)
            await self._client.get_roster()
        except Exception as exc:
            log.warning("Failed to add contact %s: %s", jid, exc)

    async def _async_remove_contact(self, jid):
        try:
            self._client.send_presence_subscription(pto=jid, ptype="unsubscribe")
            self._client.update_roster(jid, subscription="remove")
        except Exception as exc:
            log.warning("Failed to remove contact %s: %s", jid, exc)

    async def _async_get_roster(self):
        try:
            await self._client.get_roster()
            self._deliver_roster()
        except Exception as exc:
            log.warning("Failed to get roster: %s", exc)

    async def _async_block(self, jid):
        try:
            block_plugin = self._client.plugin["xep_0191"]
            await block_plugin.block(jid)
        except Exception as exc:
            log.warning("Failed to block %s: %s", jid, exc)

    async def _async_unblock(self, jid):
        try:
            block_plugin = self._client.plugin["xep_0191"]
            await block_plugin.unblock(jid)
        except Exception as exc:
            log.warning("Failed to unblock %s: %s", jid, exc)

    async def _async_query_mam(self, since, max_results):
        """Query the message archive and deliver results."""
        try:
            mam = self._client.plugin["xep_0313"]
            if since is None:
                # Default: last 24 hours.
                since_dt = datetime.now(timezone.utc).replace(
                    hour=0, minute=0, second=0, microsecond=0
                )
                since = since_dt.isoformat()

            results = []
            async for rsm_response in mam.retrieve(
                start=since,
                iterator=True,
                rsm={"max": str(max_results)},
            ):
                for msg in rsm_response["mam"]["results"]:
                    forwarded = msg["mam_result"]["forwarded"]
                    message = forwarded["stanza"]
                    delay = forwarded["delay"]
                    body = message["body"]
                    if not body:
                        continue
                    from_jid = message["from"]
                    to_jid = message["to"]
                    from_user = slixmpp.JID(from_jid).user
                    to_user = slixmpp.JID(to_jid).user
                    # Skip our own outgoing messages.
                    if from_user == self._username:
                        continue
                    ts = delay["stamp"] if delay["stamp"] else datetime.now(timezone.utc).isoformat()
                    results.append({
                        "from": from_user,
                        "to": to_user,
                        "msg": str(body),
                        "time": str(ts),
                    })

            if self.on_mam_messages:
                self.on_mam_messages(results)
        except Exception as exc:
            log.warning("MAM query failed: %s", exc)

    async def _async_get_server_info(self):
        """Query server version, disco info, and user directory for stats."""
        try:
            info = {
                "hostname": self._domain,
                "port": self._server_port,
            }

            # XEP-0092: Software Version
            try:
                version = await self._client.plugin["xep_0092"].get_version(
                    self._domain, timeout=10
                )
                sv = version["software_version"]
                info["server_software"] = sv.get("name", "")
                info["server_version"] = sv.get("version", "")
                info["server_os"] = sv.get("os", "")
            except Exception:
                pass

            # XEP-0030: Disco info for feature list + file upload limit
            try:
                disco = await self._client.plugin["xep_0030"].get_info(
                    self._domain, timeout=10
                )
                features = [f for f in disco["disco_info"]["features"]]
                info["features"] = features

                # Extract max-file-size from the raw XML (XEP-0128 extended info).
                ns_data = "jabber:x:data"
                for x_form in disco.xml.iter(f"{{{ns_data}}}x"):
                    for field in x_form.iter(f"{{{ns_data}}}field"):
                        if field.get("var") == "max-file-size":
                            val_el = field.find(f"{{{ns_data}}}value")
                            if val_el is not None and val_el.text:
                                try:
                                    info["file_size_limit"] = int(val_el.text)
                                except (ValueError, TypeError):
                                    pass
            except Exception:
                pass

            # User directory: get total/online user counts.
            try:
                ns = "urn:thrive:directory"
                iq = self._client.make_iq_get(
                    queryxmlns=ns, ito=self._domain
                )
                resp = await iq.send(timeout=10)
                directory = resp.xml.find(f"{{{ns}}}directory")
                if directory is not None:
                    total = 0
                    online = 0
                    for user_el in directory.findall(f"{{{ns}}}user"):
                        total += 1
                        status = user_el.findtext(f"{{{ns}}}status", "offline")
                        if status != "offline":
                            online += 1
                    info["total_users"] = total
                    info["online_users"] = online
            except Exception:
                pass

            if self.on_server_info:
                self.on_server_info(info)
        except Exception as exc:
            log.warning("Server info query failed: %s", exc)

    async def _async_get_user_directory(self):
        """Query the user directory via custom IQ."""
        try:
            iq = self._client.make_iq_get(
                queryxmlns="urn:thrive:directory",
                ito=self._domain
            )
            resp = await iq.send(timeout=15)
            users = []
            ns = "urn:thrive:directory"
            directory = resp.xml.find(f"{{{ns}}}directory")
            if directory is not None:
                for user_el in directory.findall(f"{{{ns}}}user"):
                    username = user_el.findtext(f"{{{ns}}}username", "")
                    status = user_el.findtext(f"{{{ns}}}status", "offline")
                    is_admin = user_el.findtext(f"{{{ns}}}admin", "false") == "true"
                    users.append({
                        "user": username,
                        "status": status,
                        "is_admin": is_admin,
                    })

            if self.on_user_directory:
                self.on_user_directory(users)
        except Exception as exc:
            log.warning("User directory query failed: %s", exc)

    async def _async_send_admin_command(self, cmd_string):
        """Send an admin command via custom IQ and deliver the response."""
        try:
            iq = self._client.make_iq_set(ito=self._domain)
            cmd_el = slixmpp.ET.SubElement(
                iq.xml, "{urn:thrive:admin}command"
            )
            cmd_el.text = cmd_string
            resp = await iq.send(timeout=15)

            # Extract response text from the server reply.
            response_text = ""
            result_el = resp.xml.find("{urn:thrive:admin}response")
            if result_el is not None and result_el.text:
                response_text = result_el.text
            else:
                # Fallback: check direct children for text.
                for child in resp.xml:
                    if child.text:
                        response_text = child.text
                        break

            if self.on_admin_response:
                self.on_admin_response(response_text or "Command executed.")
        except IqError as err:
            error_text = err.iq["error"].get("text", "Command failed.")
            if self.on_admin_response:
                self.on_admin_response(f"Error: {error_text}")
        except IqTimeout:
            if self.on_admin_response:
                self.on_admin_response("Error: Command timed out.")
        except Exception as exc:
            log.warning("Admin command failed: %s", exc)
            if self.on_admin_response:
                self.on_admin_response(f"Error: {exc}")

    async def _async_send_files(self, to_username, file_paths):
        """Upload files via HTTP Upload and send a file message."""
        to_jid = f"{to_username}@{self._domain}"
        uploaded = []
        try:
            # xep_0454 encrypts with AES-256-GCM and hands the ciphertext to
            # xep_0363, so the server stores bytes it cannot read.  The key
            # and IV ride in the URL fragment, which HTTP never sends to a
            # server, and the URL itself travels inside the OMEMO-encrypted
            # message below.
            upload = self._client.plugin["xep_0363"]
            for fp in file_paths:
                filename = os.path.basename(fp)
                size = os.path.getsize(fp)
                content_type = self._guess_content_type(filename)

                # Encrypt to a temp file first, then hand that file to
                # xep_0363 so aiohttp streams it off disk.  Going through
                # xep_0454.upload_file instead would hold the whole
                # ciphertext in memory twice over.
                cipher_path, fragment = await asyncio.to_thread(
                    encrypt_to_tempfile, fp)
                try:
                    # A random stored name keeps the user's filename off the
                    # server; the extension is kept so other clients can tell
                    # what they received, as XEP-0454 specifies.
                    stored_name = os.urandom(12).hex()
                    ext = os.path.splitext(filename)[1]
                    if ext:
                        stored_name += XEP_0454.map_extensions(ext)

                    # upload_file discovers the service, checks the size
                    # against the limit it advertises, requests the slot and
                    # does the PUT.  request_slot alone cannot be used here:
                    # its first argument is the service JID, which only
                    # discovery can supply.
                    with open(cipher_path, "rb") as enc:
                        https_url = await upload.upload_file(
                            filename=stored_name,
                            size=os.path.getsize(cipher_path),
                            # Declaring the real type would tell the server
                            # what it is holding, which defeats the point.
                            content_type="application/octet-stream",
                            input_file=enc,
                        )
                finally:
                    _quiet_remove(cipher_path)

                get_url = XEP_0454.format_url(str(https_url), fragment)

                uploaded.append({
                    "filename": filename,
                    "size": size,
                    "url": str(get_url),
                    "content_type": content_type,
                })

            # Send a message with file metadata using OOB (XEP-0066)
            # for the first URL plus a custom element for multi-file.
            msg = self._client.make_message(mto=to_jid, mtype="chat")

            # Human-readable body as fallback.
            if len(uploaded) == 1:
                msg["body"] = uploaded[0]["url"]
            else:
                lines = [f['filename'] + ": " + f['url'] for f in uploaded]
                msg["body"] = "\n".join(lines)

            # OOB for the first file (standard interop).
            oob = slixmpp.ET.SubElement(
                msg.xml, "{jabber:x:oob}x"
            )
            # XEP-0066 puts these in jabber:x:oob.  Bare names serialise as
            # xmlns="", which no other client recognises -- the attachment
            # simply does not appear for anyone outside Thrive.
            slixmpp.ET.SubElement(
                oob, "{jabber:x:oob}url").text = uploaded[0]["url"]
            slixmpp.ET.SubElement(
                oob, "{jabber:x:oob}desc").text = uploaded[0]["filename"]

            # Custom element with full file list for Thrive clients.
            files_el = slixmpp.ET.SubElement(
                msg.xml, "{urn:thrive:files}files"
            )
            for f in uploaded:
                file_el = slixmpp.ET.SubElement(files_el, "file")
                slixmpp.ET.SubElement(file_el, "name").text = f["filename"]
                slixmpp.ET.SubElement(file_el, "size").text = str(f["size"])
                slixmpp.ET.SubElement(file_el, "url").text = f["url"]
                slixmpp.ET.SubElement(file_el, "content-type").text = f["content_type"]

            # Try to encrypt the file message with OMEMO.
            try:
                xep_0384 = self._client.plugin["xep_0384"]
                messages, _errors = await xep_0384.encrypt_message(
                    msg, {slixmpp.JID(to_jid)}
                )
                for namespace, encrypted_msg in messages.items():
                    encrypted_msg["eme"]["namespace"] = namespace
                    encrypted_msg["eme"]["name"] = self._client.plugin["xep_0380"].mechanisms.get(namespace, "OMEMO")
                    encrypted_msg.send()
            except Exception:
                # Fallback: send unencrypted.
                log.info("OMEMO encryption unavailable for file message, sending plaintext.")
                msg.send()

            if self.on_file_uploaded:
                self.on_file_uploaded(to_username, uploaded)

        except UploadServiceNotFound:
            # Has no __str__, so reporting it raw gives an empty message.
            log.warning("No HTTP upload service found on %s", self._domain)
            if self.on_file_upload_error:
                self.on_file_upload_error(
                    to_username,
                    "This server does not offer file uploads.",
                )
        except Exception as exc:
            log.warning("File upload failed: %s", exc)
            if self.on_file_upload_error:
                self.on_file_upload_error(
                    to_username, str(exc) or exc.__class__.__name__)

    async def _async_download_file(self, url, save_dir, filename=None):
        """Download a file and save it to disk, decrypting aesgcm:// URIs."""
        try:
            url = str(url)
            encrypted = is_encrypted_url(url)
            fragment = ""
            fetch_url = url
            if encrypted:
                parsed = urllib.parse.urlparse(url)
                fragment = parsed.fragment
                if len(fragment) != 88:
                    return None, "Encrypted file link is malformed."
                fetch_url = urllib.parse.urlunparse(
                    ("https",) + tuple(parsed[1:5]) + ("",))

            if not filename:
                # For an encrypted upload the URL holds the random stored
                # name, so callers should pass the real one from the message.
                path = urllib.parse.urlparse(fetch_url).path
                filename = urllib.parse.unquote(
                    os.path.basename(path)) or "download"

            os.makedirs(save_dir, exist_ok=True)
            save_path = os.path.join(save_dir, filename)

            # Handle duplicates.
            if os.path.exists(save_path):
                name, ext = os.path.splitext(filename)
                counter = 1
                while os.path.exists(save_path):
                    save_path = os.path.join(save_dir, f"{name} ({counter}){ext}")
                    counter += 1

            # Stream to a temp file beside the destination and move it into
            # place at the end, so nothing unverified is ever visible there.
            fd, part_path = tempfile.mkstemp(
                prefix=".thrive-part-", dir=save_dir)
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(fetch_url) as resp:
                        if resp.status != 200:
                            log.warning(
                                "Download failed (%d): %s", resp.status, fetch_url)
                            return None, f"Download failed ({resp.status})"
                        with os.fdopen(fd, "wb") as out:
                            if encrypted:
                                decryptor = _GcmStreamDecryptor(out, fragment)
                                async for chunk in resp.content.iter_chunked(
                                        CRYPTO_CHUNK_SIZE):
                                    decryptor.feed(chunk)
                                decryptor.finish()
                            else:
                                async for chunk in resp.content.iter_chunked(
                                        CRYPTO_CHUNK_SIZE):
                                    out.write(chunk)
                os.replace(part_path, save_path)
            except BaseException:
                _quiet_remove(part_path)
                raise

            return save_path, None
        except Exception as exc:
            log.warning("Download error: %s", exc)
            return None, str(exc)

    # ------------------------------------------------------------------
    # OMEMO encrypt / decrypt
    # ------------------------------------------------------------------

    async def _async_send_encrypted(self, to_username, body):
        """Encrypt and send a message via OMEMO."""
        to_jid = f"{to_username}@{self._domain}"
        try:
            xep_0384 = self._client.plugin["xep_0384"]

            msg = self._client.make_message(mto=to_jid, mtype="chat")
            msg["body"] = body

            messages, encryption_errors = await xep_0384.encrypt_message(
                msg, {slixmpp.JID(to_jid)}
            )

            if encryption_errors:
                log.info("OMEMO non-critical encryption errors: %s", encryption_errors)

            for namespace, encrypted_msg in messages.items():
                encrypted_msg["eme"]["namespace"] = namespace
                encrypted_msg["eme"]["name"] = self._client.plugin["xep_0380"].mechanisms.get(namespace, "OMEMO")
                encrypted_msg["request_receipt"] = True
                encrypted_msg.send()

        except Exception as exc:
            log.warning("OMEMO encryption failed, sending plaintext: %s", exc)
            # Fallback to plaintext if OMEMO fails (e.g. recipient has no keys).
            msg = self._client.make_message(mto=to_jid, mbody=body, mtype="chat")
            msg["request_receipt"] = True
            msg.send()

    async def _on_message_omemo(self, msg):
        """OMEMO-aware message handler (registered as CoroutineCallback)."""
        # Server alerts arrive as headline messages (e.g. broadcast from admin).
        if msg["type"] == "headline":
            body = msg["body"]
            log.info("Received headline message from %s: %s", msg["from"], body)
            if body and self.on_server_alert:
                self.on_server_alert(str(body))
            return

        if msg["type"] not in ("chat", "normal"):
            return

        try:
            xep_0384 = self._client.plugin["xep_0384"]
            namespace = xep_0384.is_encrypted(msg)

            if namespace is not None:
                # Decrypt the OMEMO message.
                try:
                    decrypted_msg, device_info = await xep_0384.decrypt_message(msg)
                    log.debug("Decrypted OMEMO message from device %s", device_info)
                    # Process the decrypted stanza through the normal handler.
                    self._on_message(decrypted_msg)
                except Exception as exc:
                    log.warning("OMEMO decryption failed: %s: %s", type(exc).__name__, exc)
                    return
            else:
                # Plaintext message — process normally.
                self._on_message(msg)
        except Exception as exc:
            # If OMEMO plugin isn't ready yet, fall through to plaintext.
            log.debug("OMEMO handler exception: %s", exc)
            self._on_message(msg)

    # ------------------------------------------------------------------
    # slixmpp event handlers (run on the asyncio thread)
    # ------------------------------------------------------------------

    async def _on_session_start(self, event):
        """Authenticated and session established."""
        try:
            self._client.send_presence()
            await self._client.get_roster()
        except IqError as err:
            log.error("Roster error: %s", err.iq["error"]["condition"])
        except IqTimeout:
            log.error("Roster request timed out.")

        # Signal the blocking connect() call.
        self._connected_event.set()

        self._reciprocate_subscriptions()

        # Deliver the initial roster to the UI.
        self._deliver_roster()

        if self.on_connected:
            self.on_connected()

        # Query MAM for messages received while offline.
        try:
            await self._async_query_mam(since=None, max_results=200)
        except Exception as exc:
            log.warning("Initial MAM query failed: %s", exc)

    def _on_message(self, msg):
        """Incoming chat message."""
        if msg["type"] not in ("chat", "normal"):
            return
        from_jid = msg["from"]
        from_user = from_jid.user  # local part before @
        timestamp = datetime.now(timezone.utc).isoformat()

        # Check for Thrive file transfer message.
        files_el = msg.xml.find("{urn:thrive:files}files")
        if files_el is not None:
            files = []
            # Senders up to now emitted these children with no namespace
            # (xmlns=""), which is wrong but is what is on the wire.  Accept
            # both so the sender can be corrected once clients have updated.
            NS = "{urn:thrive:files}"

            def text(el, tag):
                found = el.findtext(NS + tag)
                return found if found is not None else el.findtext(tag, "")

            for file_el in list(files_el.findall(NS + "file")) + \
                    list(files_el.findall("file")):
                files.append({
                    "filename": text(file_el, "name"),
                    "size": int(text(file_el, "size") or "0"),
                    "url": text(file_el, "url"),
                    "content_type": text(file_el, "content-type"),
                })
            if files and self.on_file_message:
                self.on_file_message(from_user, files)
            return  # Don't treat as a normal text message.

        body = msg["body"]
        if not body:
            return

        if self.on_message:
            self.on_message(from_user, str(body), timestamp)

    def _on_presence_changed(self, presence):
        """A contact's presence changed (came online or changed status)."""
        from_jid = presence["from"]
        from_user = from_jid.user
        if from_user == self._username:
            return  # Ignore own presence reflections.

        # slixmpp raises changed_status for unavailable presence too, and a
        # bare <presence type="unavailable"/> carries no show -- so reading
        # only the show would report a contact who just went offline as
        # online.  got_offline is no substitute: it fires only once the
        # contact's *last* resource goes away.
        if presence["type"] == "unavailable":
            online, status_text = self._presence_of(
                self._client.client_roster[presence["from"].bare])
        else:
            online = True
            status_text = presence["status"] or ""
            if not status_text:
                status_text = _SHOW_TO_STATUS.get(presence["show"] or "", "online")

        if self.on_presence:
            self.on_presence(from_user, online, status_text)

    def _on_got_offline(self, presence):
        """A contact went offline."""
        from_jid = presence["from"]
        from_user = from_jid.user
        if from_user == self._username:
            return

        if self.on_presence:
            self.on_presence(from_user, False, "offline")

    def _on_subscription_request(self, presence):
        """Someone asked to see our presence -- approve and ask back.

        slixmpp only auto-authorises subscriptions when running as a
        component; for a client it just raises this event and expects the
        application to answer.  Ignoring it means nobody is ever granted a
        "from" subscription, so we stay permanently offline to everyone who
        added us, however our own roster looks.
        """
        jid = presence["from"].bare
        if jid == self._client.boundjid.bare:
            return  # Ignore our own reflections.

        # Grant them our presence.  The server pushes our current presence
        # to them as part of handling this, so they see us straight away.
        self._client.send_presence_subscription(pto=jid, ptype="subscribed")

        # Ask for theirs unless we already have it, so the pair ends up
        # mutually visible instead of one-way.
        item = self._client.client_roster[jid]
        if item["subscription"] not in ("to", "both"):
            self._client.send_presence_subscription(pto=jid, ptype="subscribe")

        log.info("Approved presence subscription from %s", jid)

    def _on_subscription_authorized(self, presence):
        """A contact approved us; refresh so the UI reflects the new state."""
        log.info("Presence subscription authorised by %s", presence["from"].bare)
        self._deliver_roster()

    def _reciprocate_subscriptions(self):
        """Ask back where a contact can see us but we cannot see them.

        Repairs pairs left half-finished by builds that never answered
        subscription requests.  A "from" entry means they see us and we do
        not see them; asking back completes the pair, and since they already
        added us the server usually approves it without prompting.
        """
        if not self._client:
            return
        roster = self._client.client_roster
        for jid in list(roster):
            if jid == self._client.boundjid.bare:
                continue
            item = roster[jid]
            if item["subscription"] == "from" and not item["pending_out"]:
                log.info("Asking %s for presence to complete a one-way pair", jid)
                self._client.send_presence_subscription(pto=jid, ptype="subscribe")

    def _on_tcp_connected(self, event):
        """TCP connection established (before STARTTLS/auth)."""
        log.info("TCP connected to server.")

    def _on_tls_success(self, event):
        """STARTTLS upgrade succeeded."""
        log.info("TLS handshake successful.")

    def _on_tls_failed(self, event):
        """STARTTLS upgrade failed."""
        log.error("TLS handshake failed: %s", event)
        self._connect_error = f"TLS handshake failed: {event}"
        self._connected_event.set()

    def _on_disconnected(self, event):
        """Connection lost."""
        log.info("Disconnected event: %s", event)
        if self._intentional_disconnect:
            return
        # If we haven't connected yet, treat as a connection failure.
        if not self._connected_event.is_set():
            reason = str(event) if event else "Server closed the connection."
            self._connect_error = f"Disconnected during login: {reason}"
            self._connected_event.set()
            return
        if self.on_disconnected:
            self.on_disconnected("Connection to the server was lost.")

    def _on_connection_failed(self, event):
        """Initial connection attempt failed."""
        detail = ""
        if isinstance(event, dict):
            detail = event.get("reason", "")
        elif isinstance(event, Exception):
            detail = str(event)
        elif isinstance(event, str):
            detail = event
        if detail:
            self._connect_error = f"Could not connect to server: {detail}"
        else:
            self._connect_error = "Could not connect to server."
        self._connected_event.set()

    def _on_failed_auth(self, event):
        """SASL authentication failed (wrong password or unknown user)."""
        self._connect_error = "Invalid credentials."
        self._connected_event.set()

    def _on_chatstate_composing(self, msg):
        """Remote user started typing."""
        if msg["type"] not in ("chat", "normal"):
            return
        from_user = msg["from"].user
        if from_user == self._username:
            return
        if self.on_chat_state:
            self.on_chat_state(from_user, "composing")

    def _on_chatstate_paused(self, msg):
        """Remote user paused typing."""
        if msg["type"] not in ("chat", "normal"):
            return
        from_user = msg["from"].user
        if from_user == self._username:
            return
        if self.on_chat_state:
            self.on_chat_state(from_user, "paused")

    def _on_chatstate_active(self, msg):
        """Remote user's input is active (not composing)."""
        if msg["type"] not in ("chat", "normal"):
            return
        from_user = msg["from"].user
        if from_user == self._username:
            return
        if self.on_chat_state:
            self.on_chat_state(from_user, "active")

    def _on_receipt_received(self, msg):
        """Delivery receipt received for a sent message."""
        receipt_id = msg["receipt"]
        from_user = msg["from"].user
        if self.on_receipt:
            self.on_receipt(receipt_id, from_user)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _presence_of(item):
        """Reduce a roster item's live resources to (online, status_text).

        slixmpp tracks each contact's resources as presence arrives, so this
        reflects who is actually online right now.  Reading it here matters
        because presence for contacts who were *already* online arrives in
        the probe replies at session start -- before the UI has registered
        its callbacks -- so those events are gone by the time anyone is
        listening.  The roster delivery is what carries that state instead.
        """
        resources = getattr(item, "resources", None) or {}
        if not resources:
            return False, "offline"
        # Highest priority wins, the same way a server picks a resource.
        best = max(resources.values(), key=lambda r: r.get("priority") or 0)
        status_text = best.get("status") or ""
        if not status_text:
            status_text = _SHOW_TO_STATUS.get(best.get("show") or "", "online")
        return True, status_text

    def _deliver_roster(self):
        """Extract roster into a simple list and fire the callback."""
        if not self.on_roster_loaded or not self._client:
            return
        contacts = []
        roster = self._client.client_roster
        for jid in roster:
            if jid == self._client.boundjid.bare:
                continue  # Skip self.
            user = slixmpp.JID(jid).user
            sub = roster[jid]["subscription"]
            name = roster[jid]["name"] or user
            online, status_text = self._presence_of(roster[jid])
            contacts.append({
                "user": user,
                "name": name,
                "subscription": sub,
                "online": online,
                "status_text": status_text,
            })
        self.on_roster_loaded(contacts)

    @staticmethod
    def _guess_content_type(filename):
        """Guess MIME type from filename extension."""
        import mimetypes
        ct, _ = mimetypes.guess_type(filename)
        return ct or "application/octet-stream"

    def _shutdown_loop(self):
        """Stop the asyncio event loop and wait for the thread to exit."""
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        self._loop = None
        self._thread = None
        self._client = None

    def _oneshot_iq_call(self, namespace, element, fields, timeout=15):
        """Open a temporary connection, send a custom IQ, return result.

        Used for pre-login operations (verify, password reset) that need a
        server round-trip but have no password to authenticate with.  The IQ
        goes out during stream-feature negotiation -- after STARTTLS, before
        SASL -- which is the only point an unauthenticated stream may send
        one.  The server signals support by advertising ``urn:thrive:preauth``
        in its stream features; we never attempt to log in on this stream.

        Returns (True, {child_tag: text}) or (False, reason).
        """
        jid = f"anon@{self._domain}"
        host, port, domain = self._server_host, self._server_port, self._domain

        result = {"success": False, "reason": "", "fields": {}}
        done = threading.Event()
        loop = asyncio.new_event_loop()

        def _thread_fn():
            asyncio.set_event_loop(loop)
            # Build the client on this thread, after the loop is current, so
            # slixmpp binds its futures to the loop we actually run below.
            client = slixmpp.ClientXMPP(jid, "")
            client.register_plugin("xep_0030")
            # XMLStream.send() holds back every stanza until the session
            # starts, passing through only bind/session/register IQs.  A
            # custom pre-auth payload would be queued and never sent, so the
            # IQ would just time out.  This stream carries one IQ and is then
            # dropped, so bypassing the gate is safe here.
            client._always_send_everything = True

            def _finish(reason):
                if not done.is_set():
                    if not result["reason"]:
                        result["reason"] = reason
                    done.set()
                loop.call_soon(loop.stop)

            async def _do(_features):
                """Stream-feature handler: send the IQ, then drop the stream."""
                try:
                    iq = client.make_iq_set(ito=domain)
                    query = slixmpp.ET.SubElement(
                        iq.xml, f"{{{namespace}}}{element}"
                    )
                    for key, val in fields.items():
                        # Children must inherit the payload namespace.  Bare
                        # names serialise as xmlns="", and Prosody's
                        # get_child_text(name) only matches children sharing
                        # the parent's namespace, so the server would see
                        # every field as missing.
                        child = slixmpp.ET.SubElement(
                            query, f"{{{namespace}}}{key}"
                        )
                        child.text = str(val)
                    resp = await iq.send(timeout=max(timeout - 2, 1))
                    result["success"] = True
                    # Collect the reply's children by local tag name.
                    for child in resp.xml:
                        tag = child.tag.split("}")[-1]
                        result["fields"][tag] = child.text or ""
                except IqError as err:
                    result["reason"] = err.iq["error"].get("text", "Request failed.")
                except IqTimeout:
                    result["reason"] = "Request timed out."
                except Exception as exc:
                    result["reason"] = str(exc)
                finally:
                    # Start the close before releasing the caller, so the
                    # stream footer goes out before the loop is torn down.
                    client.disconnect()
                    done.set()
                    # Backstop in case "disconnected" never arrives.
                    loop.call_later(1, loop.stop)
                # Returning True with restart=True halts feature negotiation,
                # so slixmpp never tries to authenticate this throwaway stream.
                return True

            unsupported = (
                "This server does not support password reset or account "
                "verification before login."
            )

            def _check_support(stanza):
                """Bail out early if the server can't serve pre-auth requests.

                The secure stream's features offer SASL; if they don't also
                offer urn:thrive:preauth then mod_thrive_reset is missing or
                predates pre-auth support.  Detecting it here beats waiting
                for the login we have no password for to fail.
                """
                if isinstance(stanza, StreamFeatures):
                    feats = stanza["features"]
                    if "mechanisms" in feats and "thrive_preauth" not in feats:
                        client.abort()
                        _finish(unsupported)
                return stanza

            def _on_failed_auth(_event):
                # Backstop: we only reach SASL when the feature was absent.
                if not result["reason"]:
                    result["reason"] = unsupported

            def _on_disconnected(_event):
                _finish("Connection closed by the server.")

            def _on_connection_failed(event):
                # One-shot: don't sit in slixmpp's reconnect backoff.
                client.abort()
                _finish(str(event) or "Could not reach the server.")

            client.register_feature(
                "thrive_preauth", _do,
                restart=True, order=_PREAUTH_FEATURE_ORDER,
            )
            client.add_filter("in", _check_support)
            client.add_event_handler("failed_auth", _on_failed_auth)
            client.add_event_handler("disconnected", _on_disconnected)
            client.add_event_handler("connection_failed", _on_connection_failed)
            client.enable_direct_tls = False
            client.enable_starttls = True
            try:
                client.connect(host=host, port=port)
                loop.run_forever()
            except Exception as exc:
                if not result["reason"]:
                    result["reason"] = str(exc)
                done.set()
            finally:
                # Cancel slixmpp's leftover tasks before closing, otherwise
                # asyncio logs "Task was destroyed but it is pending".
                pending = asyncio.all_tasks(loop)
                for task in pending:
                    task.cancel()
                if pending:
                    gather = asyncio.gather(*pending, return_exceptions=True)
                    # A queued loop.stop() can cut the drain short; retry
                    # until the cancellations have actually settled.
                    for _ in range(5):
                        try:
                            loop.run_until_complete(gather)
                            break
                        except RuntimeError:
                            continue
                loop.close()
                done.set()

        thread = threading.Thread(target=_thread_fn, daemon=True)
        thread.start()
        done.wait(timeout=timeout)
        if not done.is_set():
            result["reason"] = "Request timed out."
        # Always tear the loop down so the worker thread cannot outlive us.
        try:
            loop.call_soon_threadsafe(loop.stop)
        except RuntimeError:
            pass  # Loop already stopped and closed by the worker.
        thread.join(timeout=5)

        if result["success"]:
            return True, result["fields"]
        return False, result["reason"]
