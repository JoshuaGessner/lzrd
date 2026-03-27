# 🦎 LZRD

**Turn your phone into a remote control and security tripwire for your PC.**

LZRD runs quietly in your PC's system tray and serves a mobile web app to any phone on the same Wi-Fi network. Arm the tripwire, walk away, and get an instant alert the moment anyone touches your mouse — then lock the screen, shut down, or take other action right from your phone.

Local owner login. No cloud services. No internet required.

---

## What can LZRD do?

| Action | What it does |
|--------|-------------|
| 🟢 **Arm / Disarm** | Watch for mouse movement and send an instant alert to your phone when triggered |
| 🔒 **Lock Screen** | Immediately lock your PC's screen remotely |
| 🖱️ **Lock Mouse** | Freeze the cursor in place so it cannot be moved |
| ⏻ **Shutdown** | Remotely shut down your PC |
| 🔄 **Restart** | Remotely restart your PC |
| 💬 **Message** | Pop up a message box on your PC screen |
| 🚀 **Launch App** | Open any application or run any command on your PC |

Movement alerts appear instantly with a flashing red banner and vibrate your phone.

---

## What you need

- A **Windows 10/11** or **Linux** desktop PC
- **Python 3.10 or newer** installed on the PC ([download here](https://www.python.org/downloads/))
- Your phone and PC connected to the **same Wi-Fi network**
- Any modern mobile browser (Chrome for Android, Safari for iOS, etc.)

> **Linux only:** You also need a desktop environment with a system-tray notification area (GNOME, KDE, XFCE, Cinnamon, etc. all work). If you use GNOME on Ubuntu, install one extra package before you start — see the Linux setup steps below.

---

## Setup

### Windows

**Step 1 — Install Python**

Download and run the installer from [python.org](https://www.python.org/downloads/). On the first screen of the installer, tick **"Add Python to PATH"** before clicking Install.

**Step 2 — Get LZRD**

Open **Command Prompt** (press `Win + R`, type `cmd`, press Enter) and run:

```
git clone https://github.com/JoshuaGessner/lzrd.git
cd lzrd
```

If you don't have Git, download the ZIP directly from the GitHub page instead:
1. Go to [github.com/JoshuaGessner/lzrd](https://github.com/JoshuaGessner/lzrd)
2. Click **Code → Download ZIP**
3. Extract the ZIP somewhere convenient (e.g. `C:\Users\You\lzrd`)
4. Open Command Prompt and `cd` to that folder

**Step 3 — Install dependencies**

In the same Command Prompt window:

```
pip install -r requirements.txt
```

**Step 4 — Run LZRD**

```
python lzrd.py
```

LZRD will create its config file automatically on first run and generate a secure access token for transport/auth fallback.

---

### Linux

**Step 1 — Install Python**

Most Linux distributions include Python 3. Check with:

```bash
python3 --version
```

If it prints `Python 3.10` or higher, you're good. Otherwise install it through your package manager:

```bash
# Ubuntu / Debian
sudo apt install python3 python3-pip

# Fedora
sudo dnf install python3 python3-pip

# Arch
sudo pacman -S python python-pip
```

**Step 2 — Install the system tray helper (GNOME / Ubuntu only)**

```bash
sudo apt install gir1.2-appindicator3-0.1
```

On KDE, XFCE, Cinnamon, and most other desktops this step is not needed.

**Step 3 — Get LZRD**

```bash
git clone https://github.com/JoshuaGessner/lzrd.git
cd lzrd
```

No Git? Download the ZIP from [github.com/JoshuaGessner/lzrd](https://github.com/JoshuaGessner/lzrd) and extract it.

**Step 4 — Install dependencies**

```bash
pip3 install -r requirements.txt
```

**Step 5 — Run LZRD**

```bash
python3 lzrd.py
```

LZRD will create its config file automatically on first run and generate a secure access token for transport/auth fallback.

---

## Starting LZRD

**Windows:**
```
python lzrd.py
```

**Linux:**
```bash
python3 lzrd.py
```

A small lizard icon appears in the system tray. Hover over it to see the address you need to open on your phone (for example `http://192.168.1.42:7734`).

> **Tip:** If no tray icon appears (rare on some Linux setups), LZRD will print the address directly in the terminal window. The web app works exactly the same either way.

---

## Connecting your phone

1. Make sure your phone is on the **same Wi-Fi network** as your PC.
2. Open the address shown in the tray tooltip in your phone's browser.
3. On first launch, enter the tray **access token** once to authorize ownership setup, then create **owner credentials** (username + password).
4. On future visits, sign in with those owner credentials.
5. The tray **access token** is still available for advanced/manual auth fallback and integrations.

---

## Install LZRD as an app on your phone (optional)

Installing LZRD as a Progressive Web App (PWA) makes it open like a real app with its own icon on your home screen — no app store required.

### Android (Chrome)

1. Open the LZRD address in **Chrome**.
2. Tap the three-dot menu **(⋮)** → **Add to Home screen** → **Install**.
3. LZRD appears on your home screen. Tap it to open it as a full-screen app.

### iPhone / iPad (Safari)

> iOS requires a secure connection for full PWA features.  
> On a home network, Safari can still open the page and use it in the browser normally — use **Add to Home Screen** and it will work as a web clip (connection indicator and controls all function correctly).

1. Open the LZRD address in **Safari**.
2. Tap the **Share button** (the square with an arrow) → **Add to Home Screen** → **Add**.
3. The LZRD icon appears on your home screen.

---

## Using LZRD

### Typical workflow

1. Run `lzrd.py` on your PC and open the web app on your phone.
2. Tap **Arm** — the status indicator turns green and LZRD begins watching the mouse.
3. Walk away from your PC.
4. If anyone moves the mouse, your phone vibrates and shows a red **MOVEMENT DETECTED** banner.
5. Use the control buttons to respond — lock the screen, shut down, or anything else — without touching your PC.
6. Tap **Disarm** when you're back.

### Control buttons

| Button | What happens |
|--------|-------------|
| **Arm** | Start watching for mouse movement |
| **Disarm** | Stop watching; clears any active alert |
| **Lock Screen** | Instantly locks your PC (same as pressing Win + L) |
| **Lock Mouse** | Prevents the cursor from moving; tap again to unlock |
| **Shutdown** | Shows a confirmation, then shuts your PC down |
| **Restart** | Shows a confirmation, then restarts your PC |
| **Message** | Type a message and it appears as a pop-up on your PC screen |
| **Launch App** | Enter a program name or full path and it opens on your PC |

---

## Settings

Open `config.ini` in any text editor to change these settings. Restart LZRD after saving.

| Setting | Default | Description |
|---------|---------|-------------|
| `server.port` | `7734` | The port number the app listens on. Change it if something else is already using 7734. |
| `server.token` | *(auto-generated)* | Secret used for token fallback auth and internal session signing. Use **Show Access Token** in the tray menu to view it. |
| `lzrd.movement_threshold` | `10` | How many pixels the mouse must move before the alert fires. Lower = more sensitive. |
| `auth.owner_username` | *(empty initially)* | Owner username created from the web UI on first launch. |
| `auth.owner_password_hash` | *(empty initially)* | PBKDF2 hash of the owner password, created during first-launch setup. |

---

## Keeping LZRD private on your network

By default, LZRD is only accessible to devices on your local Wi-Fi — it never connects to the internet on its own. Owner credentials protect the web UI, and the server token remains an additional secret used by fallback auth/session signing.

For extra protection, you can tell your PC's firewall to only allow connections from your own Wi-Fi range:

**Windows** (open PowerShell as Administrator):
```powershell
New-NetFirewallRule -DisplayName "LZRD" -Direction Inbound -LocalPort 7734 -Protocol TCP -Action Allow
```

**Linux** (if you use `ufw`):
```bash
sudo ufw allow from 192.168.0.0/16 to any port 7734
```

---

## Background alerts and push notifications

When LZRD is installed as a PWA, you can enable **background alerts** so your phone notifies you of movement even when the app is completely closed.

### Setup (requires HTTPS domain)

For background push notifications to work, your LZRD must be served over **HTTPS** with a domain name (not just a local IP). This is because the Web Push API requires a secure context.

**Option 1: Remote access via Caddy (recommended)**

If you want to control LZRD from anywhere, set it up behind Caddy with automatic HTTPS (see "Remote access via Caddy" section below). Once Caddy is running, background push automatically works.

**Option 2: Local network with HTTPS**

You can set up a self-signed certificate for local HTTPS access, but most phone browsers will show security warnings. Caddy with a real domain is simpler and recommended.

### Enabling background alerts

1. Make sure LZRD is accessed over **HTTPS** (check the browser address bar).
2. Open LZRD and sign in.
3. Scroll down to the **Notifications** section.
4. Tap **Enable Background Alerts**.
5. Your browser will ask for permission to send notifications — tap **Allow**.
6. Once enabled, a checkmark appears and you're ready to go.

### How it works

- **App open in foreground**: Instant vibration and red banner alert.
- **App backgrounded but running**: Push notification appears on your phone.
- **App completely closed**: Push notification appears (if PWA is installed).

### Troubleshooting push notifications

| Symptom | Likely cause | Solution |
|---------|-------------|----------|
| "Push notifications not supported" | Older browser or limited device | Try Chrome/Edge (Android) or Safari (iOS with Caddy) |
| "Requires secure connection (HTTPS)" | Accessing via HTTP or local IP only | Switch to HTTPS domain (use Caddy) |
| "Background notifications blocked" | User denied permission on browser/device | Check Settings → Notifications and allow for the app |
| Notifications not arriving but page is open | Normal — page-level alerts work instead | Notifications are for closed/backgrounded app only |
| Notifications work once but stop | Subscription expired or max age reached | Re-enable background alerts if they stop working |

---

## Remote access via Caddy (access from anywhere)

If you want to control your PC from outside your home network — from a coffee shop, at work, or anywhere — you can put LZRD behind [Caddy](https://caddyserver.com/), a free reverse proxy that adds HTTPS automatically.

### What you need

- A domain name pointed at your home/server's public IP address
- Ports **80** and **443** open in your router/firewall (for Caddy + Let's Encrypt)
- [Caddy installed](https://caddyserver.com/docs/install) on the same machine running LZRD

### Step 1 — Configure LZRD for proxy mode

Open `config.ini` and add these two settings:

```ini
[server]
behind_proxy = true
public_url   = https://lzrd.yourdomain.com
```

`behind_proxy = true` tells LZRD to read the real client IP from the `X-Forwarded-For` header that Caddy adds, so that per-IP rate limiting works correctly for remote users.

### Step 1b — (Optional) Generate VAPID keys for push notifications

To enable background push notifications, you need to generate a VAPID key pair. VAPID keys are used by Web Push servers to authenticate message delivery.

Generate keys using Python (requires the `cryptography` package, already in requirements.txt):

```bash
python3 -c "
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization

private_key = ec.generate_private_key(ec.SECP256R1())
public_key = private_key.public_key()

private_pem = private_key.private_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PrivateFormat.PKCS8,
    encryption_algorithm=serialization.NoEncryption()
).decode('utf-8')

public_pem = public_key.public_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PublicFormat.SubjectPublicKeyInfo
).decode('utf-8')

print('VAPID Public Key:')
print(public_pem)
print('\nVAPID Private Key:')
print(private_pem)
"
```

Copy both keys into `config.ini` under the `[server]` section:

```ini
[server]
behind_proxy = true
public_url   = https://lzrd.yourdomain.com
vapid_public_key  = -----BEGIN PUBLIC KEY-----...
vapid_private_key = -----BEGIN PRIVATE KEY-----...
vapid_claim_email = your-email@example.com
```

Replace `your-email@example.com` with a valid email. Push services may use this to contact you if needed.

Once VAPID keys are set, users can enable "Background Alerts" on their phones to receive notifications when LZRD is backgrounded or closed.

### Step 2 — Set up Caddy

A ready-to-use `Caddyfile` is included in the repository. Edit it to replace `lzrd.yourdomain.com` with your actual domain, then run:

```bash
caddy run --config Caddyfile
```

Caddy will automatically obtain and renew a TLS certificate from Let's Encrypt.  Once it is running, open `https://lzrd.yourdomain.com` on your phone — everything works exactly the same as on your local network, and with HTTPS you get the full PWA install experience on iOS Safari too.

### Step 3 — Enable push notifications (if VAPID keys are set)

Once Caddy is running and you access LZRD over HTTPS, users can install LZRD as a PWA and enable background alerts:

1. Open the installed PWA on your phone  
2. Scroll to **Notifications**  
3. Tap **Enable Background Alerts**  
4. Allow notifications when prompted  
5. Movement alerts will now reach your phone even when LZRD is closed

> **Security reminder:** Your owner password and LZRD token both matter for remote exposure. Keep the owner password strong, and keep the token unique (LZRD generates a secure random token automatically on first run).

---

## Troubleshooting

**UI looks stale after updating LZRD**
- Hard refresh once (`Ctrl+F5`) after upgrading.
- LZRD now uses a network-first service worker strategy and explicit cache cleanup, so old assets should be replaced automatically on next load when online.

**Phone can't reach the web app**  
- Check that your phone and PC are on the same Wi-Fi network (not a guest network).  
- Make sure you're using the address shown in the tray tooltip, not `localhost`.  
- Temporarily disable your PC's firewall to test; if that fixes it, add a rule to allow port 7734.

**No system tray icon on Linux**  
LZRD will print the server address in the terminal — use that to connect. On Ubuntu/GNOME, install `gir1.2-appindicator3-0.1` (see Step 2 of the Linux setup above) and restart LZRD.

**Movement alert doesn't vibrate my phone**  
Vibration requires the LZRD page to be in the foreground. Install it as a PWA (see above) for the best experience.

**Lock Mouse button doesn't work on Linux**  
Mouse locking requires the `Xlib` Python package and an X11 display. Install it with `pip3 install python-xlib` and make sure you are running a graphical session (not SSH without X forwarding).

---

## Uninstalling

1. Close LZRD (right-click the tray icon → **Exit**).
2. Delete the `lzrd` folder.
3. That's it — LZRD doesn't install anything system-wide.

