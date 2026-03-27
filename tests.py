"""
Unit tests for LZRD core logic and Flask API.
These tests mock all platform-specific dependencies so they run on any OS.
"""

import configparser
import queue
import sys
import tempfile
import threading
import types
import unittest
from pathlib import Path
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
    cfg["auth"] = {"owner_username": "", "owner_password_hash": ""}
    return cfg


# ---------------------------------------------------------------------------
# TestLoadConfig
# ---------------------------------------------------------------------------

class TestLoadConfig(unittest.TestCase):
    """Tests for load_config() auto-creation behaviour."""

    def test_creates_config_when_missing(self):
        """load_config() should create config.ini if it does not exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.ini"
            with patch.object(lzrd_module, "CONFIG_FILE", config_path):
                cfg = lzrd_module.load_config()
            self.assertTrue(config_path.exists())
            self.assertIn("server", cfg)
            self.assertIn("lzrd", cfg)

    def test_auto_generated_token_is_non_empty(self):
        """The auto-generated token must be a non-empty string."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.ini"
            with patch.object(lzrd_module, "CONFIG_FILE", config_path):
                cfg = lzrd_module.load_config()
            token = cfg.get("server", "token")
            self.assertTrue(token)

    def test_auto_generated_token_is_not_insecure_default(self):
        """The auto-generated token must never be the insecure default."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.ini"
            with patch.object(lzrd_module, "CONFIG_FILE", config_path):
                cfg = lzrd_module.load_config()
            token = cfg.get("server", "token")
            self.assertNotEqual(token, lzrd_module._INSECURE_DEFAULT_TOKEN)

    def test_existing_config_is_loaded_unchanged(self):
        """If config.ini already exists, load_config() must read it without overwriting."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.ini"
            # Write a pre-existing config
            existing = configparser.ConfigParser()
            existing["server"] = {"port": "9999", "token": "mytoken"}
            existing["lzrd"] = {"movement_threshold": "5"}
            with config_path.open("w", encoding="utf-8") as fh:
                existing.write(fh)

            with patch.object(lzrd_module, "CONFIG_FILE", config_path):
                cfg = lzrd_module.load_config()

            self.assertEqual(cfg.get("server", "token"), "mytoken")
            self.assertEqual(cfg.getint("server", "port"), 9999)


# ---------------------------------------------------------------------------
# TestProxyConfig
# ---------------------------------------------------------------------------

