# LZRD

A minimalist Windows tripwire for when you're away from your PC.

When **armed**, LZRD watches for mouse movement. The moment the mouse moves
beyond a configurable threshold it:

1. Sends an **SMS alert** to your phone via [Twilio](https://www.twilio.com).
2. Starts polling your Twilio number for an inbound reply.
3. If the reply contains the configured lock keyword (default: `lock`), it
   immediately **locks the Windows workstation**.

---

## Requirements

- Windows 10 / 11
- Python 3.10+
- A free [Twilio](https://www.twilio.com/try-twilio) account with a phone number

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

Edit `config.ini` and fill in your Twilio credentials and phone numbers:

```ini
[twilio]
account_sid  = ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
auth_token   = xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
from_number  = +12025550100   ; your Twilio number
to_number    = +12025550199   ; your personal mobile number

[lzrd]
movement_threshold = 10   ; pixels the mouse must move to trigger an alert
lock_keyword       = lock ; reply with this word to lock the PC
```

---

## Usage

```bash
python lzrd.py
```

A small lizard icon appears in the system tray.

| Tray action | Description |
|-------------|-------------|
| **Arm**     | Capture current cursor position and start monitoring |
| **Disarm**  | Stop monitoring without locking |
| **Lock Now**| Immediately lock the workstation |
| **Exit**    | Quit the application |

### Typical workflow

1. Sit down, run `lzrd.py`, and click **Arm** from the tray menu.
2. Walk away — LZRD records the cursor position.
3. If someone touches the mouse, you receive an SMS:
   > *LZRD Alert: Mouse movement detected! Reply 'lock' to lock.*
4. Reply **lock** (or whatever keyword you configured) and your PC locks.

---

## Configuration reference

| Key | Default | Description |
|-----|---------|-------------|
| `twilio.account_sid` | — | Twilio Account SID (required) |
| `twilio.auth_token` | — | Twilio Auth Token (required) |
| `twilio.from_number` | — | Your Twilio phone number (E.164) |
| `twilio.to_number` | — | Your personal mobile number (E.164) |
| `lzrd.movement_threshold` | `10` | Pixel radius before alert fires |
| `lzrd.lock_keyword` | `lock` | Case-insensitive reply word that locks the PC |

---

## Security note

`config.ini` contains sensitive credentials — **never commit it to version
control**.  It is listed in `.gitignore` by default.
