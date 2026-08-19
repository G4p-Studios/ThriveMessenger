import tempfile
import unittest
from pathlib import Path

from srv import group_rooms


class GroupRoomTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db = Path(self.temp_dir.name) / "rooms.db"
        group_rooms.init_group_schema(self.db)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_owner_and_guest_roles_and_default_file_rule(self):
        room = group_rooms.create_room(self.db, "alice", "General")
        joined = group_rooms.join_room(self.db, room["room_id"], "bob")
        self.assertEqual(joined["role"], "guest")
        self.assertTrue(group_rooms.can(self.db, room["room_id"], "bob", "send_messages"))
        self.assertFalse(group_rooms.can(self.db, room["room_id"], "bob", "send_files"))

    def test_private_room_cannot_be_joined_without_membership(self):
        room = group_rooms.create_room(self.db, "alice", "Staff", visibility="private")
        with self.assertRaisesRegex(group_rooms.GroupRoomError, "invitation"):
            group_rooms.join_room(self.db, room["room_id"], "bob")

    def test_admin_can_promote_lower_role_but_not_owner(self):
        room = group_rooms.create_room(self.db, "alice", "General")
        group_rooms.join_room(self.db, room["room_id"], "bob")
        group_rooms.set_member_role(self.db, room["room_id"], "alice", "bob", "admin")
        self.assertEqual(group_rooms.member_role(self.db, room["room_id"], "bob"), "admin")
        with self.assertRaises(group_rooms.GroupRoomError):
            group_rooms.set_member_role(self.db, room["room_id"], "bob", "alice", "user")

    def test_message_history_is_persistent(self):
        room = group_rooms.create_room(self.db, "alice", "General")
        sent = group_rooms.add_message(self.db, room["room_id"], "alice", "Hello room")
        history = group_rooms.history(self.db, room["room_id"], "alice")
        self.assertEqual(history[0]["message_id"], sent["message_id"])
        self.assertEqual(history[0]["body"], "Hello room")

    def test_time_expiration_removes_room(self):
        room = group_rooms.create_room(self.db, "alice", "Temporary", expiration="day")
        removed = group_rooms.purge_expired_rooms(self.db, now=room["expires_at"] + 1)
        self.assertEqual(removed, [room["room_id"]])

    def test_empty_expiration_deletes_when_owner_is_last_to_leave(self):
        room = group_rooms.create_room(self.db, "alice", "Ephemeral", expiration="empty")
        self.assertTrue(group_rooms.leave_room(self.db, room["room_id"], "alice"))
        with self.assertRaises(group_rooms.GroupRoomError):
            group_rooms.get_room(self.db, room["room_id"], "alice")

    def test_owner_can_change_role_permissions(self):
        room = group_rooms.create_room(self.db, "alice", "Files")
        group_rooms.join_room(self.db, room["room_id"], "bob")
        permissions = room["permissions"]
        permissions["send_files"] = ["guest", "user", "moderator", "admin", "owner"]
        updated = group_rooms.update_room(self.db, room["room_id"], "alice", {"permissions": permissions})
        self.assertIn("guest", updated["permissions"]["send_files"])
        self.assertTrue(group_rooms.can(self.db, room["room_id"], "bob", "send_files"))

    def test_room_admin_can_invite_user(self):
        room = group_rooms.create_room(self.db, "alice", "Private", visibility="private")
        group_rooms.add_member(self.db, room["room_id"], "alice", "bob", "user")
        joined = group_rooms.join_room(self.db, room["room_id"], "bob")
        self.assertEqual(joined["role"], "user")


if __name__ == "__main__":
    unittest.main()
