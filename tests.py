"""
Unit tests for LZRD core logic and Flask API.
These tests mock all platform-specific dependencies so they run on any OS.
"""

import configparser
import queue
import sys
import threading
import types
import unittest
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Platform stubs — must be installed before importing lzrd
# ---------------------------------------------------------------------------

def _make_pynput_stub() -> None:
    pynput = types.ModuleType("pynput")
    pynput_mouse = types.ModuleType("pynput.mouse")

    class FakeListener:
        def __init__(self, on_move=None):
            self._on_move = on_move

        def start(self):
            pass

        def stop(self):
            pass

    pynput_mouse.Listener = FakeListener
    pynput.mouse = pynput_mouse
    sys.modules["pynput"] = pynput
    sys.modules["pynput.mouse"] = pynput_mouse


def _make_pystray_stub() -> None:
    pystray = types.ModuleType("pystray")

    class FakeMenu:
        SEPARATOR = object()

        def __init__(self, *items):
            self.items = items

    class FakeMenuItem:
        def __init__(self, text, action=None, enabled=True):
            self.text = text
            self.action = action
            self.enabled = enabled

    class FakeIcon:
        def __init__(self, name, icon, title, menu):
            self.name = name
            self.icon = icon
            self.title = title
            self.menu = menu

        def stop(self):
            pass

        def run(self):
            pass

    pystray.Menu = FakeMenu
    pystray.MenuItem = FakeMenuItem
    pystray.Icon = FakeIcon
    sys.modules["pystray"] = pystray


# Install stubs before importing lzrd
_make_pynput_stub()
_make_pystray_stub()

import lzrd as lzrd_module  # noqa: E402  (must come after stubs)


# ---------------------------------------------------------------------------
# Config helper
# ---------------------------------------------------------------------------

def _make_config(threshold: str = "10") -> configparser.ConfigParser:
    cfg = configparser.ConfigParser()
    cfg["server"] = {"port": "7734", "token": "testtoken"}
    cfg["lzrd"] = {"movement_threshold": threshold}
    return cfg


# ---------------------------------------------------------------------------
# TestLZRDArming
# ---------------------------------------------------------------------------

class TestLZRDArming(unittest.TestCase):
    def setUp(self):
        self.cfg = _make_config()

    def test_initial_state_is_disarmed(self):
        app = lzrd_module.LZRD(self.cfg)
        self.assertFalse(app.armed)
        self.assertFalse(app.alert_triggered)

    def test_arm_sets_armed_true(self):
        app = lzrd_module.LZRD(self.cfg)
        with patch.object(lzrd_module, "_get_cursor_pos", return_value=(100, 200)):
            app.arm()
        self.assertTrue(app.armed)
        self.assertEqual(app._initial_pos, (100, 200))

    def test_arm_is_idempotent(self):
        app = lzrd_module.LZRD(self.cfg)
        with patch.object(lzrd_module, "_get_cursor_pos", return_value=(10, 20)):
            app.arm()
            app.arm()  # second call should be a no-op
        self.assertTrue(app.armed)

    def test_disarm_sets_armed_false(self):
        app = lzrd_module.LZRD(self.cfg)
        with patch.object(lzrd_module, "_get_cursor_pos", return_value=(0, 0)):
            app.arm()
        app.disarm()
        self.assertFalse(app.armed)
        self.assertFalse(app.alert_triggered)

    def test_disarm_is_idempotent(self):
        app = lzrd_module.LZRD(self.cfg)
        app.disarm()  # should not raise when already disarmed
        self.assertFalse(app.armed)


# ---------------------------------------------------------------------------
# TestLZRDMovementDetection
# ---------------------------------------------------------------------------

