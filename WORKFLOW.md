# PhoneKey End-to-End Workflow

## Overview
PhoneKey transforms your phone into a wireless keyboard and mouse for your laptop, with optional secure public access via Cloudflare Quick Tunnels.

---

## 1. Server Startup Phase

### A. Initialization (`python server.py [--tunnel]`)

**Command-Line Arguments:**
- `--ws-port`: WebSocket port (default: 8765)
- `--http-port`: HTTP server port (default: 8080)
- `--https`: Enable HTTPS/WSS with self-signed cert
- `--tunnel`: Enable Cloudflare Quick Tunnel
- `--no-pin`: Disable 4-digit PIN authentication
- `--mouse-speed`: Mouse sensitivity multiplier (0.1-5.0)

**What Happens:**
1. Parse CLI arguments
2. Set up logging with timestamps
3. Determine base directory (normal script vs PyInstaller .exe)
4. Check environment (block cloud IDEs, verify display/input)
5. Initialize libraries (pynput, pyperclip, qrcode)
6. Generate 4-digit PIN (if enabled)
7. Set up device registry for multi-device tracking

### B. Cloudflare Tunnel Setup (if `--tunnel`)

**Process:**
1. Import `TunnelManager` from `tunnel_manager.py`
2. Search for `cloudflared` binary (CWD → bin/ → PATH)
3. Start HTTP server in background thread (needed for tunnel)
4. Wait 2 seconds for server readiness
5. Launch: `cloudflared tunnel --url http://localhost:8080`
6. Capture generated URL from subprocess output
7. Example output: `https://abc123.trycloudflare.com`
8. If tunnel fails → fall back to local URL with warning

### C. SSL Certificate (if `--https`, without tunnel)

**Process:**
1. Generate self-signed certificate with local IP in SAN
2. Save to `phonekey-cert.pem` and `phonekey-key.pem`
3. Reuse existing cert if valid and IP matches
4. Create SSLContext for HTTPS/WSS encryption

### D. Print Banner

```
╔══════════════════════════════════════════════════╗
║         📱  PhoneKey  v3.1.0  💻            ║
╠══════════════════════════════════════════════════╣
║  OS   : Windows                                  ║
║  Mode : HTTPS (Cloudflare Tunnel) 🌐             ║
║  PIN: 4827                                       ║
╠══════════════════════════════════════════════════╣
║  URL : https://abc123.trycloudflare.com          ║
║                                                  ║
║  Scan QR code with phone camera                 ║
║  Phone & laptop can be on different networks!    ║
║  Press Ctrl+C to stop                           ║
╚══════════════════════════════════════════════════╝

  📷  Scan to open PhoneKey:
  [QR Code]
  
  🔐  PIN:  4827  ← Enter this on your phone
```

---

## 2. Server Running Phase

### A. HTTP Server (Daemon Thread)

**Port:** 8080 (or custom)

**Routes:**
- `GET /` → Welcome page with browser info and auto-redirect
- `GET /index.html` → Main PhoneKey UI (tab-based interface)
- Other paths → Static files from `client/` directory

**Features:**
- If tunnel active: Shows tunnel URL
- If PIN set: Injects PIN into page JavaScript
- SPA behavior: Unknown paths redirect to index.html

### B. WebSocket Server (Async)

**Port:** 8765 (or custom)

**Protocol:** 
- `ws://` (HTTP) or `wss://` (HTTPS/tunnel)
- JSON messages

**Features:**
- Phone connection with authentication
- Tab-ID deduplication (prevents duplicate tabs)
- Device registry with real-time updates
- Keyboard/mouse event queue (12ms spacing)
- Broadcasts device list to all connected phones

### C. Cloudflare Tunnel (If Enabled)

**Process:**
- Background subprocess: `cloudflared`
- Creates: `https://*.trycloudflare.com`
- Traffic flow: Phone → Cloudflare → Your laptop
- Benefits: No certificate warnings, works from any network
- Clean termination on server shutdown

---

## 3. Phone Connection Phase

### A. Scan QR Code

1. User scans QR code from terminal with phone camera
2. Opens URL in browser (Chrome, Safari, Firefox, etc.)
3. URL is either:
   - Tunnel: `https://abc123.trycloudflare.com`
   - Local: `http://192.168.0.104:8080`

### B. Welcome Page

1. Shows "Opening PhoneKey" with detected browser
2. Auto-redirects to main app after 5 seconds
3. Or user clicks "Open in Browser" button
4. Shows browser choice modal if needed

### C. Main App Loads

**Technology:** Vanilla HTML5/CSS3/ES6 (no frameworks)

**UI Tabs:**
1. **Keyboard:** Virtual keyboard, quick keys, modifiers, function keys
2. **Mouse:** Touch trackpad, gestures, scroll
3. **Clipboard:** Copy/paste between devices
4. **Devices:** Connected phones list, device naming

**WebSocket Connection:**
1. Connects to WebSocket server
2. Sends authentication with `tabId`
3. If PIN enabled: Prompts for 4-digit code
4. Server verifies and sends auth response
5. Tab-ID prevents duplicate connections

---

## 4. Usage Phase

### A. Keyboard Input

**Flow:**
```
Phone keyboard → Live preview → WebSocket → Server → pynput → OS
```

**Details:**
- User types in phone's text field
- Live preview shows typed characters
- Each key press sent as JSON via WebSocket
- Server injects into OS using pynput
- 12ms delay between keystrokes prevents drops
- Supports: Letters, numbers, symbols, Enter, Tab, Backspace, etc.

