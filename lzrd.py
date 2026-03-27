"""
LZRD — Mouse-movement tripwire with PWA remote control.

Supports **Windows 10/11** fully and **Linux** (X11/systemd desktops).

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
  1. Run:  python lzrd.py
  2. Open the URL shown in the tray tooltip on your phone.
    3. On first launch, create owner credentials in the web UI; then sign in on future visits.
  4. Right-click the system-tray icon and choose "Arm", or use the web UI.
"""

import argparse
import configparser
import ctypes
import base64
import hashlib
import hmac
import io
import json
import logging
import os
import platform
import queue
import secrets
import shlex
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlparse

import pystray
from flask import Flask, Response, jsonify, request, send_from_directory
from PIL import Image, ImageDraw
from pynput import mouse as pynput_mouse
from werkzeug.middleware.proxy_fix import ProxyFix
from pywebpush import webpush, WebPushException
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization

# ---------------------------------------------------------------------------
# Paths / Platform
# ---------------------------------------------------------------------------

CONFIG_FILE = Path(__file__).parent / "config.ini"
WEB_DIR = Path(__file__).parent / "web"
TRAY_ICON_FILE = WEB_DIR / "icons" / "icon-192.png"

#: Current OS name: "Windows", "Linux", or "Darwin"
PLATFORM = platform.system()

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def load_config() -> configparser.ConfigParser:
    """Load and return the INI config, creating it with a random token on first run."""
    config = configparser.ConfigParser()
    if not CONFIG_FILE.exists():
        token = secrets.token_urlsafe(24)
        config["server"] = {"port": "7734", "token": token}
        config["lzrd"] = {"movement_threshold": "10"}
        config["auth"] = {"owner_username": "", "owner_password_hash": ""}
        with CONFIG_FILE.open("w", encoding="utf-8") as fh:
            config.write(fh)
        print(f"[LZRD] Created {CONFIG_FILE} with a new random access token.")
        return config
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
    body_color = (109, 191, 74) if armed else (100, 100, 100)
    # Body
    draw.ellipse([int(18 * 64 / 64), int(24 * 64 / 64), int(48 * 64 / 64), int(42 * 64 / 64)], fill=body_color)
    # Head
    draw.ellipse([int(38 * 64 / 64), int(20 * 64 / 64), int(56 * 64 / 64), int(34 * 64 / 64)], fill=body_color)
    # Snout
    draw.polygon([(53, 25), (63, 29), (53, 33)], fill=body_color)
    # Eye
    draw.ellipse([50, 22, 55, 27], fill=(255, 255, 255))
    draw.ellipse([51, 23, 54, 26], fill=(0, 0, 0))
    # Tail
    draw.line([(18, 34), (10, 40), (4, 52)], fill=body_color, width=4)
    return img


# Pre-built tray icon cache: populated once at startup by _cache_tray_icons().
_tray_icon_cache: dict[bool, Image.Image] = {}


def _cache_tray_icons() -> None:
    """Build and cache the armed/disarmed tray icons so disk I/O happens once."""
    for state in (True, False):
        _tray_icon_cache[state] = _build_tray_icon_image(state)


def _build_tray_icon_image(armed: bool) -> Image.Image:
    """Build a tray icon image (slow — disk I/O + resize)."""
    try:
        with Image.open(TRAY_ICON_FILE) as src:
            icon = src.convert("RGBA")
        try:
            resampling = Image.Resampling.LANCZOS
        except AttributeError:
            resampling = Image.LANCZOS
        icon = icon.resize((64, 64), resampling)
        if not armed:
            overlay = Image.new("RGBA", icon.size, (0, 0, 0, 110))
            icon = Image.alpha_composite(icon, overlay)
        return icon
    except Exception:
        return _make_icon_image(armed)


def _load_tray_icon_image(armed: bool) -> Image.Image:
    """Return the cached tray icon for the given state."""
    cached = _tray_icon_cache.get(armed)
    if cached is not None:
        return cached
    return _build_tray_icon_image(armed)


# ---------------------------------------------------------------------------
# Cross-platform system helpers
# ---------------------------------------------------------------------------

# Linux-only: event used to stop the mouse-lock background thread.
_linux_mouse_lock_stop: threading.Event = threading.Event()


def _get_cursor_pos() -> tuple[int, int]:
    """Return the current (x, y) cursor position."""
    if PLATFORM == "Windows":
        class _POINT(ctypes.Structure):
            _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

        pt = _POINT()
        ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
        return pt.x, pt.y
    else:
        # pynput.mouse.Controller is cross-platform (X11, Wayland, macOS)
        pos = pynput_mouse.Controller().position
        return (int(pos[0]), int(pos[1]))


def lock_workstation() -> None:
    """Lock the screen / workstation."""
    if PLATFORM == "Windows":
        ctypes.windll.user32.LockWorkStation()
    else:
        # Try common Linux screen-lockers in preference order.
        for cmd in [
            ["loginctl", "lock-session"],
            ["xdg-screensaver", "lock"],
            ["gnome-screensaver-command", "--lock"],
            ["xscreensaver-command", "-lock"],
            ["cinnamon-screensaver-command", "--lock"],
            ["dm-tool", "lock"],
        ]:
            try:
                subprocess.Popen(cmd)
                return
            except FileNotFoundError:
                continue
        print("[LZRD] Warning: no supported screen-locker found on this system.")


