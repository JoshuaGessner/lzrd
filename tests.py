"""
Unit tests for LZRD core logic.
These tests mock all platform-specific dependencies so they run on any OS.
"""

import configparser
import sys
import threading
import types
import unittest
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Build a minimal stub for pynput.mouse so the module can be imported
# without a display server.
# ---------------------------------------------------------------------------

def _make_pynput_stub():
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


def _make_pystray_stub():
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


def _make_twilio_stub():
    twilio = types.ModuleType("twilio")
    twilio_rest = types.ModuleType("twilio.rest")

    class FakeClient:
        def __init__(self, *args, **kwargs):
            self.messages = MagicMock()

    twilio_rest.Client = FakeClient
    twilio.rest = twilio_rest
    sys.modules["twilio"] = twilio
    sys.modules["twilio.rest"] = twilio_rest


# Install stubs before importing lzrd
_make_pynput_stub()
_make_pystray_stub()
_make_twilio_stub()

# Now we can safely import lzrd
import importlib
import lzrd as lzrd_module


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_config(
    account_sid="ACtest",
    auth_token="token",
    from_number="+10000000000",
    to_number="+19999999999",
    threshold="10",
    lock_keyword="lock",
) -> configparser.ConfigParser:
    cfg = configparser.ConfigParser()
    cfg["twilio"] = {
        "account_sid": account_sid,
        "auth_token": auth_token,
        "from_number": from_number,
        "to_number": to_number,
    }
    cfg["lzrd"] = {
        "movement_threshold": threshold,
        "lock_keyword": lock_keyword,
    }
    return cfg


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestLZRDArming(unittest.TestCase):
    def setUp(self):
        self.cfg = _make_config()

    def test_initial_state_is_disarmed(self):
        app = lzrd_module.LZRD(self.cfg)
        self.assertFalse(app.armed)
        self.assertFalse(app.alert_sent)

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
        self.assertFalse(app.alert_sent)

    def test_disarm_is_idempotent(self):
        app = lzrd_module.LZRD(self.cfg)
        # Should not raise even when already disarmed
        app.disarm()
        self.assertFalse(app.armed)


class TestLZRDMovementDetection(unittest.TestCase):
    def setUp(self):
        self.cfg = _make_config(threshold="10")

    def _armed_app(self, initial_pos=(100, 100)):
        app = lzrd_module.LZRD(self.cfg)
        app._send_alert = MagicMock()
        app._start_sms_poll = MagicMock()
        app.on_state_change = MagicMock()
        with patch.object(lzrd_module, "_get_cursor_pos", return_value=initial_pos):
            app.arm()
        return app

    def test_small_movement_does_not_trigger_alert(self):
        app = self._armed_app(initial_pos=(100, 100))
        app._on_move(105, 105)  # within 10-pixel threshold
        self.assertFalse(app.alert_sent)
        app._send_alert.assert_not_called()

    def test_large_movement_triggers_alert(self):
        app = self._armed_app(initial_pos=(100, 100))
        app._on_move(150, 100)  # 50 px beyond threshold
        self.assertTrue(app.alert_sent)
        app._send_alert.assert_called_once()
        app._start_sms_poll.assert_called_once()

    def test_alert_fires_only_once(self):
        app = self._armed_app(initial_pos=(100, 100))
        app._on_move(200, 200)
        app._on_move(300, 300)
        app._send_alert.assert_called_once()

    def test_movement_not_detected_when_disarmed(self):
        app = self._armed_app(initial_pos=(100, 100))
        app.disarm()
        app._on_move(200, 200)
        app._send_alert.assert_not_called()


class TestLZRDSMSPolling(unittest.TestCase):
    def setUp(self):
        self.cfg = _make_config(lock_keyword="lock")

    def test_lock_keyword_triggers_lock_and_disarm(self):
        app = lzrd_module.LZRD(self.cfg)
        app.armed = True

        # Fake inbound message containing the lock keyword
        fake_msg = MagicMock()
        fake_msg.sid = "SM001"
        fake_msg.body = "Lock the PC please"
        app.twilio_client.messages.list.return_value = [fake_msg]

        lock_called = threading.Event()
        disarm_called = threading.Event()

        with patch.object(lzrd_module, "lock_workstation", side_effect=lambda: lock_called.set()):
            app.disarm = lambda: disarm_called.set()
            app._poll_for_reply()

        self.assertTrue(lock_called.is_set(), "lock_workstation should have been called")
        self.assertTrue(disarm_called.is_set(), "disarm should have been called")

    def test_non_lock_message_does_not_lock(self):
        app = lzrd_module.LZRD(self.cfg)
        app.armed = True
        app._stop_event.set()  # single iteration

        fake_msg = MagicMock()
        fake_msg.sid = "SM002"
        fake_msg.body = "Hello there"
        app.twilio_client.messages.list.return_value = [fake_msg]

        with patch.object(lzrd_module, "lock_workstation") as mock_lock:
            app._poll_for_reply()
            mock_lock.assert_not_called()

    def test_case_insensitive_lock_keyword(self):
        app = lzrd_module.LZRD(self.cfg)
        app.armed = True

        fake_msg = MagicMock()
        fake_msg.sid = "SM003"
        fake_msg.body = "LOCK"
        app.twilio_client.messages.list.return_value = [fake_msg]

        lock_called = threading.Event()
        with patch.object(lzrd_module, "lock_workstation", side_effect=lambda: lock_called.set()):
            app.disarm = MagicMock()
            app._poll_for_reply()

        self.assertTrue(lock_called.is_set())

    def test_already_seen_message_does_not_trigger_lock(self):
        app = lzrd_module.LZRD(self.cfg)
        app.armed = True
        app._stop_event.set()  # single iteration

        fake_msg = MagicMock()
        fake_msg.sid = "SM004"
        fake_msg.body = "lock"
        app._last_message_sid = "SM004"  # already processed
        app.twilio_client.messages.list.return_value = [fake_msg]

        with patch.object(lzrd_module, "lock_workstation") as mock_lock:
            app._poll_for_reply()
            mock_lock.assert_not_called()


class TestIconRendering(unittest.TestCase):
    def test_armed_icon_is_green(self):
        from PIL import Image
        img = lzrd_module._make_icon_image(armed=True)
        self.assertIsInstance(img, Image.Image)
        self.assertEqual(img.size, (64, 64))

    def test_disarmed_icon_is_grey(self):
        from PIL import Image
        img = lzrd_module._make_icon_image(armed=False)
        self.assertIsInstance(img, Image.Image)
        self.assertEqual(img.size, (64, 64))


if __name__ == "__main__":
    unittest.main()
