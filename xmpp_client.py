"""
XMPP client wrapper for Thrive Messenger.

Wraps slixmpp with an asyncio event loop running in a background thread.
Provides a synchronous API for the wxPython UI, dispatching incoming events
via caller-provided callbacks (typically wrapped in wx.CallAfter).
"""

import asyncio
import os
import threading
import time
import logging
from datetime import datetime, timezone

import aiohttp
import slixmpp
from slixmpp.exceptions import IqError, IqTimeout
from slixmpp.xmlstream.handler import CoroutineCallback
from slixmpp.xmlstream.matcher import MatchXPath

import omemo_plugin  # noqa: F401 — registers XEP_0384Impl with slixmpp

log = logging.getLogger(__name__)

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

        # Enable slixmpp debug logging to diagnose connection issues.
        logging.basicConfig(level=logging.DEBUG, format="%(name)s %(levelname)s: %(message)s")
        logging.getLogger("slixmpp").setLevel(logging.DEBUG)

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

    def disconnect(self):
        """Gracefully disconnect."""
        self._intentional_disconnect = True
        if self._client:
            asyncio.run_coroutine_threadsafe(
                self._async_disconnect(), self._loop
            )
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
        client = slixmpp.ClientXMPP(jid, password)
        client.register_plugin("xep_0030")
        client.register_plugin("xep_0077")

        result = {"success": False, "reason": "", "verify_pending": False}
        done = threading.Event()

        async def _do_register():
            try:
                reg = client.plugin["xep_0077"]
                form = reg.get_registration()
                # Build registration fields.
                form_data = {"username": username, "password": password}
                if email:
                    form_data["email"] = email
                resp = await reg.register(form_data)
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

        loop = asyncio.new_event_loop()
        thread = threading.Thread(target=loop.run_forever, daemon=True)
        thread.start()

        try:
            client.enable_direct_tls = False
            client.enable_starttls = True
            client.connect(host=self._server_host, port=self._server_port)
            asyncio.run_coroutine_threadsafe(_do_register(), loop)
            done.wait(timeout=timeout)
            if not done.is_set():
                result["reason"] = "Registration timed out."
        finally:
            loop.call_soon_threadsafe(loop.stop)
            thread.join(timeout=5)

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
                iq = self._make_oneshot_iq(username)
                query = slixmpp.ET.SubElement(
                    iq.xml, "{urn:thrive:verify}verify"
                )
                slixmpp.ET.SubElement(query, "username").text = username
                slixmpp.ET.SubElement(query, "code").text = code
                resp = await iq.send()
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
            return self._oneshot_iq_call(
                "urn:thrive:verify", "verify",
                {"username": username, "code": code},
                timeout=timeout
            )
        if not done.is_set():
            return False, "Verification timed out."
        return (True, "") if result["success"] else (False, result["reason"])

    def request_password_reset(self, identifier, timeout=15):
        """Request a password reset code (custom IQ).

        Returns (True, username_hint) on success, (False, reason) on failure.
        """
        return self._oneshot_iq_call(
            "urn:thrive:reset", "request",
            {"identifier": identifier},
            timeout=timeout
        )

    def reset_password(self, username, code, new_password, timeout=15):
        """Confirm a password reset with code and new password (custom IQ).

        Returns (True, "") on success, (False, reason) on failure.
        """
        return self._oneshot_iq_call(
            "urn:thrive:reset", "confirm",
            {"username": username, "code": code, "password": new_password},
            timeout=timeout
        )

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
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

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
            self._client.register_plugin("xep_0199")  # Ping
            self._client.register_plugin("xep_0313")  # Message Archive Management
            self._client.register_plugin("xep_0363")  # HTTP File Upload
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
        self._client.disconnect()

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
            upload = self._client.plugin["xep_0363"]
            for fp in file_paths:
                filename = os.path.basename(fp)
                size = os.path.getsize(fp)
                content_type = self._guess_content_type(filename)

                # Request upload slot.
                slot = await upload.request_slot(
                    filename=filename,
                    size=size,
                    content_type=content_type,
                )
                put_url = slot["put"]["url"]
                get_url = slot["get"]["url"]
                put_headers = slot["put"].get("headers", {})

                # Upload via HTTP PUT.
                headers = dict(put_headers)
                headers["Content-Type"] = content_type
                headers["Content-Length"] = str(size)

                async with aiohttp.ClientSession() as session:
                    with open(fp, "rb") as f:
                        data = f.read()
                    async with session.put(put_url, data=data, headers=headers) as resp:
                        if resp.status not in (200, 201):
                            text = await resp.text()
                            raise Exception(
                                f"Upload failed ({resp.status}): {text[:200]}"
                            )

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
            slixmpp.ET.SubElement(oob, "url").text = uploaded[0]["url"]
            slixmpp.ET.SubElement(oob, "desc").text = uploaded[0]["filename"]

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

        except Exception as exc:
            log.warning("File upload failed: %s", exc)
            if self.on_file_upload_error:
                self.on_file_upload_error(to_username, str(exc))

    async def _async_download_file(self, url, save_dir, filename=None):
        """Download a file and save it to disk."""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as resp:
                    if resp.status != 200:
                        log.warning("Download failed (%d): %s", resp.status, url)
                        return None, f"Download failed ({resp.status})"
                    data = await resp.read()

            if not filename:
                # Try to extract from URL or Content-Disposition.
                from urllib.parse import urlparse, unquote
                path = urlparse(url).path
                filename = unquote(os.path.basename(path)) or "download"

            os.makedirs(save_dir, exist_ok=True)
            save_path = os.path.join(save_dir, filename)

            # Handle duplicates.
            if os.path.exists(save_path):
                name, ext = os.path.splitext(filename)
                counter = 1
                while os.path.exists(save_path):
                    save_path = os.path.join(save_dir, f"{name} ({counter}){ext}")
                    counter += 1

            with open(save_path, "wb") as f:
                f.write(data)

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
            for file_el in files_el.findall("file"):
                files.append({
                    "filename": file_el.findtext("name", ""),
                    "size": int(file_el.findtext("size", "0")),
                    "url": file_el.findtext("url", ""),
                    "content_type": file_el.findtext("content-type", ""),
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

        show = presence["show"] or "chat"  # "chat" means available
        status_text = presence["status"] or ""

        # Map XMPP show to Thrive status.
        online = show in ("", "chat", "away", "xa", "dnd")
        if not status_text:
            show_map = {"": "online", "chat": "online", "away": "away",
                        "xa": "away", "dnd": "busy"}
            status_text = show_map.get(show, "online")

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
            contacts.append({
                "user": user,
                "name": name,
                "subscription": sub,
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

        Used for pre-login operations (verify, password reset) that need
        a server round-trip but don't require authentication.

        Returns (True, response_text) or (False, reason).
        """
        jid = f"anon@{self._domain}"
        client = slixmpp.ClientXMPP(jid, "")
        client.register_plugin("xep_0030")

        result = {"success": False, "reason": "", "text": ""}
        done = threading.Event()

        async def _do():
            try:
                iq = client.make_iq_set(ito=self._domain)
                query = slixmpp.ET.SubElement(
                    iq.xml, f"{{{namespace}}}{element}"
                )
                for key, val in fields.items():
                    slixmpp.ET.SubElement(query, key).text = str(val)
                resp = await iq.send(timeout=timeout - 2)
                result["success"] = True
                # Try to extract a text response.
                for child in resp.xml:
                    if child.text:
                        result["text"] = child.text
                        break
            except IqError as err:
                result["reason"] = err.iq["error"].get("text", "Request failed.")
            except IqTimeout:
                result["reason"] = "Request timed out."
            except Exception as exc:
                result["reason"] = str(exc)
            finally:
                client.disconnect()
                done.set()

        loop = asyncio.new_event_loop()
        thread = threading.Thread(target=loop.run_forever, daemon=True)
        thread.start()

        try:
            client.enable_direct_tls = False
            client.enable_starttls = True
            client.connect(host=self._server_host, port=self._server_port)
            asyncio.run_coroutine_threadsafe(_do(), loop)
            done.wait(timeout=timeout)
            if not done.is_set():
                return False, "Request timed out."
        finally:
            loop.call_soon_threadsafe(loop.stop)
            thread.join(timeout=5)

        if result["success"]:
            return True, result["text"]
        return False, result["reason"]