def lock_mouse_cursor() -> None:
    """Confine the mouse cursor to its current position."""
    if PLATFORM == "Windows":
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
    else:
        # Use a daemon thread that continuously resets the cursor position.
        _linux_mouse_lock_stop.clear()
        lx, ly = _get_cursor_pos()

        def _keep_locked() -> None:
            ctrl = pynput_mouse.Controller()
            while not _linux_mouse_lock_stop.is_set():
                cx, cy = int(ctrl.position[0]), int(ctrl.position[1])
                if abs(cx - lx) > 1 or abs(cy - ly) > 1:
                    ctrl.position = (lx, ly)
                _linux_mouse_lock_stop.wait(0.05)

        threading.Thread(
            target=_keep_locked, daemon=True, name="lzrd-mouselock"
        ).start()


def unlock_mouse_cursor() -> None:
    """Release the mouse cursor confinement."""
    if PLATFORM == "Windows":
        ctypes.windll.user32.ClipCursor(None)
    else:
        _linux_mouse_lock_stop.set()


def shutdown_computer() -> None:
    """Initiate a system shutdown."""
    if PLATFORM == "Windows":
        subprocess.Popen(["shutdown", "/s", "/t", "5"])
    else:
        for cmd in [["systemctl", "poweroff"], ["shutdown", "-h", "now"]]:
            try:
                subprocess.Popen(cmd)
                return
            except FileNotFoundError:
                continue


def restart_computer() -> None:
    """Initiate a system restart."""
    if PLATFORM == "Windows":
        subprocess.Popen(["shutdown", "/r", "/t", "5"])
    else:
        for cmd in [["systemctl", "reboot"], ["shutdown", "-r", "now"]]:
            try:
                subprocess.Popen(cmd)
                return
            except FileNotFoundError:
                continue


# Windows MessageBox flags
_MB_ICONINFORMATION = 0x40
_MB_SETFOREGROUND = 0x1000


# Serialized message popup queue — ensures only one tkinter instance at a time.
_message_queue: queue.Queue[str] = queue.Queue()
_message_worker_started = False
_message_worker_lock = threading.Lock()


def _message_worker() -> None:
    """Drain _message_queue sequentially so only one popup is open at a time."""
    while True:
        text = _message_queue.get()
        try:
            _show_message_sync(text)
        except Exception:
            pass


def _ensure_message_worker() -> None:
    """Start the message worker thread on first use."""
    global _message_worker_started
    with _message_worker_lock:
        if _message_worker_started:
            return
        _message_worker_started = True
        threading.Thread(target=_message_worker, daemon=True, name="lzrd-msgbox").start()


def _show_message_sync(text: str) -> None:
    """Show a message popup synchronously (called from the message worker thread)."""
    if PLATFORM == "Windows":
        try:
            import tkinter as tk

            root = tk.Tk()
            root.withdraw()

            dialog = tk.Toplevel(root)
            dialog.title("LZRD Message")
            dialog.resizable(False, False)
            dialog.attributes("-topmost", True)

            try:
                icon_image = tk.PhotoImage(file=str(TRAY_ICON_FILE))
                root.iconphoto(True, icon_image)
                dialog.iconphoto(True, icon_image)
                dialog._lzrd_icon_image = icon_image  # type: ignore[attr-defined]
            except Exception:
                pass

            container = tk.Frame(dialog, padx=14, pady=12)
            container.pack(fill="both", expand=True)

            label = tk.Label(
                container,
                text=text,
                justify="left",
                anchor="w",
                wraplength=420,
            )
            label.pack(fill="x", expand=True)

            def _close_dialog() -> None:
                try:
                    dialog.destroy()
                finally:
                    root.quit()

            btn = tk.Button(container, text="OK", width=10, command=_close_dialog)
            btn.pack(pady=(12, 0))
            dialog.protocol("WM_DELETE_WINDOW", _close_dialog)

            dialog.update_idletasks()
            width = dialog.winfo_width()
            height = dialog.winfo_height()
            x = max((dialog.winfo_screenwidth() - width) // 2, 0)
            y = max((dialog.winfo_screenheight() - height) // 2, 0)
            dialog.geometry(f"{width}x{height}+{x}+{y}")

            dialog.deiconify()
            dialog.lift()
            dialog.focus_force()
            btn.focus_set()

            root.mainloop()
            root.destroy()
        except Exception:
            ctypes.windll.user32.MessageBoxW(
                None, text, "LZRD Message", _MB_ICONINFORMATION | _MB_SETFOREGROUND
            )
        finally:
            if _lzrd and _lzrd.mouse_locked:
                lock_mouse_cursor()
    else:
        for cmd in [
            ["zenity", "--info", f"--title=LZRD Message", f"--text={text}", "--no-wrap"],
            ["kdialog", "--title", "LZRD Message", "--msgbox", text],
            ["notify-send", "LZRD Message", text],
            ["xmessage", "-center", text],
        ]:
            try:
                subprocess.Popen(cmd)
                return
            except FileNotFoundError:
                continue
        print(f"[LZRD] Message: {text}")


def display_message(text: str) -> None:
    """Display a notification message to the user (non-blocking, serialized)."""
    _ensure_message_worker()
    _message_queue.put(text)


def launch_app(path: str) -> None:
    """Launch an application or command on the PC.

    The command is split using shell-style quoting (``shlex.split``) so that
    paths containing spaces can be quoted, e.g. ``"C:\\Program Files\\app.exe"``.
    ``shell=False`` is used to prevent shell injection attacks.
    """
    try:
        args = shlex.split(path)
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
# Web Push infrastructure
# ---------------------------------------------------------------------------

_push_subscriptions: dict[str, dict] = {}  # subscription_id -> {endpoint, keys, ua}
_push_subscriptions_lock = threading.Lock()
_push_file = Path.home() / ".lzrd_push_subscriptions.json"
_known_dead_push_subscriptions: set[str] = set()

_vapid_public_key: str = ""
_vapid_private_key: str = ""
_vapid_claim_email: str = ""


def _public_key_to_vapid_b64(public_key: ec.EllipticCurvePublicKey) -> str:
    """Return URL-safe base64 (unpadded) VAPID public key bytes."""
    numbers = public_key.public_numbers()
    raw = b"\x04" + numbers.x.to_bytes(32, "big") + numbers.y.to_bytes(32, "big")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _private_pem_to_raw_b64(pem: str) -> str:
    """Convert a PKCS8 EC PEM private key to the raw base64url form pywebpush expects.

    py_vapid's ``Vapid.from_string`` base64url-decodes the key string and expects
    exactly 32 bytes (the raw private key integer).  Passing a PEM string directly
    causes an ASN.1 parse error because the PEM text is not valid base64url.
    """
    try:
        priv_obj = serialization.load_pem_private_key(pem.encode("utf-8"), password=None)
        if isinstance(priv_obj, ec.EllipticCurvePrivateKey):
            raw = priv_obj.private_numbers().private_value.to_bytes(32, "big")
            return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    except Exception:
        pass
    return ""


def _normalize_vapid_public_key(public_key_value: str, private_pem: str) -> str:
    """Return URL-safe base64 VAPID public key from base64/PEM/derived private key."""
    candidate = (public_key_value or "").strip()
    if candidate.startswith("-----BEGIN"):
        try:
            pub_obj = serialization.load_pem_public_key(candidate.encode("utf-8"))
            if isinstance(pub_obj, ec.EllipticCurvePublicKey):
                return _public_key_to_vapid_b64(pub_obj)
        except Exception:
            return ""

    if candidate:
        try:
            padded = candidate + "=" * ((4 - len(candidate) % 4) % 4)
            raw = base64.urlsafe_b64decode(padded.encode("ascii"))
            ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), raw)
            return candidate
        except Exception:
            return ""

    if private_pem:
        try:
            priv_obj = serialization.load_pem_private_key(private_pem.encode("utf-8"), password=None)
            if isinstance(priv_obj, ec.EllipticCurvePrivateKey):
                return _public_key_to_vapid_b64(priv_obj.public_key())
        except Exception:
            return ""

    return ""


