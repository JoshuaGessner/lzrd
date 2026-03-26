"""
LZRD — Mouse-movement tripwire for Windows with PWA remote control.

When armed, LZRD watches for mouse movement.  The moment the mouse moves
beyond the configured threshold it broadcasts a real-time alert to all
connected web clients via Server-Sent Events (SSE).

A mobile-first Progressive Web App (PWA) served by the built-in Flask server
lets you:
  • Arm / Disarm the tripwire
  • Lock Screen
  • Lock / Unlock the Mouse cursor
  • Shutdown or Restart the PC
  • Display a message box on the PC screen
  • Launch a custom application

Usage:
  1. Copy config.ini.example to config.ini and set your access token.
  2. Run:  python lzrd.py
  3. Open the URL shown in the tray tooltip on your phone.
  4. Enter the access token from config.ini once — it is stored locally.
  5. Right-click the system-tray icon and choose "Arm", or use the web UI.
"""

import configparser
import ctypes
import json
import logging
import queue
import shlex
import socket
import subprocess
import sys
import threading
from collections.abc import Callable
from pathlib import Path

import pystray
from flask import Flask, Response, jsonify, request, send_from_directory
from PIL import Image, ImageDraw
from pynput import mouse as pynput_mouse

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

CONFIG_FILE = Path(__file__).parent / "config.ini"
WEB_DIR = Path(__file__).parent / "web"

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def load_config() -> configparser.ConfigParser:
    """Load and return the INI config.  Exits with a helpful message if missing."""
    config = configparser.ConfigParser()
    if not CONFIG_FILE.exists():
        print(
            f"[LZRD] Config file not found: {CONFIG_FILE}\n"
            "Copy config.ini.example to config.ini and fill in your settings."
        )
        sys.exit(1)
    config.read(CONFIG_FILE, encoding="utf-8")
    return config


# ---------------------------------------------------------------------------
# Icon helpers
# ---------------------------------------------------------------------------


def _make_icon_image(armed: bool) -> Image.Image:
    """Return a small PIL image used as the system-tray icon."""
    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    body_color = (0, 210, 60) if armed else (180, 180, 180)
    draw.ellipse([8, 20, 52, 48], fill=body_color)
    draw.ellipse([40, 10, 60, 34], fill=body_color)
    draw.ellipse([50, 14, 57, 21], fill=(255, 255, 255))
    draw.ellipse([52, 16, 56, 20], fill=(0, 0, 0))
    draw.polygon([(8, 34), (0, 54), (14, 40)], fill=body_color)
    return img


def _make_pwa_icon(size: int) -> Image.Image:
    """Return a PWA-sized icon (dark background) for the web manifest."""
    img = Image.new("RGBA", (size, size), (13, 17, 23, 255))
    draw = ImageDraw.Draw(img)
    s = size / 64
    c = (0, 210, 60)
    draw.ellipse([int(8 * s), int(20 * s), int(52 * s), int(48 * s)], fill=c)
    draw.ellipse([int(40 * s), int(10 * s), int(60 * s), int(34 * s)], fill=c)
    draw.ellipse([int(50 * s), int(14 * s), int(57 * s), int(21 * s)], fill=(255, 255, 255))
    draw.ellipse([int(52 * s), int(16 * s), int(56 * s), int(20 * s)], fill=(0, 0, 0))
    draw.polygon(
        [(int(8 * s), int(34 * s)), (int(0 * s), int(54 * s)), (int(14 * s), int(40 * s))],
        fill=c,
    )
    return img


def _ensure_pwa_icons() -> None:
    """Generate PWA icons into web/icons/ if they do not exist."""
    try:
        icons_dir = WEB_DIR / "icons"
        icons_dir.mkdir(parents=True, exist_ok=True)
        for px in (192, 512):
            path = icons_dir / f"icon-{px}.png"
            if not path.exists():
                _make_pwa_icon(px).save(str(path))
    except Exception as exc:
        print(f"[LZRD] Warning: could not generate PWA icons: {exc}")


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


def lock_mouse_cursor() -> None:
    """Confine the mouse cursor to a 1×1 pixel area at its current position."""

    class _RECT(ctypes.Structure):
        _fields_ = [
            ("left", ctypes.c_long),
            ("top", ctypes.c_long),
            ("right", ctypes.c_long),
            ("bottom", ctypes.c_long),
        ]

    x, y = _get_cursor_pos()
    rect = _RECT(x, y, x + 1, y + 1)
    ctypes.windll.user32.ClipCursor(ctypes.byref(rect))


def unlock_mouse_cursor() -> None:
    """Release the mouse cursor confinement."""
    ctypes.windll.user32.ClipCursor(None)


def shutdown_computer() -> None:
    """Initiate a Windows shutdown with a 5-second delay."""
    subprocess.Popen(["shutdown", "/s", "/t", "5"])


def restart_computer() -> None:
    """Initiate a Windows restart with a 5-second delay."""
    subprocess.Popen(["shutdown", "/r", "/t", "5"])