**Special Keys:**
- Quick: Enter, Tab, Backspace, Escape, Delete, Arrows
- Modifiers: Shift, Ctrl, Alt (tap to hold, releases after next key)
- Function: F1-F12 (scrollable interface)

### B. Mouse Control

**Flow:**
```
Phone touch → Delta calculation → WebSocket → Server → pynput → OS
```

**Gestures:**
- **Move:** Drag on trackpad area
- **Click:** Tap (left), Two-finger tap (right), Three-finger tap (middle)
- **Scroll:** Two-finger swipe
- **Speed:** Adjustable 0.1x to 5.0x multiplier

### C. Clipboard Sync

**Flow:**
```
Phone copy → "Copy to Laptop" → WebSocket → Server → pyperclip → Laptop clipboard
```

**Details:**
- Copy text on phone
- Click "Copy to Laptop" button
- Instantly available on laptop (Ctrl+V)
- Works across devices and networks

### D. Multi-Device Support

**Features:**
- Multiple phones connect simultaneously
- Each appears in Devices tab
- Real-time connection status
- All control same laptop keyboard
- Device naming for identification
- Tab-ID prevents duplicate tabs

---

## 5. Disconnection Phase

### A. Phone Screen Off

1. Browser kills WebSocket (background connections not allowed)
2. Server detects disconnect
3. Removes device from registry
4. Broadcasts updated device list
5. Exponential backoff reconnect when screen on

### B. Server Shutdown

1. User presses Ctrl+C
2. Signal handlers trigger (Win32 handler or Unix signal)
3. WebSocket server closes connections
4. HTTP server stops
5. Cloudflare tunnel process killed
6. Instance lock released
7. Clean exit message

---

## 6. Architecture Layers

### A. Client Layer (Phone Browser)
- HTML5/CSS3/ES6 UI
- WebSocket client
- Touch event handling
- Live preview

### B. Transport Layer
- HTTP server (static files, welcome page)
- WebSocket server (real-time communication)
- Cloudflare tunnel (optional public access)

### C. OS Layer
- pynput: Keyboard/mouse injection
- pyperclip: Clipboard sync
- System APIs: Windows (SendInput), macOS (Quartz), Linux (X11/Wayland)

---

## 7. Security Features

### A. Authentication
- Optional 4-digit PIN
- Verified on WebSocket connection
- Prevents unauthorized access

### B. Encryption
- HTTPS/WSS available (self-signed or tunnel)
- WebSocket over TLS (wss://)
- Cloudflare tunnel provides public HTTPS

### C. Network Security
- LAN-only by default
- Cloudflare tunnel opt-in
- No internet exposure without tunnel

### D. Session Management
- Tab-ID deduplication
- Device registry
- Clean disconnect handling

---

## 8. Error Handling

### A. Tunnel Failure
- Falls back to local connection
- Shows warning message
- Continues with HTTP/WS

### B. WebSocket Disconnect
- Exponential backoff reconnect
- Auto-reconnects when phone screen on
- Preserves device state

### C. Broken Pipe
- Graceful handling (phone screen off)
- No crash or traceback
- Clean resource cleanup

### D. Missing Dependencies
- Clear error messages
- Installation instructions
- Graceful degradation

---

## 9. Cross-Platform Support

### A. Windows
- Win32 API via ctypes
- Ctrl+C handling in .exe
- SendInput for keyboard/mouse

### B. macOS
- Quartz CGEvent API
- Accessibility permissions required
- Native look and feel

### C. Linux
- X11 or Wayland support
- evdev for input injection
- DISPLAY/WAYLAND_DISPLAY check

### D. PyInstaller
- Standalone executables
- Resource bundling
- Instance lock prevents duplicates

---

## 10. Data Flow Summary

### Without Tunnel:
```
Phone Browser
    ↓ (WebSocket JSON over LAN)
PhoneKey Server (Python)
    ↓ (pynput OS injection)
Operating System
    ↑ (Keystrokes/Mouse events)
Applications
```

### With Cloudflare Tunnel:
```
Phone Browser
    ↓ (HTTPS/WSS over internet)
Cloudflare Edge (encryption)
    ↓ (HTTPS to your laptop)
cloudflared (local process)
    ↓ (HTTP to localhost)
PhoneKey Server (Python)
    ↓ (pynput OS injection)
Operating System
    ↑ (Keystrokes/Mouse events)
Applications
```

---

## 11. Resource Usage

- **RAM:** ~20 MB (Python process)
- **CPU:** Minimal (event-driven)
- **Network:** Low bandwidth (WebSocket, binary efficient)
- **Latency:** <5ms over LAN

---

## 12. Key Benefits

✅ No phone app installation (pure browser)  
✅ Real-time performance (<5ms latency)  
✅ Multi-device support  
✅ Clipboard sync  
✅ Mouse + keyboard + gestures  
✅ Optional secure public access (Cloudflare)  
✅ No certificate warnings (with tunnel)  
✅ Cross-platform (Windows, macOS, Linux)  
✅ Lightweight (~20 MB RAM)  
✅ Open source and free  

---

## Quick Start

```bash
# Basic usage (local network)
python server.py

# With Cloudflare tunnel (secure public URL)
python server.py --tunnel --no-pin

# Custom configuration
python server.py --ws-port 9000 --http-port 9001 --mouse-speed 2.0
```

Then scan the QR code with your phone camera and start typing!