def _generate_vapid_keys() -> tuple[str, str]:
    """Generate a new VAPID key pair and return (public_b64, private_pem)."""
    private_key = ec.generate_private_key(ec.SECP256R1())
    public_b64 = _public_key_to_vapid_b64(private_key.public_key())

    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption()
    ).decode('utf-8')

    return public_b64, private_pem


def _load_push_subscriptions() -> None:
    """Load push subscriptions from file."""
    global _push_subscriptions, _known_dead_push_subscriptions
    if _push_file.exists():
        try:
            with _push_file.open('r') as f:
                data = json.load(f)
                _push_subscriptions = data.get('subscriptions', {})
                _known_dead_push_subscriptions = set()
        except Exception as e:
            print(f"[LZRD] Warning: could not load push subscriptions: {e}")


def _save_push_subscriptions() -> None:
    """Persist push subscriptions to file."""
    try:
        with _push_subscriptions_lock:
            snapshot = dict(_push_subscriptions)
        with _push_file.open('w') as f:
            json.dump({'subscriptions': snapshot}, f)
    except Exception as e:
        print(f"[LZRD] Warning: could not save push subscriptions: {e}")


_PUSH_TTL_SECONDS = 60 * 60 * 24 * 2  # 48 hours


def _send_push_notification(title: str, body: str) -> None:
    """Send a push notification to all subscribed clients."""
    if not _vapid_public_key or not _vapid_private_key or not _vapid_claim_email:
        return
    
    invalid_subs: set[str] = set()
    with _push_subscriptions_lock:
        subs = [
            (sub_id, sub_data)
            for sub_id, sub_data in _push_subscriptions.items()
            if sub_id not in _known_dead_push_subscriptions
        ]
    
    for sub_id, sub_data in subs:
        try:
            webpush(
                subscription_info=sub_data,
                data=json.dumps({
                    "type": "alert",
                    "title": title,
                    "body": body,
                    "icon": "/icons/icon-192.png",
                    "badge": "/badge-icon.png"
                }),
                vapid_private_key=_vapid_private_key,
                vapid_claims={"sub": f"mailto:{_vapid_claim_email}"},
                ttl=_PUSH_TTL_SECONDS,
            )
        except WebPushException as e:
            status_code = e.response.status_code if e.response else None
            if status_code in (404, 410):
                invalid_subs.add(sub_id)
            elif status_code == 403:
                resp_text = str(e.response.text) if e.response else ""
                if "vapid" in resp_text.lower() or "do not correspond" in resp_text.lower():
                    invalid_subs.add(sub_id)
                else:
                    print(f"[LZRD] Push notification failed for {sub_id}: {e}")
            else:
                print(f"[LZRD] Push notification failed for {sub_id}: {e}")
        except Exception as e:
            print(f"[LZRD] Push notification error for {sub_id}: {e}")
    
    if invalid_subs:
        with _push_subscriptions_lock:
            for sub_id in invalid_subs:
                _known_dead_push_subscriptions.add(sub_id)
                _push_subscriptions.pop(sub_id, None)
        _save_push_subscriptions()
        print(f"[LZRD] Removed {len(invalid_subs)} expired push subscription(s).")