# Windows MessageBox flags
_MB_ICONINFORMATION = 0x40
_MB_SETFOREGROUND = 0x1000


def display_message(text: str) -> None:
    """Show a Windows message box (non-blocking, runs in its own thread)."""

    def _show() -> None:
        ctypes.windll.user32.MessageBoxW(
            None, text, "LZRD Message", _MB_ICONINFORMATION | _MB_SETFOREGROUND
        )

    threading.Thread(target=_show, daemon=True, name="lzrd-msgbox").start()


def launch_app(path: str) -> None:
    """Launch an application or command on the PC.

    The command is split using shell-style quoting (``shlex.split``) so that
    paths containing spaces can be quoted, e.g. ``"C:\\Program Files\\app.exe"``.
    ``shell=False`` is used to prevent shell injection attacks.
    """
    try:
        args = shlex.split(path, posix=False)
    except ValueError:
        args = [path]
    subprocess.Popen(args, shell=False)


def get_local_ip() -> str:
    """Return the best-guess LAN IP address of this machine."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"


# ---------------------------------------------------------------------------
# SSE event bus
# ---------------------------------------------------------------------------

_event_queues: list[queue.Queue] = []
_event_queues_lock = threading.Lock()


def _broadcast(event: dict) -> None:
    """Push *event* to every active SSE client queue."""
    with _event_queues_lock:
        for q in list(_event_queues):
            q.put_nowait(event)


# ---------------------------------------------------------------------------
# Flask application
# ---------------------------------------------------------------------------

_flask_app = Flask(__name__, static_folder=None)

# Set by main() before the server starts
_lzrd: "LZRD | None" = None
_token: str = ""


def _check_token(req: "request") -> bool:
    tok = req.headers.get("X-Token", "") or req.args.get("token", "")
    return tok == _token


def _unauthorized() -> tuple:
    return jsonify({"error": "unauthorized"}), 401


@_flask_app.route("/")
def index():
    return send_from_directory(WEB_DIR, "index.html")


@_flask_app.route("/<path:filename>")
def static_files(filename: str):
    return send_from_directory(WEB_DIR, filename)


@_flask_app.route("/api/status")
def api_status():
    if not _check_token(request):
        return _unauthorized()
    return jsonify(
        {
            "armed": _lzrd.armed if _lzrd else False,
            "alert": _lzrd.alert_triggered if _lzrd else False,
            "mouse_locked": _lzrd.mouse_locked if _lzrd else False,
        }
    )


@_flask_app.route("/api/arm", methods=["POST"])
def api_arm():
    if not _check_token(request):
        return _unauthorized()
    if _lzrd:
        _lzrd.arm()
    return jsonify({"ok": True})


@_flask_app.route("/api/disarm", methods=["POST"])
def api_disarm():
    if not _check_token(request):
        return _unauthorized()
    if _lzrd:
        _lzrd.disarm()
    return jsonify({"ok": True})


@_flask_app.route("/api/lock-screen", methods=["POST"])
def api_lock_screen():
    if not _check_token(request):
        return _unauthorized()
    lock_workstation()
    return jsonify({"ok": True})


@_flask_app.route("/api/lock-mouse", methods=["POST"])
def api_lock_mouse():
    if not _check_token(request):
        return _unauthorized()
    if _lzrd:
        _lzrd.toggle_mouse_lock()
    return jsonify({"ok": True, "mouse_locked": _lzrd.mouse_locked if _lzrd else False})


@_flask_app.route("/api/shutdown", methods=["POST"])
def api_shutdown():
    if not _check_token(request):
        return _unauthorized()
    shutdown_computer()
    return jsonify({"ok": True})


@_flask_app.route("/api/restart", methods=["POST"])
def api_restart():
    if not _check_token(request):
        return _unauthorized()
    restart_computer()
    return jsonify({"ok": True})


@_flask_app.route("/api/message", methods=["POST"])
def api_message():
    if not _check_token(request):
        return _unauthorized()
    data = request.get_json(silent=True) or {}
    text = str(data.get("text", "")).strip()
    if not text:
        return jsonify({"error": "text is required"}), 400
    display_message(text)
    return jsonify({"ok": True})


@_flask_app.route("/api/launch", methods=["POST"])
def api_launch():
    if not _check_token(request):
        return _unauthorized()
    data = request.get_json(silent=True) or {}
    path = str(data.get("path", "")).strip()
    if not path:
        return jsonify({"error": "path is required"}), 400
    launch_app(path)
    return jsonify({"ok": True})


@_flask_app.route("/api/events")
def api_events():
    if not _check_token(request):
        return _unauthorized()

    def stream():
        q: queue.Queue = queue.Queue()
        with _event_queues_lock:
            _event_queues.append(q)
        try:
            # Send current state immediately on connect
            initial = {
                "type": "state",
                "armed": _lzrd.armed if _lzrd else False,
                "alert": _lzrd.alert_triggered if _lzrd else False,
                "mouse_locked": _lzrd.mouse_locked if _lzrd else False,
            }
            yield f"data: {json.dumps(initial)}\n\n"
            while True:
                try:
                    event = q.get(timeout=25)
                    yield f"data: {json.dumps(event)}\n\n"
                except queue.Empty:
                    yield ": heartbeat\n\n"
        finally:
            with _event_queues_lock:
                try:
                    _event_queues.remove(q)
                except ValueError:
                    pass

    return Response(
        stream(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---------------------------------------------------------------------------
# Core application class
# ---------------------------------------------------------------------------


class LZRD:
    """Mouse-movement tripwire with SSE-based web remote control."""

    def __init__(self, config: configparser.ConfigParser) -> None:
        self.movement_threshold = config.getint("lzrd", "movement_threshold", fallback=10)

        # Runtime state
        self.armed = False
        self.alert_triggered = False
        self.mouse_locked = False
        self._initial_pos: tuple[int, int] | None = None
        self._mouse_listener: pynput_mouse.Listener | None = None
        self._stop_event = threading.Event()

        # Callback refreshes the tray icon/menu
        self.on_state_change: Callable[[], None] = lambda: None

    # ------------------------------------------------------------------
    # Arm / Disarm
    # ------------------------------------------------------------------

    def arm(self) -> None:
        """Capture the current cursor position and start monitoring."""
        if self.armed:
            return
        self._initial_pos = _get_cursor_pos()
        self.alert_triggered = False
        self._stop_event.clear()
        self.armed = True
        self._start_mouse_listener()
        self.on_state_change()
        _broadcast(
            {
                "type": "state",
                "armed": True,
                "alert": False,
                "mouse_locked": self.mouse_locked,
            }
        )

    def disarm(self) -> None:
        """Stop monitoring."""
        if not self.armed:
            return
        self.armed = False
        self.alert_triggered = False
        self._stop_event.set()
        self._stop_mouse_listener()
        self.on_state_change()
        _broadcast(
            {
                "type": "state",
                "armed": False,
                "alert": False,
                "mouse_locked": self.mouse_locked,
            }
        )

    # ------------------------------------------------------------------
    # Mouse lock
    # ------------------------------------------------------------------

    def toggle_mouse_lock(self) -> None:
        """Toggle cursor confinement on/off."""
        if self.mouse_locked:
            unlock_mouse_cursor()
            self.mouse_locked = False
        else:
            lock_mouse_cursor()
            self.mouse_locked = True
        self.on_state_change()
        _broadcast(
            {
                "type": "state",
                "armed": self.armed,
                "alert": self.alert_triggered,
                "mouse_locked": self.mouse_locked,
            }
        )

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
        if not self.armed or self.alert_triggered or self._initial_pos is None:
            return
        ix, iy = self._initial_pos
        if abs(x - ix) > self.movement_threshold or abs(y - iy) > self.movement_threshold:
            self.alert_triggered = True
            self.on_state_change()
            _broadcast(
                {
                    "type": "alert",
                    "armed": True,
                    "alert": True,
                    "mouse_locked": self.mouse_locked,
                }
            )


# ---------------------------------------------------------------------------
# Flask server runner
# ---------------------------------------------------------------------------


def _run_flask(port: int) -> None:
    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    _flask_app.run(host="0.0.0.0", port=port, threaded=True, use_reloader=False, debug=False)


# ---------------------------------------------------------------------------
# System-tray entry point
# ---------------------------------------------------------------------------


def main() -> None:
    global _lzrd, _token

    config = load_config()
    _token = config.get("server", "token", fallback="changeme")
    port = config.getint("server", "port", fallback=7734)
    local_ip = get_local_ip()
    server_url = f"http://{local_ip}:{port}"

    _ensure_pwa_icons()

    _lzrd = LZRD(config)

    # Start Flask in a background daemon thread
    threading.Thread(
        target=_run_flask, args=(port,), daemon=True, name="lzrd-web"
    ).start()

    # ---- Tray menu ----
    def _build_menu() -> pystray.Menu:
        arm_label = "Disarm" if _lzrd.armed else "Arm"
        arm_action = _lzrd.disarm if _lzrd.armed else _lzrd.arm

        def _show_token(icon, item) -> None:
            display_message(f"Access token:\n\n{_token}")

        return pystray.Menu(
            pystray.MenuItem(server_url, lambda icon, item: None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(arm_label, lambda icon, item: arm_action()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Show Access Token", _show_token),
            pystray.MenuItem("Lock Screen Now", lambda icon, item: lock_workstation()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Exit", lambda icon, item: icon.stop()),
        )

    def _refresh_tray() -> None:
        icon.icon = _make_icon_image(_lzrd.armed)
        icon.title = f"LZRD — {'Armed 🟢' if _lzrd.armed else 'Disarmed 🔴'} | {server_url}"
        icon.menu = _build_menu()

    _lzrd.on_state_change = _refresh_tray

    icon = pystray.Icon(
        name="LZRD",
        icon=_make_icon_image(armed=False),
        title=f"LZRD — Disarmed 🔴 | {server_url}",
        menu=_build_menu(),
    )

    icon.run()


if __name__ == "__main__":
    main()