class TestLZRDMovementDetection(unittest.TestCase):
    def setUp(self):
        self.cfg = _make_config(threshold="10")

    def _armed_app(self, initial_pos: tuple = (100, 100)) -> lzrd_module.LZRD:
        app = lzrd_module.LZRD(self.cfg)
        app.on_state_change = MagicMock()
        with patch.object(lzrd_module, "_get_cursor_pos", return_value=initial_pos):
            app.arm()
        return app

    def test_small_movement_does_not_trigger_alert(self):
        app = self._armed_app(initial_pos=(100, 100))
        app._on_move(105, 105)  # within 10-pixel threshold
        self.assertFalse(app.alert_triggered)

    def test_large_movement_triggers_alert(self):
        app = self._armed_app(initial_pos=(100, 100))
        app._on_move(150, 100)  # 50 px beyond threshold
        self.assertTrue(app.alert_triggered)

    def test_alert_fires_only_once(self):
        app = self._armed_app(initial_pos=(100, 100))
        state_changes_before = app.on_state_change.call_count
        app._on_move(200, 200)
        first_calls = app.on_state_change.call_count
        app._on_move(300, 300)  # second big move — should be ignored
        self.assertEqual(app.on_state_change.call_count, first_calls)

    def test_movement_not_detected_when_disarmed(self):
        app = self._armed_app(initial_pos=(100, 100))
        app.disarm()
        app.on_state_change.reset_mock()
        app._on_move(200, 200)
        self.assertFalse(app.alert_triggered)
        app.on_state_change.assert_not_called()


# ---------------------------------------------------------------------------
# TestMouseLock
# ---------------------------------------------------------------------------

class TestMouseLock(unittest.TestCase):
    def setUp(self):
        self.cfg = _make_config()

    def test_toggle_locks_mouse(self):
        app = lzrd_module.LZRD(self.cfg)
        app.on_state_change = MagicMock()
        with patch.object(lzrd_module, "lock_mouse_cursor") as mock_lock:
            app.toggle_mouse_lock()
        self.assertTrue(app.mouse_locked)
        mock_lock.assert_called_once()

    def test_toggle_unlocks_mouse(self):
        app = lzrd_module.LZRD(self.cfg)
        app.mouse_locked = True
        app.on_state_change = MagicMock()
        with patch.object(lzrd_module, "unlock_mouse_cursor") as mock_unlock:
            app.toggle_mouse_lock()
        self.assertFalse(app.mouse_locked)
        mock_unlock.assert_called_once()


# ---------------------------------------------------------------------------
# TestBroadcast
# ---------------------------------------------------------------------------

class TestBroadcast(unittest.TestCase):
    def setUp(self):
        # Clear any leftover queues from other tests
        lzrd_module._event_queues.clear()

    def test_broadcast_delivers_to_all_queues(self):
        q1: queue.Queue = queue.Queue()
        q2: queue.Queue = queue.Queue()
        lzrd_module._event_queues.extend([q1, q2])
        try:
            lzrd_module._broadcast({"type": "test", "value": 42})
            self.assertEqual(q1.get_nowait(), {"type": "test", "value": 42})
            self.assertEqual(q2.get_nowait(), {"type": "test", "value": 42})
        finally:
            lzrd_module._event_queues.clear()

    def test_broadcast_with_no_subscribers_is_safe(self):
        # Should not raise
        lzrd_module._broadcast({"type": "noop"})


# ---------------------------------------------------------------------------
# TestFlaskAPI
# ---------------------------------------------------------------------------