# Cached badge PNG bytes — built once at startup by _cache_badge_png().
_cached_badge_png: bytes = b""


def _cache_badge_png() -> None:
    """Pre-build the notification badge PNG so /badge-icon.png is instant."""
    global _cached_badge_png
    _cached_badge_png = _build_notification_badge_png()


def _build_notification_badge_png() -> bytes:
    """Return a monochrome 96x96 PNG badge derived from the app icon."""
    size = (96, 96)
    source = _load_tray_icon_image(armed=True).convert("RGBA").resize(size)

    alpha = source.getchannel("A")
    if alpha.getbbox() is None:
        source = _make_icon_image(armed=True).convert("RGBA").resize(size)
        alpha = source.getchannel("A")

    badge = Image.new("RGBA", size, (255, 255, 255, 0))
    badge.putalpha(alpha)

    buf = io.BytesIO()
    badge.save(buf, format="PNG")
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Flask application
# ---------------------------------------------------------------------------

_flask_app = Flask(__name__, static_folder=None)
_flask_app.config["MAX_CONTENT_LENGTH"] = 16 * 1024  # 16 KB — Flask returns 413 for larger bodies

# Set by main() before the server starts
_lzrd: "LZRD | None" = None
_config: configparser.ConfigParser | None = None
_token: str = ""
_token_bytes: bytes = b""   # pre-encoded form of _token; set alongside _token in main()
_owner_username: str = ""
_owner_password_hash: str = ""

# ---------------------------------------------------------------------------
# Security helpers
# ---------------------------------------------------------------------------

# Maximum allowed length for user-supplied string fields.
_MAX_MESSAGE_LEN = 500   # characters
_MAX_PATH_LEN = 260      # characters (matches Windows MAX_PATH)

# The insecure default token value — refused at startup.
_INSECURE_DEFAULT_TOKEN = "changeme"
_MIN_SERVER_TOKEN_LEN = 24
_INSECURE_TOKEN_VALUES = {
    "",
    "changeme",
    "testtoken",
    "default",
    "password",
    "token",
}

# Brute-force protection: track failed auth attempts per remote IP.
_MAX_FAILED_AUTH = 10      # max failures within the rolling window
_AUTH_WINDOW_SECS = 60.0   # window length in seconds

_rate_limit_lock = threading.Lock()
_failed_auth: dict[str, list[float]] = {}  # ip -> [monotonic timestamps of failures]

# Session/auth settings
_SESSION_COOKIE_NAME = "lzrd_session"
_SESSION_TTL_SECS = 60 * 60 * 24 * 30  # 30 days
_MIN_USERNAME_LEN = 3
_MAX_USERNAME_LEN = 64
_MIN_PASSWORD_LEN = 8
_MAX_PASSWORD_LEN = 256
_PBKDF2_ITERATIONS = 200_000

# Rolling setup keyword — short human-friendly code for first-time owner setup.
_SETUP_KEYWORD_ROLL_SECS = 300  # regenerate every 5 minutes
_SETUP_WORDS = [
    "amber", "blade", "cedar", "delta", "ember", "frost", "grain", "hatch",
    "ivory", "jewel", "knack", "latch", "maple", "north", "oasis", "pearl",
    "quail", "raven", "solar", "thorn", "ultra", "vivid", "waltz", "xenon",
    "acorn", "blaze", "cliff", "dusk", "eagle", "fable", "globe", "haven",
    "inlet", "kite", "lunar", "marsh", "noble", "olive", "plume", "quest",
    "ridge", "storm", "trail", "umbra", "vault", "wren", "zinc", "apex",
    "birch", "crane", "drift", "echo", "flame", "grove", "helm", "iron",
    "jade", "kelp", "lynx", "mist", "nexus", "orbit", "prism", "reef",
    "sage", "tide", "unity", "crest", "flint", "coral",
]
_setup_keyword: str = ""
_setup_keyword_lock = threading.Lock()


def _generate_setup_keyword() -> str:
    """Return a fresh two-word setup keyword like 'amber-blade'."""
    return f"{secrets.choice(_SETUP_WORDS)}-{secrets.choice(_SETUP_WORDS)}"


def _roll_setup_keyword() -> None:
    """Replace the current setup keyword with a new random one."""
    global _setup_keyword
    with _setup_keyword_lock:
        _setup_keyword = _generate_setup_keyword()


def _get_setup_keyword() -> str:
    """Return the current setup keyword."""
    with _setup_keyword_lock:
        return _setup_keyword


def _verify_setup_keyword(candidate: str) -> bool:
    """Return True when *candidate* matches the current setup keyword (case-insensitive)."""
    with _setup_keyword_lock:
        if not _setup_keyword:
            return False
        return hmac.compare_digest(
            candidate.lower().strip().encode("utf-8"),
            _setup_keyword.lower().encode("utf-8"),
        )


def _setup_keyword_roller() -> None:
    """Background thread: roll the setup keyword every *_SETUP_KEYWORD_ROLL_SECS*."""
    while True:
        time.sleep(_SETUP_KEYWORD_ROLL_SECS)
        _roll_setup_keyword()


def _config_has_owner_credentials() -> bool:
    return bool(_owner_username and _owner_password_hash)


