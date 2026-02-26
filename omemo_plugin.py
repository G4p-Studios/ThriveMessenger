"""
OMEMO plugin implementation for Thrive Messenger.

Provides:
- ``StorageImpl``: JSON-file-backed key/value storage for python-omemo.
- ``XEP_0384Impl``: Concrete XEP-0384 subclass with Blind Trust Before
  Verification (BTBV) enabled.

Import this module before calling ``register_plugin("xep_0384", ...)``
so that slixmpp can resolve the concrete implementation.
"""

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, FrozenSet, Optional

from omemo.storage import Just, Maybe, Nothing, Storage
from omemo.types import DeviceInformation, JSONType
from slixmpp.plugins import register_plugin

from slixmpp_omemo import TrustLevel, XEP_0384

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Storage backend
# ---------------------------------------------------------------------------

class StorageImpl(Storage):
    """Persist OMEMO session data to a JSON file (one per account).

    The file is rewritten on every store/delete — acceptable for a desktop
    client with a single active account at a time.
    """

    def __init__(self, json_file_path: Path) -> None:
        super().__init__()
        self.__path = json_file_path
        self.__data: Dict[str, JSONType] = {}
        try:
            with open(self.__path, encoding="utf-8") as f:
                self.__data = json.load(f)
        except Exception:
            pass

    async def _load(self, key: str) -> Maybe[JSONType]:
        if key in self.__data:
            return Just(self.__data[key])
        return Nothing()

    async def _store(self, key: str, value: JSONType) -> None:
        self.__data[key] = value
        self._flush()

    async def _delete(self, key: str) -> None:
        self.__data.pop(key, None)
        self._flush()

    def _flush(self) -> None:
        os.makedirs(self.__path.parent, exist_ok=True)
        with open(self.__path, "w", encoding="utf-8") as f:
            json.dump(self.__data, f)


# ---------------------------------------------------------------------------
# XEP-0384 concrete subclass
# ---------------------------------------------------------------------------

class XEP_0384Impl(XEP_0384):
    """OMEMO plugin for Thrive Messenger.

    Uses Blind Trust Before Verification (BTBV): new devices from
    contacts are automatically trusted until the user manually verifies
    at least one device for that contact.
    """

    default_config = {
        "fallback_message": "This message is OMEMO encrypted.",
        "json_file_path": None,
    }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.__storage: Storage

    def plugin_init(self) -> None:
        if not self.json_file_path:
            raise RuntimeError(
                "OMEMO storage path not configured. "
                "Pass json_file_path when registering xep_0384."
            )
        self.__storage = StorageImpl(Path(self.json_file_path))
        super().plugin_init()

    # --- Required: storage backend ---
    @property
    def storage(self) -> Storage:
        return self.__storage

    # --- Required: BTBV flag ---
    @property
    def _btbv_enabled(self) -> bool:
        return True

    # --- BTBV notification (informational) ---
    async def _devices_blindly_trusted(
        self,
        blindly_trusted: FrozenSet[DeviceInformation],
        identifier: Optional[str],
    ) -> None:
        for dev in blindly_trusted:
            log.info(
                "OMEMO: Blindly trusted device %s for %s",
                dev.device_id, dev.bare_jid,
            )

    # --- Required: manual trust prompt ---
    async def _prompt_manual_trust(
        self,
        manually_trusted: FrozenSet[DeviceInformation],
        identifier: Optional[str],
    ) -> None:
        # For now, auto-trust devices that require manual approval.
        # A future UI enhancement could show a verification dialog.
        session_manager = await self.get_session_manager()
        for device in manually_trusted:
            log.info(
                "OMEMO: Auto-trusting device %s for %s (manual trust prompted)",
                device.device_id, device.bare_jid,
            )
            await session_manager.set_trust(
                device.bare_jid,
                device.identity_key,
                TrustLevel.TRUSTED.value,
            )


# Register so slixmpp can find our implementation when xep_0384 is loaded.
register_plugin(XEP_0384Impl)
