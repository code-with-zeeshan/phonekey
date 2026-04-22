# 📱→💻 PhoneKey v3.0.0

> Advanced phone-as-keyboard with mouse control, clipboard sync, and secure connections — lightweight, real-time, zero-install on phone.

[![License: MIT](https://img.shields.io/badge/License-MIT-purple.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.8%2B-blue.svg)](https://python.org)
[![Platform](https://img.shields.io/badge/Platform-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey.svg)]()

---

## 🧠 Why PhoneKey v3.0.0?

| Problem | PhoneKey v3.0.0 Solution |
|---|---|
| Forgot physical keyboard | Full keyboard with modifier keys & function keys |
| Need mouse control | Touch trackpad with gestures (move, click, scroll) |
| Clipboard between devices | Phone-to-laptop clipboard sync |
| Multiple device management | Device naming & real-time connection tracking |
| Security concerns | 4-digit PIN authentication + HTTPS/WSS |
| Hard to share URL | QR code terminal display for instant scanning |
| Other tools show duplicates | Sentinel pattern + tab deduplication |
| Other tools eat laptop RAM | ~20 MB Python process with all features |
| Other tools require app install | Pure browser — no downloads needed |
| Slow response | WebSocket over LAN (<5ms latency) |
| Theme preferences | Dark/light mode toggle with persistence |

---

## 🏗️ Architecture

```
📱 Phone Browser          💻 Your Laptop
┌──────────────┐          ┌────────────────────────┐
│ index.html   │  WiFi    │ server.py              │
│              │◄────────►│ ┌──────────────────┐   │
│ Sentinel     │WebSocket │ │ WS Server  :8765 │   │
│ Pattern      │          │ │ HTTP Server:8080 │   │
│ input event  │          │ └────────┬─────────┘   │
└──────────────┘          │          │             │
                          │     asyncio Queue      │
                          │          │             │
                          │       pynput           │
                          │  (OS keystroke inject) │
                          └────────────────────────┘
```

---

## ⚙️ Tech Stack

| Layer | Technology | Why |
|---|---|---|
| Server | Python 3.8+ | Lightweight, cross-platform async |
| Key Injection | `pynput 1.7.6` | OS-level keyboard/mouse control |
| Mouse Control | `pynput 1.7.6` | Touch trackpad with gestures |
| WebSocket | `websockets 12.0` (asyncio) | Real-time bidirectional comms |
| SSL/TLS | `cryptography 42.0.5` | Auto-generated HTTPS certificates |
| QR Codes | `qrcode 7.4.2` | Terminal QR for easy URL sharing |
| Clipboard | `pyperclip 1.8.2` | Cross-device clipboard sync |
| Key Queue | `asyncio.Queue` | Prevents fast-typing key drops |
| HTTP | `http.server` (stdlib) | Built-in web server |
| Client | Vanilla HTML5/CSS3/ES6 | No frameworks, pure browser |
| Input Fix | Sentinel + `input` event | Mobile keyboard compatibility |
| Testing | `unittest` (stdlib) | Unit tests for core logic |

---

## 🚀 v3.0.0 New Features

### 🔐 Security & Authentication
- **4-Digit PIN**: Secure connection with optional PIN authentication
- **HTTPS/WSS**: Auto-generated self-signed certificates for encrypted connections
- **Connection Deduplication**: Prevents duplicate connections from same browser tab

### 🖱️ Advanced Input Control
- **Mouse Trackpad**: Touch gestures for cursor movement, clicking, and scrolling
- **Modifier Keys**: Full support for Shift, Ctrl, Alt combinations
- **Function Keys**: F1-F12 with scrollable interface
- **Speed Control**: Adjustable mouse movement sensitivity

### 📋 Cross-Device Features
- **Clipboard Sync**: Copy text from phone and paste on laptop instantly
- **Multi-Device Support**: Connect multiple phones simultaneously with device naming
- **Real-time Updates**: Live device list and connection status

### 🎨 User Experience
- **Dark/Light Theme**: Persistent theme toggle with system preference detection
- **Tab-Based UI**: Organized interface (Keyboard, Mouse, Clipboard, Devices)
- **QR Code Display**: Terminal QR codes for instant URL sharing
- **Responsive Design**: Optimized for all mobile screen sizes

### 🛠️ Developer Features
- **CLI Arguments**: Custom ports, HTTPS mode, PIN disable, mouse speed
- **Environment Config**: All settings configurable via environment variables
- **Type Hints**: Full Python type annotations for maintainability
- **Unit Tests**: Test coverage for core functionality
- **Connection Metrics**: Active connection counting and logging

---

## 📋 Requirements

- Python 3.8 or higher
- Phone and laptop on the **same WiFi network**
- Any modern phone browser (Chrome, Safari, Firefox)

---

## ⚙️ Configuration

PhoneKey supports configuration via environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `PHONEKEY_WS_PORT` | 8765 | WebSocket server port |
| `PHONEKEY_HTTP_PORT` | 8080 | HTTP server port for client UI |
| `PHONEKEY_LOCK_PORT` | 18765 | Internal socket lock port |
| `PHONEKEY_KEY_DELAY` | 0.012 | Delay between keystrokes (seconds) |
| `PHONEKEY_PING_INTERVAL` | 30 | WebSocket ping interval (seconds) |
| `PHONEKEY_PING_TIMEOUT` | 60 | WebSocket ping timeout (seconds) |

Example:
```bash
PHONEKEY_WS_PORT=9999 PHONEKEY_HTTP_PORT=8888 python server.py
```

---

## ⬇️ Download (No Python Required)

| Platform | Download |
|---|---|
| 🪟 Windows | [phonekey-windows.exe](https://github.com/code-with-zeeshan/phonekey/releases/latest/download/phonekey-windows.exe) |
| 🍎 macOS   | [phonekey-macos](https://github.com/code-with-zeeshan/phonekey/releases/latest/download/phonekey-macos) |
| 🐧 Linux   | [phonekey-linux](https://github.com/code-with-zeeshan/phonekey/releases/latest/download/phonekey-linux) |

### Run the downloaded file:

**Windows:** Double-click `phonekey-windows.exe`

**macOS / Linux:**
```bash
chmod +x phonekey-macos   # or phonekey-linux
./phonekey-macos
```

> 💡 macOS may show "unidentified developer" warning.
> Go to System Settings → Privacy → Allow anyway.

---

## 🚀 Quick Start

### 1. Clone the repo

```bash
git clone https://github.com/code-with-zeeshan/phonekey.git
cd phonekey
```

### 2. Create virtual environment

```bash
python -m venv venv
```

### 3. Activate it

```bash
# Windows
venv\Scripts\activate

# macOS / Linux
source venv/bin/activate
```

### 4. Install dependencies

```bash
pip install -r requirements.txt
```

### 5. (Optional) Run tests

```bash
python -m unittest test_server.py
```

### 7. Run the server

#### Basic Usage (HTTP, PIN enabled)
```bash
python server.py
```

#### HTTPS Mode (recommended for iOS Safari)
```bash
python server.py --https
```

#### Custom Configuration
```bash
# Custom ports
python server.py --ws-port 9000 --http-port 9001

# Disable PIN for home network
python server.py --no-pin

# Adjust mouse speed (0.1-5.0)
python server.py --mouse-speed 2.5

# Full secure setup
python server.py --https --ws-port 9443 --http-port 9444 --mouse-speed 1.5
```

### 8. Open on your phone

The terminal will show:

```
╔══════════════════════════════════════════════╗
║         📱  PhoneKey  v3.0.0  💻            ║
╠══════════════════════════════════════════════════╣
║  OS      : Windows                              ║
║  Mode    : HTTPS/WSS 🔒                         ║
║  PIN: 4827                                      ║
╠══════════════════════════════════════════════════╣
║  Open on your phone:                             ║
║  👉  https://192.168.0.104:8080                 ║
╚══════════════════════════════════════════════════╝

  📷  Scan QR code with your phone camera:

  ██████████████  ██  ██████████████
  ██          ██      ██          ██
  ... (QR code) ...

  🔐  When prompted on phone, enter PIN:  4827
```

Open that URL in your phone browser → tap "Tap here to start typing" → type!

---

## 💻 OS-Specific Notes

### Windows
No extra steps — works out of the box.

### macOS
Grant Accessibility permission once:
```
System Settings → Privacy & Security → Accessibility → Enable Terminal
```

### Linux (Ubuntu/Debian)
```bash
sudo apt-get install linux-headers-$(uname -r) python3-dev gcc
pip install -r requirements.txt
```

### Linux (Fedora/RHEL)
```bash
sudo dnf install kernel-headers-$(uname -r) python3-devel gcc
pip install -r requirements.txt
```

---

## 📱 Phone UI Features

| Feature | Description |
|---|---|
| **Tap to Type** | Opens native phone keyboard |
| **Live Preview** | Shows what you've typed with real cursor position |
| **Cursor Repositioning** | Tap inside preview to move cursor; edits go there |
| **Quick Keys** | Enter, Tab, Backspace, Escape, Delete, CapsLock, Arrows |
| **Modifier Keys** | Shift, Ctrl, Alt — tap to hold, releases after next key |
| **Function Keys** | F1–F12 in a scrollable row |
| **Auto-reconnect** | Reconnects automatically if WiFi drops or screen turns off |

---

## 🔌 Connection Behavior

| Event | What Happens |
|---|---|
| Phone screen turns OFF | WebSocket closes (OS kills background connections) |
| Phone screen turns ON | Browser auto-reconnects with a new connection ID |
| Multiple phones connect | All phones control the same laptop simultaneously |
| WiFi drops briefly | Exponential backoff reconnect (1s → 2s → 4s → max 16s) |

---

## ❓ FAQ

**Q: Does the IP in the terminal change for different users?**
> Yes. `server.py` auto-detects your laptop's LAN IP at runtime.
> If someone in another country clones this repo, they will see
> their own laptop's IP — not yours. GitHub only stores code, never IPs.

**Q: Can multiple phones connect at once?**
> Yes. All phones on the same WiFi can connect simultaneously.
> Each controls the same laptop keyboard.

**Q: Is it secure?**
> PhoneKey is LAN-only. The WebSocket port (8765) is not exposed
> to the internet — only devices on your local WiFi can reach it.

**Q: Why does the terminal show "no close frame received or sent"?**
> This is normal. It means the phone's browser closed the connection
> abruptly (e.g. screen turned off) without sending a WebSocket
> close handshake. The server handles it cleanly.

**Q: Can I run this in Firebase Studio / GitHub Codespaces?**
> No. The server must run on your local laptop. Cloud environments
> have no physical display or keyboard input layer. Use Firebase Studio
> only to edit code, then push to GitHub and run on your laptop.

---

## 📁 Project Structure

```
phonekey/
├── server.py           ← Main server (run this on your laptop)
├── test_server.py     ← Unit tests for core logic
├── requirements.txt    ← Python dependencies
├── README.md           ← This file
├── LICENSE             ← MIT License
├── .gitignore
└── client/
    └── index.html      ← Phone browser UI (auto-served by server.py)
```

---

## 🤝 Contributing

1. Fork the repo
2. Create a feature branch: `git checkout -b feat/your-feature`
3. Commit: `git commit -m "feat: describe your change"`
4. Push: `git push origin feat/your-feature`
5. Open a Pull Request