def _write_config() -> None:
    """Persist current config state to config.ini."""
    if _config is None:
        return

    # Preserve the on-disk token if this process has stale in-memory config.
    # This prevents unrelated writes (e.g. auth updates) from rolling back a
    # manually rotated token while the app is still running.
    disk_cfg = configparser.ConfigParser()
    try:
        if CONFIG_FILE.exists():
            disk_cfg.read(CONFIG_FILE, encoding="utf-8")
            disk_token = _normalize_token(disk_cfg.get("server", "token", fallback=""))
            if disk_token:
                if not _config.has_section("server"):
                    _config.add_section("server")
                _config.set("server", "token", disk_token)
    except Exception:
        # Fall back to writing current in-memory config if disk read fails.
        pass

    with CONFIG_FILE.open("w", encoding="utf-8") as fh:
        _config.write(fh)


def _reset_owner_credentials() -> bool:
    """Clear configured owner credentials and persist the change to config.ini."""
    global _owner_username, _owner_password_hash
    if _config is None:
        return False
    if not _config.has_section("auth"):
        _config.add_section("auth")
    _owner_username = ""
    _owner_password_hash = ""
    _config.set("auth", "owner_username", "")
    _config.set("auth", "owner_password_hash", "")
    _write_config()
    return True


def _normalize_token(tok: str) -> str:
    """Normalize copied tokens by stripping all whitespace characters."""
    return "".join(tok.split())


def _is_weak_server_token(token: str) -> bool:
    """Return True when *token* is too weak for internet-exposed deployments."""
    normalized = _normalize_token(token)
    if len(normalized) < _MIN_SERVER_TOKEN_LEN:
        return True
    if normalized.lower() in _INSECURE_TOKEN_VALUES:
        return True
    return False


def _ensure_server_token(config: configparser.ConfigParser) -> str:
    """Read, validate, and auto-rotate the server token when needed."""
    if not config.has_section("server"):
        config.add_section("server")

    current = _normalize_token(config.get("server", "token", fallback=""))
    if not _is_weak_server_token(current):
        return current

    rotated = secrets.token_urlsafe(32)
    config.set("server", "token", rotated)
    with CONFIG_FILE.open("w", encoding="utf-8") as fh:
        config.write(fh)

    print(
        "[LZRD] Warning: weak or missing [server] token detected. "
        "Generated and saved a new random token. Existing sessions were invalidated."
    )
    return rotated


def _ensure_vapid_config(config: configparser.ConfigParser) -> tuple[str, str, str]:
    """Return validated VAPID config and persist generated defaults when needed."""
    if not config.has_section("server"):
        config.add_section("server")

    private_key = config.get("server", "vapid_private_key", fallback="").strip()
    public_key = config.get("server", "vapid_public_key", fallback="").strip()
    claim_email = config.get("server", "vapid_claim_email", fallback="").strip()

    changed = False
    if not private_key:
        public_key, private_key = _generate_vapid_keys()
        config.set("server", "vapid_public_key", public_key)
        config.set("server", "vapid_private_key", private_key)
        changed = True

    normalized_public = _normalize_vapid_public_key(public_key, private_key)
    if normalized_public and normalized_public != public_key:
        public_key = normalized_public
        config.set("server", "vapid_public_key", public_key)
        changed = True

    if not claim_email:
        public_url = config.get("server", "public_url", fallback="").strip()
        host = urlparse(public_url).hostname if public_url else ""
        claim_email = f"admin@{host or 'localhost'}"
        config.set("server", "vapid_claim_email", claim_email)
        changed = True

    if changed:
        with CONFIG_FILE.open("w", encoding="utf-8") as fh:
            config.write(fh)
        print("[LZRD] Web Push config updated in config.ini.")

    # pywebpush (py_vapid) expects the private key as the raw 32-byte private
    # integer base64url-encoded, not as a PEM string.  The PEM stays in
    # config.ini; only the in-memory form returned here is converted.
    return public_key, _private_pem_to_raw_b64(private_key), claim_email


def _check_raw_token(req: "request") -> bool:
    """Return True when the request carries a valid raw token value."""
    if not _token_bytes:
        return False
    tok = req.headers.get("X-Token", "") or req.args.get("token", "")
    tok = _normalize_token(tok)
    if not tok:
        _record_auth_failure(req.remote_addr or "")
        return False
    result = hmac.compare_digest(tok.encode("utf-8"), _token_bytes)
    if not result:
        _record_auth_failure(req.remote_addr or "")
    return result


def _hash_password(password: str) -> str:
    """Return a PBKDF2-SHA256 password hash in a self-describing format."""
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, _PBKDF2_ITERATIONS
    )
    return f"pbkdf2_sha256${_PBKDF2_ITERATIONS}${salt.hex()}${digest.hex()}"


def _verify_password(password: str, encoded_hash: str) -> bool:
    """Return True when *password* matches *encoded_hash*."""
    try:
        algo, iterations_s, salt_hex, digest_hex = encoded_hash.split("$", 3)
        if algo != "pbkdf2_sha256":
            return False
        iterations = int(iterations_s)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(digest_hex)
    except (ValueError, TypeError):
        return False
    computed = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, iterations
    )
    return hmac.compare_digest(computed, expected)


def _make_session_cookie(username: str) -> str:
    """Return an HMAC-signed session cookie value for *username*."""
    expires = int(time.time() + _SESSION_TTL_SECS)
    payload = f"{username}|{expires}".encode("utf-8")
    signature = hmac.new(_token_bytes, payload, hashlib.sha256).hexdigest()
    return f"{username}|{expires}|{signature}"


def _verify_session_cookie(cookie_value: str) -> bool:
    """Return True when *cookie_value* is a valid non-expired session."""
    if not cookie_value or not _token_bytes:
        return False
    try:
        username, expires_s, signature = cookie_value.split("|", 2)
        expires = int(expires_s)
    except ValueError:
        return False
    if not username or username != _owner_username:
        return False
    if expires < int(time.time()):
        return False
    payload = f"{username}|{expires}".encode("utf-8")
    expected = hmac.new(_token_bytes, payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature, expected)


