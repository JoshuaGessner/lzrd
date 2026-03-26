"""
LZRD — Mouse-movement tripwire for Windows.

When armed, LZRD watches for mouse movement. The moment the mouse moves beyond
the configured threshold it:
  1. Sends an SMS alert to your phone via Twilio.
  2. Starts polling your Twilio number for an inbound reply.
  3. If the reply contains the configured lock keyword (default: "lock"), it
     immediately locks the Windows workstation.

Usage:
  1. Copy config.ini.example to config.ini and fill in your credentials.
  2. Run:  python lzrd.py
  3. Right-click the system-tray icon and choose "Arm".
  4. Walk away.
"""

import configparser
import ctypes
import sys
import threading
from collections.abc import Callable
from pathlib import Path

import pystray
from PIL import Image, ImageDraw
from pynput import mouse as pynput_mouse
from twilio.rest import Client

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CONFIG_FILE = Path(__file__).parent / "config.ini"
POLL_INTERVAL = 5  # seconds between inbound-SMS polls

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def load_config() -> configparser.ConfigParser:
    """Load and return the INI config.  Exits with a helpful message if missing."""
    config = configparser.ConfigParser()
    if not CONFIG_FILE.exists():
        print(
            f"[LZRD] Config file not found: {CONFIG_FILE}\n"
            "Copy config.ini.example to config.ini and fill in your credentials."
        )
        sys.exit(1)
    config.read(CONFIG_FILE, encoding="utf-8")
    return config


# ---------------------------------------------------------------------------
# Tray icon image
# ---------------------------------------------------------------------------


def _make_icon_image(armed: bool) -> Image.Image:
    """Return a small PIL image used as the system-tray icon."""
    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Body
    body_color = (0, 210, 60) if armed else (180, 180, 180)
    draw.ellipse([8, 20, 52, 48], fill=body_color)
    # Head
    draw.ellipse([40, 10, 60, 34], fill=body_color)
    # Eye
    draw.ellipse([50, 14, 57, 21], fill=(255, 255, 255))
    draw.ellipse([52, 16, 56, 20], fill=(0, 0, 0))
    # Tail
    draw.polygon([(8, 34), (0, 54), (14, 40)], fill=body_color)

    return img


# ---------------------------------------------------------------------------
# Windows helpers
# ---------------------------------------------------------------------------


