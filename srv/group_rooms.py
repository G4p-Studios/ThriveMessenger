"""Persistent group-room domain model used by every Thrive server."""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROLE_RANK = {"guest": 0, "user": 1, "moderator": 2, "admin": 3, "owner": 4}
DEFAULT_PERMISSIONS: dict[str, list[str]] = {
    "view": ["guest", "user", "moderator", "admin", "owner"],
    "send_messages": ["guest", "user", "moderator", "admin", "owner"],
    "send_files": ["user", "moderator", "admin", "owner"],
    "join_voice": ["user", "moderator", "admin", "owner"],
    "invite": ["user", "moderator", "admin", "owner"],
    "moderate_messages": ["moderator", "admin", "owner"],
    "manage_members": ["admin", "owner"],
    "manage_room": ["owner"],
}
EXPIRATION_SECONDS = {
    "day": 86400,
    "week": 7 * 86400,
    "month": 30 * 86400,
    "year": 365 * 86400,
}


class GroupRoomError(ValueError):
    """A safe error that can be returned to a client."""


@dataclass(slots=True, frozen=True)
class RoomAccess:
    room_id: str
    username: str
    role: str


@contextmanager
def _connect(db_path: str | Path):
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def init_group_schema(db_path: str | Path) -> None:
    with _connect(db_path) as con:
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS group_rooms (
                room_id TEXT PRIMARY KEY,
                name TEXT NOT NULL COLLATE NOCASE UNIQUE,
                owner TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                visibility TEXT NOT NULL DEFAULT 'public',
                permissions_json TEXT NOT NULL,
                created_at REAL NOT NULL,
                expires_at REAL,
                expire_when_empty INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS group_room_members (
                room_id TEXT NOT NULL,
                username TEXT NOT NULL,
                role TEXT NOT NULL,
                joined_at REAL NOT NULL,
                PRIMARY KEY(room_id, username),
                FOREIGN KEY(room_id) REFERENCES group_rooms(room_id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS group_room_messages (
                message_id TEXT PRIMARY KEY,
                room_id TEXT NOT NULL,
                sender TEXT NOT NULL,
                body TEXT NOT NULL DEFAULT '',
                kind TEXT NOT NULL DEFAULT 'text',
                filename TEXT NOT NULL DEFAULT '',
                sent_at REAL NOT NULL,
                deleted INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY(room_id) REFERENCES group_rooms(room_id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_group_messages_room_time
                ON group_room_messages(room_id, sent_at);
            """
        )


def _room_dict(row: sqlite3.Row, member_count: int = 0, role: str = "") -> dict[str, Any]:
    return {
        "room_id": row["room_id"],
        "name": row["name"],
        "owner": row["owner"],
        "description": row["description"],
        "visibility": row["visibility"],
        "permissions": json.loads(row["permissions_json"] or "{}"),
        "created_at": row["created_at"],
        "expires_at": row["expires_at"],
        "expire_when_empty": bool(row["expire_when_empty"]),
        "member_count": member_count,
        "role": role,
    }


def purge_expired_rooms(db_path: str | Path, now: float | None = None) -> list[str]:
    now = time.time() if now is None else now
    with _connect(db_path) as con:
        rows = con.execute("SELECT room_id FROM group_rooms WHERE expires_at IS NOT NULL AND expires_at<=?", (now,)).fetchall()
        ids = [row[0] for row in rows]
        if ids:
            con.executemany("DELETE FROM group_rooms WHERE room_id=?", [(room_id,) for room_id in ids])
    return ids


def create_room(
    db_path: str | Path,
    owner: str,
    name: str,
    description: str = "",
    visibility: str = "public",
    expiration: str = "never",
    permissions: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    name = str(name or "").strip()
    if not name or len(name) > 80:
        raise GroupRoomError("Room name must contain 1 to 80 characters.")
    visibility = visibility if visibility in ("public", "private") else "public"
    expiration = str(expiration or "never").lower()
    expire_when_empty = expiration == "empty"
    expires_at = None
    if expiration in EXPIRATION_SECONDS:
        expires_at = time.time() + EXPIRATION_SECONDS[expiration]
    elif expiration not in ("never", "empty"):
        raise GroupRoomError("Expiration must be day, week, month, year, empty, or never.")
    normalized = DEFAULT_PERMISSIONS.copy()
    if permissions:
        for action, roles in permissions.items():
            if action in normalized and isinstance(roles, list):
                normalized[action] = [role for role in roles if role in ROLE_RANK]
    room_id = str(uuid.uuid4())
    now = time.time()
    try:
        with _connect(db_path) as con:
            con.execute(
                "INSERT INTO group_rooms VALUES(?,?,?,?,?,?,?,?,?)",
                (room_id, name, owner, str(description or "")[:1000], visibility, json.dumps(normalized), now, expires_at, int(expire_when_empty)),
            )
            con.execute("INSERT INTO group_room_members VALUES(?,?,?,?)", (room_id, owner, "owner", now))
    except sqlite3.IntegrityError as exc:
        raise GroupRoomError("A room with that name already exists.") from exc
    return get_room(db_path, room_id, owner)


def get_room(db_path: str | Path, room_id: str, username: str = "") -> dict[str, Any]:
    purge_expired_rooms(db_path)
    with _connect(db_path) as con:
        row = con.execute("SELECT * FROM group_rooms WHERE room_id=?", (room_id,)).fetchone()
        if not row:
            raise GroupRoomError("Room not found or expired.")
        count = con.execute("SELECT COUNT(*) FROM group_room_members WHERE room_id=?", (room_id,)).fetchone()[0]
        member = con.execute("SELECT role FROM group_room_members WHERE room_id=? AND username=?", (room_id, username)).fetchone()
    return _room_dict(row, count, member[0] if member else "")


def get_room_by_name(db_path: str | Path, name: str, username: str = "") -> dict[str, Any]:
    with _connect(db_path) as con:
        row = con.execute("SELECT room_id FROM group_rooms WHERE name=? COLLATE NOCASE", (str(name or "").strip(),)).fetchone()
    if not row:
        raise GroupRoomError("Create or join the group room before starting voice.")
    return get_room(db_path, row[0], username)


def list_rooms(db_path: str | Path, username: str) -> list[dict[str, Any]]:
    purge_expired_rooms(db_path)
    with _connect(db_path) as con:
        rows = con.execute(
            """SELECT r.*, COUNT(m.username) AS member_count,
                      COALESCE(me.role, '') AS role
               FROM group_rooms r
               LEFT JOIN group_room_members m ON m.room_id=r.room_id
               LEFT JOIN group_room_members me ON me.room_id=r.room_id AND me.username=?
               WHERE r.visibility='public' OR me.username IS NOT NULL
               GROUP BY r.room_id ORDER BY lower(r.name)""",
            (username,),
        ).fetchall()
    return [_room_dict(row, row["member_count"], row["role"]) for row in rows]


def member_role(db_path: str | Path, room_id: str, username: str) -> str:
    with _connect(db_path) as con:
        row = con.execute("SELECT role FROM group_room_members WHERE room_id=? AND username=?", (room_id, username)).fetchone()
    return row[0] if row else ""


def can(db_path: str | Path, room_id: str, username: str, action: str) -> bool:
    room = get_room(db_path, room_id, username)
    role = room["role"]
    return bool(role and role in room["permissions"].get(action, []))


def join_room(db_path: str | Path, room_id: str, username: str) -> dict[str, Any]:
    room = get_room(db_path, room_id, username)
    if not room["role"]:
        if room["visibility"] != "public":
            raise GroupRoomError("This private room requires an invitation.")
        with _connect(db_path) as con:
            con.execute("INSERT INTO group_room_members VALUES(?,?,?,?)", (room_id, username, "guest", time.time()))
    return get_room(db_path, room_id, username)


def leave_room(db_path: str | Path, room_id: str, username: str) -> bool:
    room = get_room(db_path, room_id, username)
    if room["role"] == "owner":
        if room["expire_when_empty"] and room["member_count"] == 1:
            with _connect(db_path) as con:
                con.execute("DELETE FROM group_rooms WHERE room_id=?", (room_id,))
            return True
        raise GroupRoomError("Transfer ownership or delete the room before leaving.")
    with _connect(db_path) as con:
        con.execute("DELETE FROM group_room_members WHERE room_id=? AND username=?", (room_id, username))
        remaining = con.execute("SELECT COUNT(*) FROM group_room_members WHERE room_id=?", (room_id,)).fetchone()[0]
        if not remaining and room["expire_when_empty"]:
            con.execute("DELETE FROM group_rooms WHERE room_id=?", (room_id,))
            return True
    return False


def add_member(db_path: str | Path, room_id: str, actor: str, target: str, role: str = "user") -> None:
    actor_role = member_role(db_path, room_id, actor)
    if ROLE_RANK.get(actor_role, -1) < ROLE_RANK["admin"]:
        raise GroupRoomError("You cannot invite room members.")
    if role not in ROLE_RANK or role == "owner" or ROLE_RANK[role] >= ROLE_RANK[actor_role]:
        raise GroupRoomError("Invalid role for this invitation.")
    target = str(target or "").strip()
    if not target:
        raise GroupRoomError("Username is required.")
    with _connect(db_path) as con:
        con.execute("INSERT OR REPLACE INTO group_room_members VALUES(?,?,?,?)", (room_id, target, role, time.time()))


def update_room(db_path: str | Path, room_id: str, actor: str, changes: dict[str, Any]) -> dict[str, Any]:
    room = get_room(db_path, room_id, actor)
    if room["role"] != "owner":
        raise GroupRoomError("Only the room owner can change room settings.")
    visibility = str(changes.get("visibility", room["visibility"]))
    if visibility not in ("public", "private"):
        raise GroupRoomError("Visibility must be public or private.")
    expiration = str(changes.get("expiration", "unchanged"))
    expires_at, expire_when_empty = room["expires_at"], room["expire_when_empty"]
    if expiration != "unchanged":
        expire_when_empty = expiration == "empty"
        expires_at = time.time() + EXPIRATION_SECONDS[expiration] if expiration in EXPIRATION_SECONDS else None
        if expiration not in (*EXPIRATION_SECONDS.keys(), "never", "empty"):
            raise GroupRoomError("Invalid room expiration.")
    permissions = changes.get("permissions", room["permissions"])
    normalized = DEFAULT_PERMISSIONS.copy()
    for action, roles in permissions.items():
        if action in normalized and isinstance(roles, list):
            normalized[action] = [role for role in roles if role in ROLE_RANK]
    with _connect(db_path) as con:
        con.execute(
            "UPDATE group_rooms SET description=?, visibility=?, permissions_json=?, expires_at=?, expire_when_empty=? WHERE room_id=?",
            (str(changes.get("description", room["description"]))[:1000], visibility, json.dumps(normalized), expires_at, int(expire_when_empty), room_id),
        )
    return get_room(db_path, room_id, actor)


def list_members(db_path: str | Path, room_id: str) -> list[dict[str, str]]:
    with _connect(db_path) as con:
        rows = con.execute(
            "SELECT username, role FROM group_room_members WHERE room_id=? ORDER BY CASE role WHEN 'owner' THEN 0 WHEN 'admin' THEN 1 WHEN 'moderator' THEN 2 WHEN 'user' THEN 3 ELSE 4 END, lower(username)",
            (room_id,),
        ).fetchall()
    return [{"username": row["username"], "role": row["role"]} for row in rows]


def set_member_role(db_path: str | Path, room_id: str, actor: str, target: str, role: str) -> None:
    if role not in ROLE_RANK or role == "owner":
        raise GroupRoomError("Role must be guest, user, moderator, or admin.")
    actor_role = member_role(db_path, room_id, actor)
    target_role = member_role(db_path, room_id, target)
    if not actor_role or not target_role or ROLE_RANK[actor_role] < ROLE_RANK["admin"]:
        raise GroupRoomError("You cannot manage room members.")
    if ROLE_RANK[target_role] >= ROLE_RANK[actor_role] or ROLE_RANK[role] >= ROLE_RANK[actor_role]:
        raise GroupRoomError("You cannot modify a member with an equal or higher role.")
    with _connect(db_path) as con:
        con.execute("UPDATE group_room_members SET role=? WHERE room_id=? AND username=?", (role, room_id, target))


def add_message(db_path: str | Path, room_id: str, sender: str, body: str, kind: str = "text", filename: str = "") -> dict[str, Any]:
    action = "send_files" if kind == "file" else "send_messages"
    if not can(db_path, room_id, sender, action):
        raise GroupRoomError(f"Your room role cannot {action.replace('_', ' ')}.")
    body = str(body or "")
    if kind == "text" and (not body.strip() or len(body) > 4000):
        raise GroupRoomError("Messages must contain 1 to 4000 characters.")
    message = {
        "message_id": str(uuid.uuid4()), "room_id": room_id, "sender": sender,
        "body": body, "kind": kind, "filename": str(filename or "")[:255], "sent_at": time.time(), "deleted": False,
    }
    with _connect(db_path) as con:
        con.execute(
            "INSERT INTO group_room_messages VALUES(?,?,?,?,?,?,?,0)",
            (message["message_id"], room_id, sender, body, kind, message["filename"], message["sent_at"]),
        )
    return message


def history(db_path: str | Path, room_id: str, username: str, limit: int = 100) -> list[dict[str, Any]]:
    if not can(db_path, room_id, username, "view"):
        raise GroupRoomError("Join the room before reading its history.")
    limit = max(1, min(int(limit), 500))
    with _connect(db_path) as con:
        rows = con.execute(
            "SELECT * FROM group_room_messages WHERE room_id=? ORDER BY sent_at DESC LIMIT ?", (room_id, limit)
        ).fetchall()
    return [dict(row) for row in reversed(rows)]