class TestFlaskAPI(unittest.TestCase):
    def setUp(self):
        self.cfg = _make_config()
        lzrd_module._token = "testtoken"
        lzrd_module._lzrd = lzrd_module.LZRD(self.cfg)
        lzrd_module._lzrd.on_state_change = MagicMock()
        self.client = lzrd_module._flask_app.test_client()

    def tearDown(self):
        lzrd_module._lzrd = None

    # Helpers
    def _get(self, url: str, token: str = "testtoken"):
        return self.client.get(url, headers={"X-Token": token})

    def _post(self, url: str, body=None, token: str = "testtoken"):
        return self.client.post(url, json=body or {}, headers={"X-Token": token})

    # Authentication
    def test_status_unauthorized(self):
        r = self._get("/api/status", token="wrong")
        self.assertEqual(r.status_code, 401)

    def test_arm_unauthorized(self):
        r = self._post("/api/arm", token="wrong")
        self.assertEqual(r.status_code, 401)

    def test_events_unauthorized(self):
        r = self._get("/api/events", token="wrong")
        self.assertEqual(r.status_code, 401)

    # Status
    def test_status_returns_state(self):
        r = self._get("/api/status")
        self.assertEqual(r.status_code, 200)
        data = r.get_json()
        self.assertFalse(data["armed"])
        self.assertFalse(data["alert"])
        self.assertFalse(data["mouse_locked"])

    # Arm / Disarm
    def test_arm_and_disarm(self):
        with patch.object(lzrd_module, "_get_cursor_pos", return_value=(0, 0)):
            r = self._post("/api/arm")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(lzrd_module._lzrd.armed)

        r = self._post("/api/disarm")
        self.assertEqual(r.status_code, 200)
        self.assertFalse(lzrd_module._lzrd.armed)

    # Lock screen
    def test_lock_screen(self):
        with patch.object(lzrd_module, "lock_workstation") as m:
            r = self._post("/api/lock-screen")
        self.assertEqual(r.status_code, 200)
        m.assert_called_once()

    # Lock mouse
    def test_lock_mouse_toggle(self):
        with patch.object(lzrd_module, "lock_mouse_cursor"):
            r = self._post("/api/lock-mouse")
        self.assertEqual(r.status_code, 200)
        data = r.get_json()
        self.assertTrue(data["mouse_locked"])

    def test_unlock_mouse_toggle(self):
        lzrd_module._lzrd.mouse_locked = True
        with patch.object(lzrd_module, "unlock_mouse_cursor"):
            r = self._post("/api/lock-mouse")
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.get_json()["mouse_locked"])

    # Shutdown
    def test_shutdown(self):
        with patch.object(lzrd_module, "shutdown_computer") as m:
            r = self._post("/api/shutdown")
        self.assertEqual(r.status_code, 200)
        m.assert_called_once()

    # Restart
    def test_restart(self):
        with patch.object(lzrd_module, "restart_computer") as m:
            r = self._post("/api/restart")
        self.assertEqual(r.status_code, 200)
        m.assert_called_once()

    # Message
    def test_message_sent(self):
        with patch.object(lzrd_module, "display_message") as m:
            r = self._post("/api/message", {"text": "hello world"})
        self.assertEqual(r.status_code, 200)
        m.assert_called_once_with("hello world")

    def test_message_missing_text_returns_400(self):
        r = self._post("/api/message", {})
        self.assertEqual(r.status_code, 400)

    def test_message_whitespace_only_returns_400(self):
        r = self._post("/api/message", {"text": "   "})
        self.assertEqual(r.status_code, 400)

    # Launch app
    def test_launch_app(self):
        with patch.object(lzrd_module, "launch_app") as m:
            r = self._post("/api/launch", {"path": "notepad.exe"})
        self.assertEqual(r.status_code, 200)
        m.assert_called_once_with("notepad.exe")

    def test_launch_app_does_not_use_shell(self):
        """launch_app must not invoke a shell (prevents shell injection)."""
        with patch("subprocess.Popen") as mock_popen:
            lzrd_module.launch_app("notepad.exe arg1")
        mock_popen.assert_called_once()
        _, kwargs = mock_popen.call_args
        self.assertFalse(kwargs.get("shell", False), "shell=True would allow injection")

    def test_launch_missing_path_returns_400(self):
        r = self._post("/api/launch", {})
        self.assertEqual(r.status_code, 400)


# ---------------------------------------------------------------------------
# TestIconRendering
# ---------------------------------------------------------------------------

class TestIconRendering(unittest.TestCase):
    def test_armed_icon_size(self):
        from PIL import Image
        img = lzrd_module._make_icon_image(armed=True)
        self.assertIsInstance(img, Image.Image)
        self.assertEqual(img.size, (64, 64))

    def test_disarmed_icon_size(self):
        from PIL import Image
        img = lzrd_module._make_icon_image(armed=False)
        self.assertIsInstance(img, Image.Image)
        self.assertEqual(img.size, (64, 64))

    def test_pwa_icon_size_192(self):
        from PIL import Image
        img = lzrd_module._make_pwa_icon(192)
        self.assertIsInstance(img, Image.Image)
        self.assertEqual(img.size, (192, 192))

    def test_pwa_icon_size_512(self):
        from PIL import Image
        img = lzrd_module._make_pwa_icon(512)
        self.assertIsInstance(img, Image.Image)
        self.assertEqual(img.size, (512, 512))


# ---------------------------------------------------------------------------
# TestCrossPlatformOperations
# ---------------------------------------------------------------------------

