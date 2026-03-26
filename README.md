# LZRD

A minimalist Windows tripwire and remote-control app for when you're away from your PC.

When **armed**, LZRD watches for mouse movement. The moment the mouse moves beyond a configurable
threshold it sends a real-time alert to any phone browser connected via the built-in
**Progressive Web App (PWA)** — no Twilio account, no third-party service required.

![LZRD PWA screenshot](https://github.com/user-attachments/assets/8a3b4912-7a78-4e99-971a-7c376dea8da2)

---

## Features

| Control | Description |
|---------|-------------|
| 🔒 **Lock Screen** | Lock the Windows workstation immediately |
| 🖱️ **Lock Mouse** | Confine the cursor to its current position (toggle) |
| ⏻ **Shutdown** | Shut down the PC (5-second delay) |
| 🔄 **Restart** | Restart the PC (5-second delay) |
| 💬 **Message** | Show a pop-up message box on the PC screen |
| 🚀 **Launch App** | Run any application or command on the PC |

The app also lets you **Arm / Disarm** the mouse-movement tripwire directly from the web UI.
Movement alerts are delivered instantly via **Server-Sent Events** and trigger a haptic vibration
on the phone.

---

## Requirements

- Windows 10 / 11
- Python 3.10+
- Your phone and PC on the **same Wi-Fi network** (no internet required)

---

## Installation

```bash
# 1. Clone the repo
git clone https://github.com/JoshuaGessner/lzrd.git
cd lzrd

# 2. Install Python dependencies
pip install -r requirements.txt

# 3. Create your config file
copy config.ini.example config.ini
```

Edit `config.ini` and (at minimum) set a strong access token:

```ini
[server]
port  = 7734
token = your-strong-passphrase-here

[lzrd]
movement_threshold = 10
```

---

## Usage

```bash
python lzrd.py
```

A small lizard icon appears in the Windows system tray. Hover over it to see the server URL
(e.g. `http://192.168.1.100:7734`).

### Connecting your phone

1. Make sure your phone is on the same Wi-Fi network as your PC.
2. Open the URL shown in the tray tooltip in your phone's browser.
3. When prompted, enter the access token from your `config.ini`  
   (or right-click the tray icon → **Show Access Token**).
4. The token is stored in `localStorage` — you only enter it once.

### Installing as a PWA (Android Chrome)

1. Open the URL in Chrome for Android.
2. Tap the menu → **Add to Home screen** → **Install**.
3. LZRD now opens as a standalone app, just like a native app.

### Typical workflow

1. Sit down at your PC, run `lzrd.py`, and tap **Arm** in the web UI (or from the tray menu).
2. Walk away.
3. If someone touches the mouse, your phone vibrates and shows a red **MOVEMENT DETECTED** banner.
4. Use the control buttons to lock the screen, shut down, or take other action remotely.

---

## Configuration reference

| Key | Default | Description |
|-----|---------|-------------|
| `server.port` | `7734` | TCP port the web server listens on |
| `server.token` | `changeme` | Access token — change this before use |
| `lzrd.movement_threshold` | `10` | Pixel radius before the alert fires |

---

## PWA notes

- **HTTP is sufficient** for local network use on Android Chrome.
- **iOS Safari** requires HTTPS for service-worker installation. For HTTPS, place a reverse proxy
  (nginx, Caddy) with a self-signed certificate in front of the Flask server.
- Service worker caches the app shell so the UI loads even if the PC is unreachable (controls
  will fail gracefully with an error toast).

---

## Security note

`config.ini` contains your access token — **never commit it to version control**.
It is listed in `.gitignore` by default.
The web server is only accessible to devices on the same local network.
For additional security, configure your Windows Firewall to restrict access to port 7734.

