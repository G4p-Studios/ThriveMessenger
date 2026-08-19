import unittest
import threading

from srv import server
from srv.server import ADMIN_CLI_HELP, parse_admin_command


class AdminCliParsingTests(unittest.TestCase):
    def test_accepts_plain_command(self):
        self.assertEqual(parse_admin_command("gpolicy show"), ["gpolicy", "show"])

    def test_accepts_gui_style_leading_slash(self):
        self.assertEqual(
            parse_admin_command(" /accountlimit set 2 "),
            ["accountlimit", "set", "2"],
        )

    def test_question_mark_is_help_alias(self):
        self.assertEqual(parse_admin_command("/?"), ["help"])

    def test_empty_command_is_ignored(self):
        self.assertEqual(parse_admin_command("   "), [])

    def test_help_lists_extended_admin_commands(self):
        for command in ("accountlimit", "gpolicy", "banfile", "restart"):
            with self.subTest(command=command):
                self.assertIn(command, ADMIN_CLI_HELP)


class GroupCallRegistryTests(unittest.TestCase):
    def tearDown(self):
        with server.group_call_lock:
            server.group_call_sessions.clear()

    def test_snapshot_can_be_taken_while_registry_is_locked(self):
        with server.group_call_lock:
            server.group_call_sessions["team"] = {
                "mode": "voice",
                "participants": {"alice", "bob"},
            }
            snapshot = server._group_call_snapshot("team")
        self.assertEqual(snapshot["participants"], ["alice", "bob"])

    def test_disconnect_cleanup_does_not_deadlock(self):
        with server.group_call_lock:
            server.group_call_sessions["team"] = {
                "mode": "voice",
                "participants": {"alice", "bob"},
            }
        worker = threading.Thread(target=server._remove_user_from_all_group_calls, args=("alice",))
        worker.start()
        worker.join(timeout=1)
        self.assertFalse(worker.is_alive(), "group call cleanup deadlocked")
        self.assertEqual(server._group_call_snapshot("team")["participants"], ["bob"])

    def test_direct_call_busy_state_includes_ringing_target(self):
        with server.group_call_lock:
            server.group_call_sessions["direct:test"] = {
                "mode": "voice", "direct": True, "participants": {"alice"}, "pending": "bob",
            }
        self.assertTrue(server._user_has_direct_call("alice"))
        self.assertTrue(server._user_has_direct_call("bob"))
        self.assertFalse(server._user_has_direct_call("carol"))


if __name__ == "__main__":
    unittest.main()