class TestCrossPlatformOperations(unittest.TestCase):
    """Tests for cross-platform system operations (Linux / non-Windows paths)."""

    @unittest.skipIf(sys.platform == "win32", "Linux-specific path")
    def test_lock_workstation_tries_loginctl_first(self):
        """On Linux, lock_workstation should try loginctl before other lockers."""
        with patch("subprocess.Popen") as mock_popen:
            lzrd_module.lock_workstation()
        mock_popen.assert_called()
        first_cmd = mock_popen.call_args_list[0][0][0]
        self.assertEqual(first_cmd[0], "loginctl")

    @unittest.skipIf(sys.platform == "win32", "Linux-specific path")
    def test_lock_workstation_falls_back_on_missing_command(self):
        """Falls through to the next locker when the first is not installed."""
        call_count = [0]

        def _side_effect(cmd, *a, **kw):
            call_count[0] += 1
            if cmd[0] == "loginctl":
                raise FileNotFoundError
            return MagicMock()

        with patch("subprocess.Popen", side_effect=_side_effect):
            lzrd_module.lock_workstation()
        self.assertGreaterEqual(call_count[0], 2)

    @unittest.skipIf(sys.platform == "win32", "Linux-specific path")
    def test_shutdown_non_windows_calls_popen(self):
        with patch("subprocess.Popen") as mock_popen:
            lzrd_module.shutdown_computer()
        mock_popen.assert_called()

    @unittest.skipIf(sys.platform == "win32", "Linux-specific path")
    def test_restart_non_windows_calls_popen(self):
        with patch("subprocess.Popen") as mock_popen:
            lzrd_module.restart_computer()
        mock_popen.assert_called()

    @unittest.skipIf(sys.platform == "win32", "Linux-specific path")
    def test_display_message_non_windows_calls_display_tool(self):
        """display_message on Linux should launch a display tool in a thread."""
        triggered = threading.Event()

        def _fake_popen(*args, **kwargs):
            triggered.set()
            return MagicMock()

        with patch("subprocess.Popen", side_effect=_fake_popen):
            lzrd_module.display_message("hello from test")
        self.assertTrue(triggered.wait(timeout=2.0))

    @unittest.skipIf(sys.platform == "win32", "Linux-specific path")
    def test_launch_app_uses_posix_splitting_on_linux(self):
        """On Linux, shlex should be called with posix=True."""
        with patch("subprocess.Popen") as mock_popen:
            lzrd_module.launch_app("echo hello world")
        mock_popen.assert_called_once()
        called_args = mock_popen.call_args[0][0]
        # posix=True splits correctly into three tokens
        self.assertEqual(called_args, ["echo", "hello", "world"])

    @unittest.skipIf(sys.platform == "win32", "Linux-specific path")
    def test_get_cursor_pos_non_windows(self):
        """_get_cursor_pos on Linux should return an (int, int) tuple via pynput."""
        fake_controller = MagicMock()
        fake_controller.position = (123.7, 456.2)
        # The test pynput stub only defines Listener; use create=True to add Controller.
        with patch.object(
            lzrd_module.pynput_mouse, "Controller", return_value=fake_controller, create=True
        ):
            pos = lzrd_module._get_cursor_pos()
        self.assertEqual(pos, (123, 456))


# ---------------------------------------------------------------------------
# TestCheckToken
# ---------------------------------------------------------------------------

class TestCheckToken(unittest.TestCase):
    """Unit tests for the _check_token helper."""

    def _make_req(self, header_token: str = "", query_token: str = "") -> MagicMock:
        req = MagicMock()
        req.headers.get.return_value = header_token
        req.args.get.return_value = query_token
        return req

    def test_empty_global_token_always_denies(self):
        """If _token is not configured (empty), all requests must be denied."""
        original = lzrd_module._token
        try:
            lzrd_module._token = ""
            self.assertFalse(lzrd_module._check_token(self._make_req("")))
        finally:
            lzrd_module._token = original

    def test_correct_header_token_accepted(self):
        original = lzrd_module._token
        try:
            lzrd_module._token = "secret"
            self.assertTrue(lzrd_module._check_token(self._make_req(header_token="secret")))
        finally:
            lzrd_module._token = original

    def test_correct_query_token_accepted(self):
        original = lzrd_module._token
        try:
            lzrd_module._token = "secret"
            self.assertTrue(lzrd_module._check_token(self._make_req(query_token="secret")))
        finally:
            lzrd_module._token = original

    def test_wrong_token_rejected(self):
        original = lzrd_module._token
        try:
            lzrd_module._token = "secret"
            self.assertFalse(lzrd_module._check_token(self._make_req("wrong")))
        finally:
            lzrd_module._token = original


if __name__ == "__main__":
    unittest.main()