def _is_authenticated(req: "request") -> bool:
    """Return True only if the request has a valid session or token."""
    cookie_value = req.cookies.get(_SESSION_COOKIE_NAME, "")
    if _verify_session_cookie(cookie_value):
        return True

    return _check_raw_token(req)


def _set_auth_cookie(response: "Response") -> None:
    """Set a hardened auth session cookie on *response*."""
    response.set_cookie(
        _SESSION_COOKIE_NAME,
        _make_session_cookie(_owner_username),
        max_age=_SESSION_TTL_SECS,
        httponly=True,
        secure=request.is_secure,
        samesite="Strict",
    )


def _record_auth_failure(ip: str) -> None:
    """Append the current timestamp to the failure log for *ip*."""
    with _rate_limit_lock:
        _failed_auth.setdefault(ip, []).append(time.monotonic())


def _check_token(req: "request") -> bool:
    """Return True only if *req* carries a valid auth token.

    Uses a constant-time comparison (``hmac.compare_digest``) to prevent
    timing-based token enumeration.  Failed attempts are recorded so the
    rate limiter can block the source IP when abuse is detected.

    ``_token_bytes`` is the pre-encoded form of the configured token so
    UTF-8 encoding only happens once at startup rather than on every request.
    """
    return _is_authenticated(req)


def _unauthorized() -> tuple:
    return jsonify({"error": "unauthorized"}), 401


@_flask_app.before_request
def _enforce_rate_limit():
    """Return 429 when an IP has exceeded the failed-auth threshold."""
    if not request.path.startswith("/api/"):
        return None
    ip = request.remote_addr or ""
    with _rate_limit_lock:
        now = time.monotonic()
        history = _failed_auth.get(ip, [])
        history = [t for t in history if now - t < _AUTH_WINDOW_SECS]
        _failed_auth[ip] = history
        if len(history) >= _MAX_FAILED_AUTH:
            return jsonify({"error": "too many requests"}), 429


@_flask_app.before_request
def _enforce_fetch_site():
    """Reject obvious cross-site unsafe requests that rely on cookie auth."""
    if not request.path.startswith("/api/"):
        return None
    if request.method in {"GET", "HEAD", "OPTIONS"}:
        return None
    # Token-authenticated requests are not CSRF-prone in the same way.
    has_token = bool(
        _normalize_token(request.headers.get("X-Token", "") or request.args.get("token", ""))
    )
    if has_token:
        return None
    fetch_site = (request.headers.get("Sec-Fetch-Site", "") or "").lower()
    if fetch_site and fetch_site not in {"same-origin", "same-site", "none"}:
        return jsonify({"error": "forbidden"}), 403
    return None


@_flask_app.after_request
def _add_security_headers(response):
    """Attach standard defensive HTTP headers to every response."""
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Content-Security-Policy"] = "default-src 'self'"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    # Prevent caching of API JSON responses.
    # The SSE endpoint manages its own Cache-Control header.
    if request.path.startswith("/api/") and request.path != "/api/events":
        response.headers["Cache-Control"] = "no-store"
    elif request.path == "/":
        response.headers["Cache-Control"] = "no-cache"
    elif request.path == "/sw.js":
        response.headers["Cache-Control"] = "no-cache, must-revalidate"
    else:
        response.headers["Cache-Control"] = "no-cache"
    return response


@_flask_app.route("/")
def index():
    return send_from_directory(WEB_DIR, "index.html")


@_flask_app.route("/manifest.json")
def manifest_file():
    return send_from_directory(
        WEB_DIR,
        "manifest.json",
        mimetype="application/manifest+json",
    )


@_flask_app.route("/badge-icon.png")
def badge_icon_file():
    data = _cached_badge_png or _build_notification_badge_png()
    return Response(data, mimetype="image/png")


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
            "platform": PLATFORM,
        }
    )


@_flask_app.route("/api/auth/bootstrap-status")
def api_auth_bootstrap_status():
    return jsonify({"requires_setup": not _config_has_owner_credentials()})


@_flask_app.route("/api/auth/setup", methods=["POST"])
def api_auth_setup():
    global _owner_username, _owner_password_hash

    if _config_has_owner_credentials():
        return jsonify({"error": "owner already configured"}), 409

    data = request.get_json(silent=True) or {}
    setup_code = str(data.get("setup_code", "")).strip()
    if not _verify_setup_keyword(setup_code):
        _record_auth_failure(request.remote_addr or "")
        return _unauthorized()
    username = str(data.get("username", "")).strip()
    password = str(data.get("password", ""))

    if not (_MIN_USERNAME_LEN <= len(username) <= _MAX_USERNAME_LEN):
        return jsonify({"error": "invalid username length"}), 400
    if not (_MIN_PASSWORD_LEN <= len(password) <= _MAX_PASSWORD_LEN):
        return jsonify({"error": "invalid password length"}), 400

    _owner_username = username
    _owner_password_hash = _hash_password(password)

    if _config is not None:
        if not _config.has_section("auth"):
            _config.add_section("auth")
        _config.set("auth", "owner_username", _owner_username)
        _config.set("auth", "owner_password_hash", _owner_password_hash)
        _write_config()

    resp = jsonify({"ok": True})
    _set_auth_cookie(resp)
    return resp