def _get_cursor_pos() -> tuple[int, int]:
    """Return the current (x, y) cursor position using the Win32 API."""

    class _POINT(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

    pt = _POINT()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
    return pt.x, pt.y


def lock_workstation() -> None:
    """Lock the Windows workstation."""
    ctypes.windll.user32.LockWorkStation()


# ---------------------------------------------------------------------------
# Core application class
# ---------------------------------------------------------------------------


class LZRD:
    """Encapsulates all arming, detection, and SMS logic."""

    def __init__(self, config: configparser.ConfigParser) -> None:
        self.config = config

        # Twilio credentials
        self.account_sid = config.get("twilio", "account_sid")
        self.auth_token = config.get("twilio", "auth_token")
        self.from_number = config.get("twilio", "from_number")
        self.to_number = config.get("twilio", "to_number")
        self.lock_keyword = config.get("lzrd", "lock_keyword", fallback="lock").lower()
        self.movement_threshold = config.getint("lzrd", "movement_threshold", fallback=10)

        self.twilio_client = Client(self.account_sid, self.auth_token)

        # Runtime state
        self.armed = False
        self.alert_sent = False
        self._initial_pos: tuple[int, int] | None = None
        self._mouse_listener: pynput_mouse.Listener | None = None
        self._stop_event = threading.Event()
        self._poll_thread: threading.Thread | None = None
        self._last_message_sid: str | None = None

        # Callback set by the tray setup so the menu can be refreshed
        self.on_state_change: Callable[[], None] = lambda: None

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def arm(self) -> None:
        """Arm: capture cursor position and start monitoring."""
        if self.armed:
            return
        self._initial_pos = _get_cursor_pos()
        self.alert_sent = False
        self._stop_event.clear()
        self.armed = True
        self._start_mouse_listener()
        self.on_state_change()

    def disarm(self) -> None:
        """Disarm: stop monitoring."""
        if not self.armed:
            return
        self.armed = False
        self.alert_sent = False
        self._stop_event.set()
        self._stop_mouse_listener()
        self.on_state_change()

    # ------------------------------------------------------------------
    # Mouse monitoring
    # ------------------------------------------------------------------

    def _start_mouse_listener(self) -> None:
        self._mouse_listener = pynput_mouse.Listener(on_move=self._on_move)
        self._mouse_listener.start()

    def _stop_mouse_listener(self) -> None:
        if self._mouse_listener is not None:
            self._mouse_listener.stop()
            self._mouse_listener = None

    def _on_move(self, x: int, y: int) -> None:
        if not self.armed or self.alert_sent or self._initial_pos is None:
            return
        ix, iy = self._initial_pos
        if abs(x - ix) > self.movement_threshold or abs(y - iy) > self.movement_threshold:
            self.alert_sent = True
            self._send_alert()
            self._start_sms_poll()
            self.on_state_change()

    # ------------------------------------------------------------------
    # SMS
    # ------------------------------------------------------------------

    def _send_alert(self) -> None:
        """Send the movement-detected SMS alert."""
        try:
            self.twilio_client.messages.create(
                body="LZRD Alert: Mouse movement detected! Reply 'lock' to lock.",
                from_=self.from_number,
                to=self.to_number,
            )
        except Exception as exc:
            print(f"[LZRD] Failed to send SMS alert: {exc}")

    def _start_sms_poll(self) -> None:
        """Start a background thread that polls for a lock reply."""
        self._poll_thread = threading.Thread(
            target=self._poll_for_reply, daemon=True, name="lzrd-sms-poll"
        )
        self._poll_thread.start()

    def _poll_for_reply(self) -> None:
        """Periodically check Twilio for inbound messages containing the lock keyword."""
        while not self._stop_event.is_set() and self.armed:
            try:
                messages = self.twilio_client.messages.list(
                    from_=self.to_number,
                    to=self.from_number,
                    limit=5,
                )
                for msg in messages:
                    if msg.sid == self._last_message_sid:
                        break
                    if self.lock_keyword in msg.body.lower():
                        self._last_message_sid = msg.sid
                        lock_workstation()
                        self.disarm()
                        return
                if messages:
                    self._last_message_sid = messages[0].sid
            except Exception as exc:
                print(f"[LZRD] SMS poll error: {exc}")

            self._stop_event.wait(POLL_INTERVAL)


# ---------------------------------------------------------------------------
# System-tray entry point
# ---------------------------------------------------------------------------


def main() -> None:
    config = load_config()
    lzrd = LZRD(config)

    # ---- Build a fresh menu reflecting current state ----
    def _build_menu() -> pystray.Menu:
        if lzrd.armed:
            arm_label = "Disarm"
            arm_action = lzrd.disarm
        else:
            arm_label = "Arm"
            arm_action = lzrd.arm

        alert_item = (
            pystray.MenuItem("⚠ Alert sent — waiting for reply", None, enabled=False)
            if lzrd.alert_sent
            else None
        )

        items = [
            pystray.MenuItem(arm_label, lambda icon, item: arm_action()),
        ]
        if alert_item:
            items.append(alert_item)
        items += [
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Lock Now", lambda icon, item: lock_workstation()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Exit", lambda icon, item: icon.stop()),
        ]
        return pystray.Menu(*items)

    def _refresh_tray() -> None:
        """Update icon image, tooltip, and menu to reflect current state."""
        icon.icon = _make_icon_image(lzrd.armed)
        icon.title = "LZRD — Armed 🟢" if lzrd.armed else "LZRD — Disarmed 🔴"
        icon.menu = _build_menu()

    # Register the callback so LZRD can trigger refreshes
    lzrd.on_state_change = _refresh_tray

    icon = pystray.Icon(
        name="LZRD",
        icon=_make_icon_image(armed=False),
        title="LZRD — Disarmed 🔴",
        menu=_build_menu(),
    )

    icon.run()


if __name__ == "__main__":
    main()
