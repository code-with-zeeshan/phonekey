# 📱 PhoneKey

> Use your phone as a wireless keyboard and mouse — no app install required.

PhoneKey runs a lightweight server on your laptop. Scan a QR code with your
phone camera and your phone browser instantly becomes a full keyboard, trackpad,
and clipboard bridge. No Bluetooth pairing, no app store, no cloud account.

![Version](https://img.shields.io/badge/version-3.2.0-6c63ff)
![Python](https://img.shields.io/badge/python-3.11%2B-blue)
![License](https://img.shields.io/badge/license-MIT-green)
![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey)

---

## ✨ Features

| Feature | Details |
|---|---|
| ⌨️ Full keyboard | All keys, modifiers (Shift/Ctrl/Alt), function keys F1–F12 |
| 🖱️ Mouse trackpad | Move, click, right-click, scroll, double-click with touch gestures |
| 📋 Clipboard sync | Type on phone → paste on laptop with one tap; bidirectional sync option |
| 📋 Clipboard history | Access recent clipboard items; persistent across restarts |
| 📎 File transfer | Send files from phone to laptop (up to 10MB, multiple formats) |
| 🎮 Macro recording | Record and playback keyboard/mouse sequences per device |
| 🖐️ Gesture commands | Touch gestures for window management (alt-tab, show desktop, etc.) |
| 🌐 Multi-language | English, 中文, Español UI support |
| � PIN security | 4-digit PIN prevents unauthorised connections |
| 🌐 Tunnel mode | Cloudflare Quick Tunnel — works across different networks |
| 🔒 HTTPS/WSS | Self-signed certificate for encrypted local connections |
| 📱 Multi-device | Multiple phones can connect simultaneously |
| 🎨 Dark/Light theme | Persisted theme preference per device |
| 🖥️ Interactive TUI | First-run setup screen — no CLI knowledge required |
| 📦 Standalone binary | Single `.exe` / binary — zero Python install on target machine |

---

## 🚀 Quick Start

### Option A — Python (recommended for development)

```bash
# 1. Clone
git clone https://github.com/code-with-zeeshan/phonekey.git
cd phonekey

# 2. Install dependencies
pip install -r requirements.txt

# 3. Run
python system.py
```

An interactive setup screen guides you through connection mode, PIN, and
mouse speed. Scan the QR code with your phone camera and you're done.

### Option B — Standalone Binary (no Python required)

Download the latest binary for your platform from
[Releases](https://github.com/code-with-zeeshan/phonekey/releases):

| Platform | File |
|---|---|
| Windows | `phonekey-windows.exe` |
| macOS | `phonekey-macos` |
| Linux | `phonekey-linux` |

**Windows:** Double-click `phonekey-windows.exe` — an interactive setup
screen appears in the terminal window.

**macOS / Linux:**
```bash
chmod +x phonekey-macos   # or phonekey-linux
./phonekey-macos
```

---

## 🔌 Connection Modes

### Mode 1 — Local WiFi (default)
Phone and laptop must be on the **same WiFi network**.

```
Phone ──WiFi──► PhoneKey HTTP :8080
                PhoneKey WS   :8765
```

No certificate warning. Works offline. Best for home/office use.

### Mode 2 — Local HTTPS (`--https`)
Same WiFi, but traffic is encrypted with a self-signed certificate.

```bash
python system.py --https
```

Phone will show a **one-time certificate warning** — tap
"Advanced → Proceed" (Android) or "Show Details → Visit" (iOS).
The certificate is regenerated automatically if your LAN IP changes.

### Mode 3 — Cloudflare Tunnel (`--tunnel`)
Phone and laptop can be on **completely different networks**.

```bash
python system.py --tunnel
```

- Creates a temporary `https://*.trycloudflare.com` public URL
- No certificate warnings — Cloudflare's certificate is trusted
- `cloudflared` binary is **auto-downloaded** on first use
- URL changes on every restart — re-scan QR code after restart
- Both devices need internet access

```
Phone ──Internet──► trycloudflare.com ──► cloudflared ──► PhoneKey
```

---

## ⚙️ Configuration

### Interactive TUI (default)

Running `python system.py` or double-clicking the `.exe` shows:

```
╔══════════════════════════════════════════════════════════╗
║           📱  PhoneKey  v3.2.0  💻                       ║
║      Use your phone as a wireless keyboard & mouse       ║
╚══════════════════════════════════════════════════════════╝

  Choose connection mode:

  [1]  Local WiFi    — phone & laptop on same network (default)
  [2]  Local HTTPS   — same WiFi, encrypted
  [3]  Cloudflare Tunnel  — phone & laptop on ANY network

  Mode [1/2/3] →
```

### CLI Flags (skip the TUI)

```bash
python system.py [OPTIONS]

Options:
  --ws-port    PORT    WebSocket port          (default: 8765)
  --http-port  PORT    HTTP server port        (default: 8080)
  --https              Enable HTTPS/WSS
  --no-pin             Disable PIN auth
  --tunnel             Enable Cloudflare tunnel
  --mouse-speed MULT   Mouse speed 0.1–5.0     (default: 1.0)
  --clipboard-sync-direction MODE  Clipboard sync: phone_to_laptop (default), laptop_to_phone, or bidirectional
  --log-level  LEVEL   DEBUG/INFO/WARNING/ERROR (default: INFO)
  --yes / -y           Skip interactive setup, use defaults
```

**Examples:**
```bash
python system.py --yes                      # defaults, no prompts
python system.py --no-pin --mouse-speed 2   # fast mouse, no PIN
python system.py --tunnel --no-pin          # cross-network, no PIN
python system.py --ws-port 9000 --http-port 9001  # custom ports
python system.py --clipboard-sync-direction bidirectional  # sync both ways
```

### Environment Variables

All CLI flags have environment variable equivalents:

| Variable | Equivalent flag | Default |
|---|---|---|
| `PHONEKEY_ENV` | (selects config profile) | `development` |
| `PHONEKEY_LOG_LEVEL` | `--log-level` | `INFO` |

---

## 📱 Phone UI

After scanning the QR code, your phone browser shows:

| Tab | What it does |
|---|---|
| ⌨️ Keyboard | Live preview, quick keys, modifier keys, function keys |
| 🖱️ Mouse | Trackpad, click buttons, scroll, speed slider |
| 📋 Clipboard | Send text to laptop; clipboard history; file transfer |
| 🎮 Macros | Record and playback keyboard/mouse sequences |
| 🖐️ Gestures | Configure touch gestures for window management |
| 🌐 Language | Switch UI language (EN/中文/ES) |
| 📱 Devices | See connected devices, set your device name |

---

## 🔒 Security

- **PIN authentication** — 4-digit PIN displayed in terminal, entered on phone
- **Local-only by default** — no data leaves your LAN without `--tunnel`
- **Self-signed TLS** — certificates stored locally, regenerated on IP change
- **No persistent storage** — PIN changes on every server restart
- **Tab deduplication** — prevents phantom connections from the same browser tab

See [SECURITY.md](SECURITY.md) for responsible disclosure.

---

## 🗂️ Project Structure

```
phonekey/
├── system.py              # Entry point — CLI, interactive TUI, process lock
├── server.py              # Core server — WebSocket, HTTP, input injection
├── tunnel_manager.py      # Cloudflare tunnel lifecycle management
├── logging_setup.py       # Logging configuration (replaces 7-file logging suite)
├── config.py              # Configuration loader with environment overrides
├── config.json            # Configuration schema and defaults
│
├── client/
│   ├── index.html         # Phone browser SPA (keyboard, mouse, clipboard, devices)
│   ├── phonekey.ico       # Multi-size favicon
│   └── phonekey.svg       # SVG icon
│
├── test_server.py         # Unit + integration tests
├── phonekey.spec          # PyInstaller build spec
├── requirements.txt       # Runtime dependencies
├── requirements-dev.txt   # Dev + build dependencies
│
├── .github/
│   └── workflows/
│       └── release.yml    # CI — builds binaries for Win/macOS/Linux on version tag
│
├── README.md
├── CHANGELOG.md
├── WORKFLOW.md
├── CONTRIBUTING.md
└── SECURITY.md
```

### Module Contracts (non-overlapping)

| Module | Owns | Does NOT touch |
|---|---|---|
| `system.py` | Process lifecycle, CLI, instance lock, TUI | Server logic, networking |
| `server.py` | WebSocket, HTTP, device registry, input | Arg parsing, locking |
| `tunnel_manager.py` | cloudflared process (find, download, run) | HTTP/WS serving |
| `logging_setup.py` | Logger configuration | Application logic |
| `config.py` | Config schema + defaults | Runtime state |
| `client/index.html` | Phone browser UI + WebSocket client | Server-side logic |

---

## 🏗️ Building Standalone Binaries

```bash
pip install -r requirements-dev.txt
pyinstaller phonekey.spec
```

Output: `dist/phonekey` (Linux/macOS) or `dist/phonekey.exe` (Windows).

The spec bundles `client/index.html` and `client/phonekey.ico` into the binary.
Entry point is `system.py`.

### CI Builds

Pushing a version tag triggers GitHub Actions to build all three platforms:

```bash
git tag v3.2.0
git push origin v3.2.0
```

Binaries are attached to the GitHub Release automatically.

---

## 🧪 Running Tests

```bash
python -m pytest test_server.py -v
```

Tests cover client file validation, device registration, and duplicate
tab detection. Server tests are skipped gracefully if `pynput` is not
available in the test environment.

---

## 📦 Dependencies

| Package | Version | Purpose |
|---|---|---|
| `websockets` | 12.0 | WebSocket server |
| `pynput` | 1.7.6 | OS-level keyboard + mouse injection |
| `qrcode` | 7.4.2 | Terminal QR code display |
| `cryptography` | 42.0.5 | Self-signed TLS certificate generation |
| `pyperclip` | 1.8.2 | Cross-platform clipboard access |

**Optional (auto-downloaded at runtime):**
- `cloudflared` — Cloudflare tunnel binary (downloaded to `bin/` on first `--tunnel` use)

---

## 🖥️ Platform Support

| Platform | Keyboard | Mouse | Clipboard | HTTPS | Tunnel |
|---|---|---|---|---|---|
| Windows 10/11 | ✅ | ✅ | ✅ | ✅ | ✅ |
| macOS 12+ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Linux (X11) | ✅ | ✅ | ✅ | ✅ | ✅ |
| Linux (Wayland) | ✅ | ✅ | ⚠️ | ✅ | ✅ |

> **macOS note:** System Preferences → Security & Privacy → Accessibility —
> allow the terminal (or PhoneKey binary) to control your computer.

> **Linux note:** If running without a display server, ensure `/dev/input`
> is accessible. Cloud/headless environments are detected and rejected at startup.

---

## ❓ Troubleshooting

**Phone can't connect (same WiFi)**
- Confirm phone and laptop are on the same network (not guest/IoT VLAN)
- Check firewall allows ports 8080 and 8765 inbound
- Try `python system.py --no-pin` to rule out PIN issues

**"Already running" error**
- Another PhoneKey instance is using that port
- Run `python system.py --ws-port 9000 --http-port 9001`

**Certificate warning on HTTPS**
- Expected behaviour with self-signed certs
- Android: tap "Advanced" → "Proceed to site"
- iOS: tap "Show Details" → "Visit this website"
- Or use `--tunnel` for a trusted Cloudflare certificate

**Cloudflare tunnel URL not appearing**
- `cloudflared` auto-downloads to `bin/` on first run — needs internet
- Check `bin/cloudflared` exists and is executable
- Try running manually: `./bin/cloudflared tunnel --url http://localhost:8080`

**Ctrl+C not working in .exe**
- Known PyInstaller limitation — fixed in v3.2.0 via `SetConsoleCtrlHandler`
- If still stuck, close the terminal window with the ✕ button

**macOS: "cannot be opened because the developer cannot be verified"**
```bash
xattr -d com.apple.quarantine phonekey-macos
```

---

## 📄 License

MIT — see [LICENSE](LICENSE).

---

## 👤 Author

**Mohammad Zeeshan**
Built because Bluetooth keyboard pairing is annoying and cloud tools are overkill.