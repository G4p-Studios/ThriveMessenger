import threading
import unittest
from unittest import mock

import main


class KeepaliveHarness:
    _start_keepalive_monitor = main.ClientApp._start_keepalive_monitor
    _stop_keepalive_monitor = main.ClientApp._stop_keepalive_monitor
    _handle_keepalive_failure = main.ClientApp._handle_keepalive_failure


class KeepaliveLifecycleTests(unittest.TestCase):
    def test_starting_replacement_monitor_stops_previous_generation(self):
        app = KeepaliveHarness()
        app._keepalive_stop = threading.Event()
        previous = app._keepalive_stop
        app._keepalive_monitor = mock.Mock()
        replacement_socket = object()

        with mock.patch.object(main.threading, "Thread") as thread:
            app._start_keepalive_monitor(replacement_socket)

        self.assertTrue(previous.is_set())
        self.assertIsNot(previous, app._keepalive_stop)
        thread.assert_called_once_with(
            target=app._keepalive_monitor,
            args=(replacement_socket, app._keepalive_stop),
            daemon=True,
        )
        thread.return_value.start.assert_called_once_with()

    def test_stale_socket_failure_does_not_disconnect_current_socket(self):
        app = KeepaliveHarness()
        app.sock = object()
        app.on_server_disconnect = mock.Mock()

        app._handle_keepalive_failure(object())

        app.on_server_disconnect.assert_not_called()

    def test_current_socket_failure_disconnects(self):
        app = KeepaliveHarness()
        app.sock = object()
        app.on_server_disconnect = mock.Mock()

        app._handle_keepalive_failure(app.sock)

        app.on_server_disconnect.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