class TestProxyConfig(unittest.TestCase):
    """Tests for the behind_proxy and public_url configuration options."""

    def _make_config_with_proxy(self, behind_proxy: str = "false", public_url: str = "") -> configparser.ConfigParser:
        cfg = configparser.ConfigParser()
        server_section = {"port": "7734", "token": "testtoken", "behind_proxy": behind_proxy}
        if public_url:
            server_section["public_url"] = public_url
        cfg["server"] = server_section
        cfg["lzrd"] = {"movement_threshold": "10"}
        return cfg

    def test_behind_proxy_defaults_to_false(self):
        """behind_proxy should default to False when absent from config."""
        cfg = configparser.ConfigParser()
        cfg["server"] = {"port": "7734", "token": "testtoken"}
        cfg["lzrd"] = {"movement_threshold": "10"}
        self.assertFalse(cfg.getboolean("server", "behind_proxy", fallback=False))

    def test_behind_proxy_true_is_read_correctly(self):
        """behind_proxy = true should be readable as a boolean True."""
        cfg = self._make_config_with_proxy(behind_proxy="true")
        self.assertTrue(cfg.getboolean("server", "behind_proxy", fallback=False))

    def test_public_url_is_read_when_set(self):
        """public_url should be readable from config when present."""
        cfg = self._make_config_with_proxy(public_url="https://lzrd.example.com")
        val = cfg.get("server", "public_url", fallback="").strip()
        self.assertEqual(val, "https://lzrd.example.com")

    def test_public_url_defaults_to_empty(self):
        """public_url should default to an empty string when absent."""
        cfg = self._make_config_with_proxy()
        val = cfg.get("server", "public_url", fallback="").strip()
        self.assertEqual(val, "")


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
        lzrd_module._token_bytes = b"testtoken"
        lzrd_module._owner_username = ""
        lzrd_module._owner_password_hash = ""
        lzrd_module._setup_keyword = "amber-blade"
        lzrd_module._config = self.cfg
        lzrd_module._lzrd = lzrd_module.LZRD(self.cfg)
        lzrd_module._lzrd.on_state_change = MagicMock()
        lzrd_module._failed_auth.clear()   # reset rate-limit state between tests
        self.client = lzrd_module._flask_app.test_client()

    def tearDown(self):
        lzrd_module._lzrd = None
        lzrd_module._failed_auth.clear()
        lzrd_module._owner_username = ""
        lzrd_module._owner_password_hash = ""
        lzrd_module._setup_keyword = ""
        lzrd_module._config = None

    # Helpers
    def _get(self, url: str, token: str = "testtoken"):
        return self.client.get(url, headers={"X-Token": token})

    def _post(self, url: str, body=None, token: str = "testtoken"):
        return self.client.post(url, json=body or {}, headers={"X-Token": token})

    # Authentication
    def test_bootstrap_status_requires_setup_when_owner_missing(self):
        r = self.client.get("/api/auth/bootstrap-status")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.get_json()["requires_setup"])

    def test_setup_then_cookie_auth_allows_status(self):
        r = self.client.post(
            "/api/auth/setup",
            json={"username": "owner", "password": "strongpass123", "setup_code": "amber-blade"},
        )
        self.assertEqual(r.status_code, 200)
        # Same test client keeps response cookies automatically.
        r2 = self.client.get("/api/status")
        self.assertEqual(r2.status_code, 200)

    def test_setup_requires_valid_setup_code(self):
        r = self.client.post(
            "/api/auth/setup",
            json={"username": "owner", "password": "strongpass123", "setup_code": "wrong-code"},
        )
        self.assertEqual(r.status_code, 401)

    def test_login_sets_cookie_and_allows_status(self):
        setup_client = lzrd_module._flask_app.test_client()
        setup_client.post(
            "/api/auth/setup",
            json={"username": "owner", "password": "strongpass123", "setup_code": "amber-blade"},
        )

        login_client = lzrd_module._flask_app.test_client()
        r = login_client.post(
            "/api/auth/login",
            json={"username": "owner", "password": "strongpass123"},
        )
        self.assertEqual(r.status_code, 200)

        r2 = login_client.get("/api/status")
        self.assertEqual(r2.status_code, 200)

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

    def test_status_includes_platform(self):
        """The /api/status response must include the current platform string."""
        import platform as platform_module
        r = self._get("/api/status")
        self.assertEqual(r.status_code, 200)
        data = r.get_json()
        self.assertIn("platform", data)
        self.assertEqual(data["platform"], platform_module.system())

    # Arm / Disarm
    def test_arm_and_disarm(self):
        with patch.object(lzrd_module, "_get_cursor_pos", return_value=(0, 0)):
            r = self._post("/api/arm")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(lzrd_module._lzrd.armed)

        r = self._post("/api/disarm")
        self.assertEqual(r.status_code, 200)
        self.assertFalse(lzrd_module._lzrd.armed)

    def test_events_initial_state_includes_platform(self):
        """The first SSE data frame must include the platform field."""
        import json
        import platform as platform_module

        with lzrd_module._flask_app.app_context():
            # Build the generator exactly as the route does.
            gen = lzrd_module._make_sse_stream()
            first_line = next(gen)  # "data: {...}\n\n"

        self.assertTrue(first_line.startswith("data: "))
        payload = json.loads(first_line[len("data: "):].strip())
        self.assertIn("platform", payload)
        self.assertEqual(payload["platform"], platform_module.system())

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

    def test_load_tray_icon_from_asset_resized(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as tmpdir:
            icon_path = Path(tmpdir) / "icon.png"
            Image.new("RGBA", (192, 192), (255, 0, 0, 255)).save(icon_path)
            with patch.object(lzrd_module, "TRAY_ICON_FILE", icon_path):
                img = lzrd_module._load_tray_icon_image(armed=True)
        self.assertIsInstance(img, Image.Image)
        self.assertEqual(img.size, (64, 64))

    def test_load_tray_icon_falls_back_when_missing(self):
        from PIL import Image
        with patch.object(lzrd_module, "TRAY_ICON_FILE", Path("missing-file.png")):
            img = lzrd_module._load_tray_icon_image(armed=False)
        self.assertIsInstance(img, Image.Image)
        self.assertEqual(img.size, (64, 64))


class TestOwnerReset(unittest.TestCase):
    def test_reset_owner_credentials_clears_config_and_globals(self):
        cfg = _make_config()
        cfg["auth"] = {
            "owner_username": "owner",
            "owner_password_hash": "pbkdf2_sha256$1$aa$bb",
        }
        original_cfg = lzrd_module._config
        original_user = lzrd_module._owner_username
        original_hash = lzrd_module._owner_password_hash
        try:
            lzrd_module._config = cfg
            lzrd_module._owner_username = "owner"
            lzrd_module._owner_password_hash = "pbkdf2_sha256$1$aa$bb"
            self.assertTrue(lzrd_module._reset_owner_credentials())
            self.assertEqual(lzrd_module._owner_username, "")
            self.assertEqual(lzrd_module._owner_password_hash, "")
            self.assertEqual(cfg.get("auth", "owner_username", fallback="x"), "")
            self.assertEqual(cfg.get("auth", "owner_password_hash", fallback="x"), "")
        finally:
            lzrd_module._config = original_cfg
            lzrd_module._owner_username = original_user
            lzrd_module._owner_password_hash = original_hash


# ---------------------------------------------------------------------------
# TestSetupKeyword
# ---------------------------------------------------------------------------

class TestSetupKeyword(unittest.TestCase):
    """Tests for the rolling setup keyword used during first-time owner setup."""

    def setUp(self):
        self._orig = lzrd_module._setup_keyword

    def tearDown(self):
        lzrd_module._setup_keyword = self._orig

    def test_generate_setup_keyword_format(self):
        kw = lzrd_module._generate_setup_keyword()
        parts = kw.split("-")
        self.assertEqual(len(parts), 2)
        self.assertIn(parts[0], lzrd_module._SETUP_WORDS)
        self.assertIn(parts[1], lzrd_module._SETUP_WORDS)

    def test_roll_changes_keyword(self):
        lzrd_module._setup_keyword = "fixed-value"
        lzrd_module._roll_setup_keyword()
        self.assertNotEqual(lzrd_module._setup_keyword, "")
        # Very unlikely (but possible) to get the same value
        parts = lzrd_module._setup_keyword.split("-")
        self.assertEqual(len(parts), 2)

    def test_verify_setup_keyword_case_insensitive(self):
        lzrd_module._setup_keyword = "amber-blade"
        self.assertTrue(lzrd_module._verify_setup_keyword("amber-blade"))
        self.assertTrue(lzrd_module._verify_setup_keyword("AMBER-BLADE"))
        self.assertTrue(lzrd_module._verify_setup_keyword("Amber-Blade"))

    def test_verify_setup_keyword_rejects_wrong(self):
        lzrd_module._setup_keyword = "amber-blade"
        self.assertFalse(lzrd_module._verify_setup_keyword("wrong-code"))
        self.assertFalse(lzrd_module._verify_setup_keyword(""))

    def test_verify_empty_keyword_rejects_all(self):
        lzrd_module._setup_keyword = ""
        self.assertFalse(lzrd_module._verify_setup_keyword("anything"))


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
        req.cookies.get.return_value = ""
        req.remote_addr = "127.0.0.1"
        return req

    def test_empty_global_token_always_denies(self):
        """If _token is not configured (empty), all requests must be denied."""
        original_tok = lzrd_module._token
        original_bytes = lzrd_module._token_bytes
        original_owner_user = lzrd_module._owner_username
        original_owner_hash = lzrd_module._owner_password_hash
        try:
            lzrd_module._token = ""
            lzrd_module._token_bytes = b""
            lzrd_module._owner_username = ""
            lzrd_module._owner_password_hash = ""
            self.assertFalse(lzrd_module._check_token(self._make_req("")))
        finally:
            lzrd_module._token = original_tok
            lzrd_module._token_bytes = original_bytes
            lzrd_module._owner_username = original_owner_user
            lzrd_module._owner_password_hash = original_owner_hash

    def test_correct_header_token_accepted(self):
        original_tok = lzrd_module._token
        original_bytes = lzrd_module._token_bytes
        original_owner_user = lzrd_module._owner_username
        original_owner_hash = lzrd_module._owner_password_hash
        try:
            lzrd_module._token = "secret"
            lzrd_module._token_bytes = b"secret"
            lzrd_module._owner_username = ""
            lzrd_module._owner_password_hash = ""
            self.assertTrue(lzrd_module._check_token(self._make_req(header_token="secret")))
        finally:
            lzrd_module._token = original_tok
            lzrd_module._token_bytes = original_bytes
            lzrd_module._owner_username = original_owner_user
            lzrd_module._owner_password_hash = original_owner_hash

    def test_correct_query_token_accepted(self):
        original_tok = lzrd_module._token
        original_bytes = lzrd_module._token_bytes
        original_owner_user = lzrd_module._owner_username
        original_owner_hash = lzrd_module._owner_password_hash
        try:
            lzrd_module._token = "secret"
            lzrd_module._token_bytes = b"secret"
            lzrd_module._owner_username = ""
            lzrd_module._owner_password_hash = ""
            self.assertTrue(lzrd_module._check_token(self._make_req(query_token="secret")))
        finally:
            lzrd_module._token = original_tok
            lzrd_module._token_bytes = original_bytes
            lzrd_module._owner_username = original_owner_user
            lzrd_module._owner_password_hash = original_owner_hash

    def test_query_token_with_spaces_is_normalized_and_accepted(self):
        original_tok = lzrd_module._token
        original_bytes = lzrd_module._token_bytes
        original_owner_user = lzrd_module._owner_username
        original_owner_hash = lzrd_module._owner_password_hash
        try:
            lzrd_module._token = "secret"
            lzrd_module._token_bytes = b"secret"
            lzrd_module._owner_username = ""
            lzrd_module._owner_password_hash = ""
            self.assertTrue(
                lzrd_module._check_token(self._make_req(query_token=" sec ret "))
            )
        finally:
            lzrd_module._token = original_tok
            lzrd_module._token_bytes = original_bytes
            lzrd_module._owner_username = original_owner_user
            lzrd_module._owner_password_hash = original_owner_hash

    def test_wrong_token_rejected(self):
        original_tok = lzrd_module._token
        original_bytes = lzrd_module._token_bytes
        original_owner_user = lzrd_module._owner_username
        original_owner_hash = lzrd_module._owner_password_hash
        try:
            lzrd_module._token = "secret"
            lzrd_module._token_bytes = b"secret"
            lzrd_module._owner_username = ""
            lzrd_module._owner_password_hash = ""
            self.assertFalse(lzrd_module._check_token(self._make_req("wrong")))
        finally:
            lzrd_module._token = original_tok
            lzrd_module._token_bytes = original_bytes
            lzrd_module._owner_username = original_owner_user
            lzrd_module._owner_password_hash = original_owner_hash

    def test_check_token_uses_constant_time_comparison(self):
        """_check_token must delegate to hmac.compare_digest (not plain ==)."""
        import hmac as hmac_module
        req = self._make_req(header_token="testtoken")
        original_tok = lzrd_module._token
        original_bytes = lzrd_module._token_bytes
        original_owner_user = lzrd_module._owner_username
        original_owner_hash = lzrd_module._owner_password_hash
        try:
            lzrd_module._token = "testtoken"
            lzrd_module._token_bytes = b"testtoken"
            lzrd_module._owner_username = ""
            lzrd_module._owner_password_hash = ""
            with patch.object(hmac_module, "compare_digest", return_value=True) as mock_cd:
                result = lzrd_module._check_token(req)
            mock_cd.assert_called_once()
            self.assertTrue(result)
        finally:
            lzrd_module._token = original_tok
            lzrd_module._token_bytes = original_bytes
            lzrd_module._owner_username = original_owner_user
            lzrd_module._owner_password_hash = original_owner_hash


# ---------------------------------------------------------------------------
# TestSecurity
# ---------------------------------------------------------------------------


class TestSecurity(unittest.TestCase):
    """Rate limiting, security headers, and input-length validation."""

    def setUp(self):
        self.cfg = _make_config()
        lzrd_module._token = "testtoken"
        lzrd_module._token_bytes = b"testtoken"
        lzrd_module._owner_username = ""
        lzrd_module._owner_password_hash = ""
        lzrd_module._setup_keyword = "amber-blade"
        lzrd_module._config = self.cfg
        lzrd_module._lzrd = lzrd_module.LZRD(self.cfg)
        lzrd_module._lzrd.on_state_change = MagicMock()
        lzrd_module._failed_auth.clear()
        self.client = lzrd_module._flask_app.test_client()

    def tearDown(self):
        lzrd_module._lzrd = None
        lzrd_module._failed_auth.clear()
        lzrd_module._owner_username = ""
        lzrd_module._owner_password_hash = ""
        lzrd_module._setup_keyword = ""
        lzrd_module._config = None

    # Helpers
    def _get(self, url: str, token: str = "testtoken"):
        return self.client.get(url, headers={"X-Token": token})

    def _post(self, url: str, body=None, token: str = "testtoken"):
        return self.client.post(url, json=body or {}, headers={"X-Token": token})

    # ── Rate limiting ──────────────────────────────────────────────────────

    def test_rate_limit_blocks_after_too_many_failures(self):
        """After _MAX_FAILED_AUTH failed auth attempts the IP is blocked (429)."""
        for _ in range(lzrd_module._MAX_FAILED_AUTH):
            r = self._get("/api/status", token="wrong")
            self.assertEqual(r.status_code, 401)
        # The next attempt — regardless of token — must be rate-limited.
        r = self._get("/api/status", token="wrong")
        self.assertEqual(r.status_code, 429)

    def test_rate_limit_blocks_correct_token_from_blocked_ip(self):
        """A blocked IP is refused even when presenting the correct token."""
        for _ in range(lzrd_module._MAX_FAILED_AUTH):
            self._get("/api/status", token="wrong")
        r = self._get("/api/status", token="testtoken")
        self.assertEqual(r.status_code, 429)

    def test_correct_token_does_not_accumulate_failures(self):
        """Successful auth never increments the failure counter."""
        for _ in range(lzrd_module._MAX_FAILED_AUTH + 5):
            r = self._get("/api/status")
            self.assertEqual(r.status_code, 200)
        self.assertEqual(len(lzrd_module._failed_auth.get("127.0.0.1", [])), 0)

    def test_rate_limit_does_not_apply_to_static_files(self):
        """The rate limiter must not block requests for static assets."""
        for _ in range(lzrd_module._MAX_FAILED_AUTH):
            self._get("/api/status", token="wrong")
        r = self.client.get("/")
        try:
            self.assertNotEqual(r.status_code, 429)
        finally:
            r.close()

    # ── Security headers ───────────────────────────────────────────────────

    def test_security_headers_on_api_response(self):
        """Authenticated API JSON responses carry the required security headers."""
        r = self._get("/api/status")
        self.assertEqual(r.headers.get("X-Content-Type-Options"), "nosniff")
        self.assertEqual(r.headers.get("X-Frame-Options"), "DENY")
        self.assertIn("default-src 'self'", r.headers.get("Content-Security-Policy", ""))
        self.assertEqual(r.headers.get("Referrer-Policy"), "no-referrer")
        self.assertEqual(r.headers.get("Cache-Control"), "no-store")

    def test_security_headers_on_401_response(self):
        """Unauthorized API responses also carry security headers."""
        r = self._get("/api/status", token="wrong")
        self.assertEqual(r.headers.get("X-Content-Type-Options"), "nosniff")
        self.assertEqual(r.headers.get("X-Frame-Options"), "DENY")

    def test_security_headers_on_static_response(self):
        """Static file responses also carry the defensive headers."""
        r = self.client.get("/")
        try:
            self.assertEqual(r.headers.get("X-Content-Type-Options"), "nosniff")
            self.assertEqual(r.headers.get("X-Frame-Options"), "DENY")
            self.assertIn("default-src 'self'", r.headers.get("Content-Security-Policy", ""))
        finally:
            r.close()

    def test_no_cache_control_no_store_on_sse_route(self):
        """The SSE events endpoint must NOT receive Cache-Control: no-store."""
        # The SSE generator blocks indefinitely; exercise only the auth path.
        r = self._get("/api/events", token="wrong")
        # 401 response — after_request still runs; verify no-store is absent
        # because the /api/events path is excluded from the no-store rule.
        self.assertNotEqual(r.headers.get("Cache-Control"), "no-store")

    # ── Input length limits ────────────────────────────────────────────────

    def test_message_text_too_long_returns_400(self):
        """Messages exceeding _MAX_MESSAGE_LEN are rejected with 400."""
        long_text = "a" * (lzrd_module._MAX_MESSAGE_LEN + 1)
        r = self._post("/api/message", {"text": long_text})
        self.assertEqual(r.status_code, 400)

    def test_message_at_max_length_is_accepted(self):
        """A message exactly at the character limit must be accepted."""
        exact_text = "a" * lzrd_module._MAX_MESSAGE_LEN
        with patch.object(lzrd_module, "display_message"):
            r = self._post("/api/message", {"text": exact_text})
        self.assertEqual(r.status_code, 200)

    def test_launch_path_too_long_returns_400(self):
        """Paths exceeding _MAX_PATH_LEN are rejected with 400."""
        long_path = "a" * (lzrd_module._MAX_PATH_LEN + 1)
        r = self._post("/api/launch", {"path": long_path})
        self.assertEqual(r.status_code, 400)

    def test_launch_path_at_max_length_is_accepted(self):
        """A path exactly at the character limit must be accepted."""
        exact_path = "a" * lzrd_module._MAX_PATH_LEN
        with patch("subprocess.Popen"):
            r = self._post("/api/launch", {"path": exact_path})
        self.assertEqual(r.status_code, 200)


if __name__ == "__main__":
    unittest.main()