@_flask_app.route("/api/auth/login", methods=["POST"])
def api_auth_login():
    if not _config_has_owner_credentials():
        return jsonify({"error": "owner setup required"}), 400

    data = request.get_json(silent=True) or {}
    username = str(data.get("username", "")).strip()
    password = str(data.get("password", ""))

    if username != _owner_username or not _verify_password(password, _owner_password_hash):
        _record_auth_failure(request.remote_addr or "")
        return _unauthorized()

    resp = jsonify({"ok": True})
    _set_auth_cookie(resp)
    return resp


@_flask_app.route("/api/auth/logout", methods=["POST"])
def api_auth_logout():
    resp = jsonify({"ok": True})
    resp.delete_cookie(_SESSION_COOKIE_NAME)
    return resp


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
    if len(text) > _MAX_MESSAGE_LEN:
        return jsonify({"error": "text too long"}), 400
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
    if len(path) > _MAX_PATH_LEN:
        return jsonify({"error": "path too long"}), 400
    try:
        launch_app(path)
    except Exception:
        return jsonify({"error": "could not launch application"}), 400
    return jsonify({"ok": True})


@_flask_app.route("/api/events")
def api_events():
    if not _check_token(request):
        return _unauthorized()

    return Response(
        _make_sse_stream(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _make_sse_stream():
    """Generator that yields SSE-formatted data frames for connected clients."""
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
            "platform": PLATFORM,
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


@_flask_app.route("/api/push/subscribe", methods=["POST"])
def api_push_subscribe():
    if not _check_token(request):
        return _unauthorized()

    if not (_vapid_public_key and _vapid_private_key and _vapid_claim_email):
        return jsonify({"error": "push not configured"}), 503
    
    data = request.get_json(silent=True) or {}
    subscription = data.get("subscription")
    if not subscription or not isinstance(subscription, dict):
        return jsonify({"error": "invalid subscription"}), 400
    
    endpoint = subscription.get("endpoint", "").strip()
    if not endpoint:
        return jsonify({"error": "missing endpoint"}), 400

    keys = subscription.get("keys") or {}
    if not keys.get("p256dh") or not keys.get("auth"):
        return jsonify({"error": "invalid subscription keys"}), 400
    
    sub_id = secrets.token_urlsafe(16)
    with _push_subscriptions_lock:
        for existing_id, existing_sub in list(_push_subscriptions.items()):
            if existing_sub.get("endpoint") == endpoint:
                _push_subscriptions.pop(existing_id, None)
                _known_dead_push_subscriptions.discard(existing_id)
        _push_subscriptions[sub_id] = subscription
        _known_dead_push_subscriptions.discard(sub_id)
    _save_push_subscriptions()
    
    return jsonify({"ok": True, "subscription_id": sub_id})


@_flask_app.route("/api/push/unsubscribe", methods=["POST"])
def api_push_unsubscribe():
    if not _check_token(request):
        return _unauthorized()
    
    data = request.get_json(silent=True) or {}
    endpoint = data.get("endpoint", "").strip()
    if not endpoint:
        return jsonify({"error": "missing endpoint"}), 400
    
    with _push_subscriptions_lock:
        to_remove = [
            sub_id for sub_id, sub_data in _push_subscriptions.items()
            if sub_data.get("endpoint") == endpoint
        ]
        for sub_id in to_remove:
            _push_subscriptions.pop(sub_id, None)
            _known_dead_push_subscriptions.discard(sub_id)
    _save_push_subscriptions()
    
    return jsonify({"ok": True, "removed": len(to_remove)})


@_flask_app.route("/api/push/status", methods=["GET"])
def api_push_status():
    if not _check_token(request):
        return _unauthorized()
    
    has_vapid = bool(_vapid_public_key and _vapid_private_key and _vapid_claim_email)
    with _push_subscriptions_lock:
        sub_count = len(_push_subscriptions)
    
    return jsonify({
        "ok": True,
        "push_enabled": has_vapid,
        "vapid_public_key": _vapid_public_key if has_vapid else None,
        "subscriptions": sub_count
    })


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
        try:
            self._mouse_listener = pynput_mouse.Listener(on_move=self._on_move)
            self._mouse_listener.start()
        except Exception as exc:
            print(f"[LZRD] Warning: could not start mouse listener: {exc}")
            self._mouse_listener = None

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
            # Dispatch all heavy work (tray refresh, SSE broadcast, push HTTP)
            # off the low-level mouse hook callback to avoid Windows killing
            # the WH_MOUSE_LL hook due to timeout.
            threading.Thread(
                target=self._fire_alert, daemon=True, name="lzrd-alert"
            ).start()

    def _fire_alert(self) -> None:
        """Handle alert side-effects off the mouse-hook thread."""
        self.on_state_change()
        _broadcast(
            {
                "type": "alert",
                "armed": True,
                "alert": True,
                "mouse_locked": self.mouse_locked,
            }
        )
        _send_push_notification("LZRD Alert", "Movement detected!")


# ---------------------------------------------------------------------------
# Flask server runner
# ---------------------------------------------------------------------------


def _run_flask(port: int) -> None:
    # Suppress all Werkzeug/Flask access and info logs.
    for name in ("werkzeug", "flask.app", _flask_app.name):
        logger = logging.getLogger(name)
        logger.setLevel(logging.ERROR)
        logger.addHandler(logging.NullHandler())
        logger.propagate = False
    _flask_app.run(host="0.0.0.0", port=port, threaded=True, use_reloader=False, debug=False)


# ---------------------------------------------------------------------------
# System-tray entry point
# ---------------------------------------------------------------------------


def _detach_process() -> None:
    """Re-launch this script as a detached background process and exit.

    On Windows, uses pythonw.exe (windowless interpreter) with
    DETACHED_PROCESS so closing the original CMD has no effect.
    On Linux/macOS, forks and creates a new session.
    """
    if PLATFORM == "Windows":
        python = sys.executable
        # Prefer pythonw.exe for a windowless process.
        pythonw = Path(python).with_name("pythonw.exe")
        if pythonw.exists():
            python = str(pythonw)

        # Build command: same script, same args, minus --detach.
        args = [python] + [a for a in sys.argv if a != "--detach"]

        DETACHED_PROCESS = 0x00000008
        CREATE_NO_WINDOW = 0x08000000
        subprocess.Popen(
            args,
            creationflags=DETACHED_PROCESS | CREATE_NO_WINDOW,
            close_fds=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
        )
        print("[LZRD] Running in the background. Close this window safely.")
        sys.exit(0)
    else:
        # Unix: double-fork daemonization.
        if os.fork() > 0:
            sys.exit(0)
        os.setsid()
        if os.fork() > 0:
            sys.exit(0)
        # Redirect std streams.
        devnull = os.open(os.devnull, os.O_RDWR)
        os.dup2(devnull, 0)
        os.dup2(devnull, 1)
        os.dup2(devnull, 2)
        os.close(devnull)


def main() -> None:
    global _config, _lzrd, _token, _token_bytes, _owner_username, _owner_password_hash
    global _vapid_public_key, _vapid_private_key, _vapid_claim_email

    parser = argparse.ArgumentParser(description="LZRD — Mouse-movement tripwire")
    parser.add_argument(
        "--detach",
        action="store_true",
        help="Re-launch as a background process and exit (CMD can be closed).",
    )
    cli_args = parser.parse_args()

    if cli_args.detach:
        _detach_process()

    config = load_config()
    _config = config
    _token = _ensure_server_token(config)
    _token_bytes = _token.encode("utf-8")
    _owner_username = config.get("auth", "owner_username", fallback="").strip()
    _owner_password_hash = config.get("auth", "owner_password_hash", fallback="").strip()

    _vapid_public_key, _vapid_private_key, _vapid_claim_email = _ensure_vapid_config(config)
    
    # Load push subscriptions
    _load_push_subscriptions()
    
    port = config.getint("server", "port", fallback=7734)
    local_ip = get_local_ip()

    behind_proxy = config.getboolean("server", "behind_proxy", fallback=False)
    if behind_proxy:
        # Trust one proxy hop so that request.remote_addr reflects the real
        # client IP (read from X-Forwarded-For set by Caddy / nginx / etc.).
        # This is required for per-IP rate limiting to work correctly when
        # LZRD is accessed remotely through a reverse proxy.
        _flask_app.wsgi_app = ProxyFix(
            _flask_app.wsgi_app, x_for=1, x_proto=1, x_host=1
        )

    public_url = config.get("server", "public_url", fallback="").strip()
    server_url = public_url if public_url else f"http://{local_ip}:{port}"

    _lzrd = LZRD(config)

    # Pre-cache icon images and badge PNG so they are ready instantly.
    _cache_tray_icons()
    _cache_badge_png()

    # Generate the initial setup keyword and start the rolling timer.
    _roll_setup_keyword()
    threading.Thread(
        target=_setup_keyword_roller, daemon=True, name="lzrd-setup-roll"
    ).start()

    # Start Flask in a background daemon thread
    threading.Thread(
        target=_run_flask, args=(port,), daemon=True, name="lzrd-web"
    ).start()

    # ---- Tray menu ----
    def _build_menu() -> pystray.Menu:
        arm_label = "Disarm" if _lzrd.armed else "Arm"
        arm_action = _lzrd.disarm if _lzrd.armed else _lzrd.arm

        def _show_setup_code(icon, item) -> None:
            display_message(f"Setup code:\n\n{_get_setup_keyword()}")

        def _reset_owner(icon, item) -> None:
            if _reset_owner_credentials():
                display_message(
                    "Owner credentials were reset.\n\n"
                    "Refresh the web app to create new first-time owner credentials."
                )
            else:
                display_message("Could not reset owner credentials.")

        return pystray.Menu(
            pystray.MenuItem(server_url, lambda icon, item: None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(arm_label, lambda icon, item: arm_action()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Show Setup Code", _show_setup_code),
            pystray.MenuItem("Reset Owner Credentials", _reset_owner),
            pystray.MenuItem("Lock Screen Now", lambda icon, item: lock_workstation()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Exit", lambda icon, item: icon.stop()),
        )

    # Create the icon *before* setting on_state_change so _refresh_tray
    # can safely reference it without triggering an UnboundLocalError if
    # the Flask thread receives a request during startup.
    icon = pystray.Icon(
        name="LZRD",
        icon=_load_tray_icon_image(armed=False),
        title=f"LZRD — Disarmed 🔴 | {server_url}",
        menu=_build_menu(),
    )

    def _refresh_tray() -> None:
        icon.icon = _load_tray_icon_image(_lzrd.armed)
        icon.title = f"LZRD — {'Armed 🟢' if _lzrd.armed else 'Disarmed 🔴'} | {server_url}"
        icon.menu = _build_menu()

    _lzrd.on_state_change = _refresh_tray

    try:
        icon.run()
    except Exception as exc:
        # System tray not available (headless Linux, no notification area, etc.)
        print(f"[LZRD] System tray unavailable: {exc}")
        print(f"[LZRD] Web interface running at {server_url}")
        print("[LZRD] Press Ctrl+C to stop.")
        try:
            threading.Event().wait()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
