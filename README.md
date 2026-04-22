# 📱→💻 PhoneKey

> Use your phone as a wireless keyboard for your laptop — lightweight,
> real-time, and zero-install on the phone side.

[![License: MIT](https://img.shields.io/badge/License-MIT-purple.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.8%2B-blue.svg)](https://python.org)
[![Platform](https://img.shields.io/badge/Platform-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey.svg)]()

---

## 🧠 Why PhoneKey?

| Problem | PhoneKey Fix |
|---|---|
| Forgot physical keyboard | Use phone browser as keyboard |
| Other tools show "keeyy" duplicates | Sentinel + async queue pattern |
| Other tools eat laptop RAM | ~15 MB Python process only |
| Other tools require app install | Pure browser — no app needed |
| slow reflection on screen | WebSocket over LAN (<5ms latency) |

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
| Server | Python 3.8+ | Lightweight, cross-platform |
| Key Injection | `pynput 1.7.6` | Works on Windows/macOS/Linux |
| WebSocket | `websockets 12.0` (asyncio) | Non-blocking, minimal overhead |
| Key Queue | `asyncio.Queue` | Prevents fast-typing key drops |
| HTTP | `http.server` (stdlib) | Zero extra dependencies |
| Client | Vanilla HTML5/CSS3/ES6 | No framework, no app install |
| Input Fix | Sentinel + `input` event | Fixes mobile `keydown:229` bug |

---

## 📋 Requirements

- Python 3.8 or higher
- Phone and laptop on the **same WiFi network**
- Any modern phone browser (Chrome, Safari, Firefox)

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

### 5. Run the server

```bash
python server.py
```

### 6. Open on your phone

The terminal will show:

```
╔══════════════════════════════════════════════╗
║           📱  PhoneKey  v2.0.0  💻           ║
╠══════════════════════════════════════════════╣
║  OS detected : Windows                       ║
║  Open on your phone:                         ║
║  👉  http://192.168.0.104:8080               ║
╚══════════════════════════════════════════════╝
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