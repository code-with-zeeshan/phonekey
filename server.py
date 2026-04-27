"""
PhoneKey — Core Server  (server.py)
Contract : WebSocket handler, HTTP handler, device registry, key/mouse injection,
           SSL management, PIN, QR code, startup banner, and signal setup.
           Receives a parsed argparse.Namespace from system.py; never parses
           argv itself and never acquires the instance lock.
"""

__version__ = "3.1.0"

# ─────────────────────────────────────────────
#  Standard Library
# ─────────────────────────────────────────────
import asyncio
import ipaddress
import json
import os
import random
import signal
import socket
import ssl
import sys
import threading
import time
import uuid
from argparse import Namespace
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Optional

# ─────────────────────────────────────────────
#  Third-Party
# ─────────────────────────────────────────────
import websockets

# ─────────────────────────────────────────────
#  PhoneKey modules
# ─────────────────────────────────────────────
from logging_setup import get_logger

logger     = get_logger("phonekey")
http_log   = get_logger("phonekey.http")
ws_log     = get_logger("phonekey.websocket")
input_log  = get_logger("phonekey.input")

# ─────────────────────────────────────────────
#  Tunnel (optional)
# ─────────────────────────────────────────────
try:
    from tunnel_manager import TunnelManager
    TUNNEL_AVAILABLE = True
except ImportError:
    TUNNEL_AVAILABLE = False

# ─────────────────────────────────────────────
#  Path Resolution (script + PyInstaller)
# ─────────────────────────────────────────────
if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
    BASE_DIR = Path(sys._MEIPASS)
else:
    BASE_DIR = Path(__file__).parent

CLIENT_DIR = BASE_DIR / "client"
CERT_FILE  = Path(__file__).parent / "phonekey-cert.pem"
KEY_FILE   = Path(__file__).parent / "phonekey-key.pem"

# ─────────────────────────────────────────────
#  Constants
# ─────────────────────────────────────────────
DEFAULT_WS_PORT   = 8765
DEFAULT_HTTP_PORT = 8080
KEY_INJECT_DELAY  = 0.012
WS_PING_INTERVAL  = 30
WS_PING_TIMEOUT   = 60

# ─────────────────────────────────────────────
#  Runtime globals — set inside main()
# ─────────────────────────────────────────────
_WS_PORT:     int            = DEFAULT_WS_PORT
_USE_HTTPS:   bool           = False
_TUNNEL_URL:  Optional[str]  = None
_SESSION_PIN: Optional[str]  = None
_MOUSE_SPEED: float          = 1.0

# ─────────────────────────────────────────────
#  Windows Ctrl+C — SetConsoleCtrlHandler
# ─────────────────────────────────────────────
_stop_event_ref: asyncio.Event | None = None
_loop_ref:       asyncio.AbstractEventLoop | None = None

if sys.platform == "win32":
    import ctypes
    import ctypes.wintypes

    _CTRL_C_EVENT     = 0
    _CTRL_CLOSE_EVENT = 2
    _HandlerRoutine   = ctypes.WINFUNCTYPE(ctypes.wintypes.BOOL, ctypes.wintypes.DWORD)

    def _win_ctrl_handler(ctrl_type: int) -> bool:
        if ctrl_type in (_CTRL_C_EVENT, _CTRL_CLOSE_EVENT):
            logger.info("🛑 Shutting down PhoneKey…")
            if _stop_event_ref and _loop_ref:
                _loop_ref.call_soon_threadsafe(_stop_event_ref.set)
            return True
        return False

    _handler_ref = _HandlerRoutine(_win_ctrl_handler)

    def _register_win_ctrl_handler() -> None:
        ctypes.windll.kernel32.SetConsoleCtrlHandler(_handler_ref, True)
        logger.debug("✅ Windows SetConsoleCtrlHandler registered.")

# ─────────────────────────────────────────────
#  Key Maps
# ─────────────────────────────────────────────

def _build_key_maps():
    """Defer pynput import so the module can be imported safely in tests."""
    from pynput.keyboard import Key
    from pynput.mouse    import Button

    special: dict[str, Key] = {
        "Enter": Key.enter,      "Backspace": Key.backspace, "Tab": Key.tab,
        "Escape": Key.esc,       "Delete":    Key.delete,
        "ArrowUp": Key.up,       "ArrowDown": Key.down,
        "ArrowLeft": Key.left,   "ArrowRight": Key.right,
        "Home": Key.home,        "End": Key.end,
        "PageUp": Key.page_up,   "PageDown": Key.page_down,
        "CapsLock": Key.caps_lock,
        "Shift": Key.shift,      "Control": Key.ctrl,       "Alt": Key.alt,
        "Meta": Key.cmd,
        "ShiftRight": Key.shift_r, "ControlRight": Key.ctrl_r, "AltRight": Key.alt_r,
        **{f"F{n}": getattr(Key, f"f{n}") for n in range(1, 13)},
    }
    mouse_btns = {
        "left": Button.left, "right": Button.right, "middle": Button.middle,
    }
    return special, mouse_btns

# ─────────────────────────────────────────────
#  Device Registry
# ─────────────────────────────────────────────

@dataclass
class ConnectedDevice:
    device_id: str
    name:      str
    websocket: object
    authed:    bool       = False
    tab_id:    str | None = None


_device_registry:  dict[str, ConnectedDevice] = {}
_tab_id_to_device: dict[str, str]             = {}
_registry_lock = threading.Lock()


def _try_register_device(
    device: ConnectedDevice, tab_id: str | None, client_addr: tuple
) -> bool:
    """Atomically register device; returns False on duplicate tab_id."""
    with _registry_lock:
        if tab_id and tab_id in _tab_id_to_device:
            existing = _tab_id_to_device[tab_id]
            logger.warning(
                "Duplicate tab rejected: tabId=%s from %s (already device %s)",
                tab_id, client_addr, existing[:8],
            )
            return False
        _device_registry[device.device_id] = device
        if device.tab_id:
            _tab_id_to_device[device.tab_id] = device.device_id
    return True


def _unregister_device(device_id: str) -> None:
    with _registry_lock:
        dev = _device_registry.get(device_id)
        if dev and dev.tab_id:
            _tab_id_to_device.pop(dev.tab_id, None)
        _device_registry.pop(device_id, None)


def _get_device_list() -> list[dict]:
    with _registry_lock:
        return [
            {"id": d.device_id, "name": d.name}
            for d in _device_registry.values()
            if d.authed
        ]


async def _broadcast_device_list() -> None:
    payload = json.dumps({"type": "device_list", "devices": _get_device_list()})
    with _registry_lock:
        targets = [d.websocket for d in _device_registry.values() if d.authed]
    for ws in targets:
        try:
            await ws.send(payload)
        except Exception:
            pass

# ─────────────────────────────────────────────
#  Key / Mouse Queue
# ─────────────────────────────────────────────
key_queue: asyncio.Queue = asyncio.Queue()


def _inject_key(data: dict, keyboard, SPECIAL_KEY_MAP: dict) -> None:
    action    = data.get("action", "")
    key_value = data.get("key",    "")
    if not action or not key_value:
        return
    resolved = SPECIAL_KEY_MAP.get(key_value, key_value)
    try:
        if   action == "keypress": keyboard.press(resolved); keyboard.release(resolved)
        elif action == "keydown":  keyboard.press(resolved)
        elif action == "keyup":    keyboard.release(resolved)
    except Exception as exc:
        input_log.error("Key inject failed '%s': %s", key_value, exc)


def _inject_mouse(data: dict, mouse, MOUSE_BUTTON_MAP: dict) -> None:
    from pynput.mouse import Button
    action = data.get("action", "")
    try:
        if action == "mouse_move":
            dx = int(data.get("dx", 0) * _MOUSE_SPEED)
            dy = int(data.get("dy", 0) * _MOUSE_SPEED)
            if dx or dy:
                mouse.move(dx, dy)
        elif action == "mouse_click":
            btn = MOUSE_BUTTON_MAP.get(data.get("button", "left"), Button.left)
            mouse.click(btn, 2 if data.get("double") else 1)
        elif action == "mouse_scroll":
            mouse.scroll(data.get("dx", 0), data.get("dy", 0))
    except Exception as exc:
        input_log.error("Mouse inject failed '%s': %s", action, exc)


def _make_key_worker(keyboard, mouse, SPECIAL_KEY_MAP, MOUSE_BUTTON_MAP):
    async def key_worker() -> None:
        while True:
            data   = await key_queue.get()
            action = data.get("action", "")
            if action in ("mouse_move", "mouse_click", "mouse_scroll"):
                _inject_mouse(data, mouse, MOUSE_BUTTON_MAP)
            else:
                _inject_key(data, keyboard, SPECIAL_KEY_MAP)
            key_queue.task_done()
            if action not in ("mouse_move", "mouse_scroll"):
                await asyncio.sleep(KEY_INJECT_DELAY)
    return key_worker

# ─────────────────────────────────────────────
#  WebSocket Handler
# ─────────────────────────────────────────────

async def ws_handler(websocket) -> None:
    client_addr = websocket.remote_address
    device_id   = str(uuid.uuid4())
    device:     ConnectedDevice | None = None

    ws_log.info("📱 Phone connecting: %s", client_addr)

    try:
        try:
            raw = await asyncio.wait_for(websocket.recv(), timeout=30.0)
            msg = json.loads(raw)
        except asyncio.TimeoutError:
            await websocket.close(1008, "auth_timeout")
            return
        except json.JSONDecodeError:
            await websocket.close(1003, "bad_json")
            return

        tab_id = msg.get("tabId")
        if tab_id is not None and (not isinstance(tab_id, str) or not tab_id.strip()):
            tab_id = None

        action = msg.get("action", "")

        if action == "hello":
            if _SESSION_PIN is not None:
                await websocket.close(1008, "pin_required")
                return
            device = ConnectedDevice(
                device_id=device_id,
                name=f"Device-{device_id[:4]}",
                websocket=websocket,
                authed=True,
                tab_id=tab_id,
            )
            if not _try_register_device(device, tab_id, client_addr):
                await websocket.close(1008, "duplicate_tab")
                device = None
                return
            ws_log.info("📱 Connected (no-PIN): %s (id=%s tabId=%s)", client_addr, device_id[:8], tab_id)

        elif action == "pin_auth":
            if _SESSION_PIN is None:
                await websocket.close(1008, "pin_not_required")
                return
            if msg.get("pin", "") != _SESSION_PIN:
                ws_log.warning("🔒 Wrong PIN from %s", client_addr)
                await websocket.send(json.dumps({"type": "auth_fail", "reason": "wrong_pin"}))
                await websocket.close(1008, "wrong_pin")
                return
            device = ConnectedDevice(
                device_id=device_id,
                name=f"Device-{device_id[:4]}",
                websocket=websocket,
                authed=True,
                tab_id=tab_id,
            )
            if not _try_register_device(device, tab_id, client_addr):
                await websocket.close(1008, "duplicate_tab")
                device = None
                return
            ws_log.info("🔓 PIN verified: %s (id=%s tabId=%s)", client_addr, device_id[:8], tab_id)

        else:
            await websocket.close(1008, "expected_auth")
            return

        await websocket.send(json.dumps({"type": "auth_ok", "device_id": device_id}))
        await _broadcast_device_list()

        async for raw in websocket:
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue

            action = msg.get("action", "")

            if action == "device_name":
                if device:
                    device.name = str(msg.get("name", ""))[:32].strip() or device.name
                    ws_log.info("📛 Device renamed: '%s' (%s)", device.name, client_addr)
                    await _broadcast_device_list()
                continue

            if action == "clipboard_push":
                text = msg.get("text", "")
                if text and _CLIPBOARD_AVAILABLE:
                    try:
                        import pyperclip
                        pyperclip.copy(text)
                        await websocket.send(json.dumps({"type": "clipboard_ack"}))
                        logger.info("📋 Clipboard: %d chars from %s", len(text), client_addr)
                    except Exception as exc:
                        await websocket.send(json.dumps({"type": "clipboard_ack", "error": str(exc)}))
                elif not _CLIPBOARD_AVAILABLE:
                    await websocket.send(json.dumps(
                        {"type": "clipboard_ack", "error": "pyperclip not installed"}
                    ))
                continue

            if action in ("keypress","keydown","keyup","mouse_move","mouse_click","mouse_scroll"):
                await key_queue.put(msg)

    except websockets.exceptions.ConnectionClosedOK:
        ws_log.info("📴 Disconnected (clean): %s", client_addr)
    except websockets.exceptions.ConnectionClosedError as exc:
        ws_log.warning("📴 Disconnected (error): %s | %s", client_addr, exc)
    except Exception as exc:
        ws_log.error("WS error from %s: %s", client_addr, exc)
    finally:
        if device:
            _unregister_device(device.device_id)
            await _broadcast_device_list()

# ─────────────────────────────────────────────
#  HTTP Server
# ─────────────────────────────────────────────

class PhoneKeyHTTPHandler(BaseHTTPRequestHandler):
    """
    Serves:
      GET /            → animated welcome / browser-chooser page
      GET /api/config  → JSON config for the client (pin_required, version)
      GET /index.html  → main PhoneKey SPA
      GET /*           → static files from CLIENT_DIR
    """

    def do_GET(self):

        # ── /api/config  — replaces the brittle PIN string-injection ───────
        if self.path == "/api/config":
            payload = json.dumps({
                "pin_required": _SESSION_PIN is not None,
                "version": __version__,
                # When tunnel is active, client must use wss://tunnel-host (not LAN IP)
                # When None, client uses its default WS_URL calculation
                "ws_url":        _WS_URL_OVERRIDE,
            }).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type",   "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control",  "no-cache")
            self.end_headers()
            self.wfile.write(payload)
            return

        # ── /  → welcome page ──────────────────────────────────────────────
        if self.path == "/":
            proto    = "https" if (_USE_HTTPS or _TUNNEL_URL) else "http"
            main_url = _TUNNEL_URL or f"{proto}://{self.headers.get('Host', 'localhost')}"
            page     = _build_welcome_page(main_url, _WS_PORT).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type",   "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(page)))
            self.send_header("Cache-Control",  "no-cache")
            self.end_headers()
            self.wfile.write(page)
            return

        # ── Static files from CLIENT_DIR ───────────────────────────────────
        path      = self.path.split("?")[0].lstrip("/") or "index.html"
        file_path = CLIENT_DIR / path

        if not file_path.exists() or not file_path.is_file():
            file_path = CLIENT_DIR / "index.html"   # SPA fallback

        if not file_path.exists():
            self.send_error(404, "Not Found")
            return

        suffix   = file_path.suffix.lower()
        mime_map = {
            ".html": "text/html; charset=utf-8",
            ".css":  "text/css",
            ".js":   "application/javascript",
            ".ico":  "image/x-icon",
            ".png":  "image/png",
            ".svg":  "image/svg+xml",
        }
        mime    = mime_map.get(suffix, "application/octet-stream")
        content = file_path.read_bytes()

        self.send_response(200)
        self.send_header("Content-Type",   mime)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control",  "no-cache")
        self.end_headers()
        self.wfile.write(content)

    def log_message(self, format, *args):   # noqa: A002
        pass   # Suppress per-request HTTP logs


class PhoneKeyHTTPServer(HTTPServer):
    """Silently drops expected mobile-disconnect errors."""
    _IGNORED_WIN = {10053, 10054, 10058}

    def handle_error(self, request, client_address):
        exc_type, exc_val, _ = sys.exc_info()
        if exc_type in (BrokenPipeError, ConnectionResetError):
            return
        if exc_type is OSError and getattr(exc_val, "winerror", None) in self._IGNORED_WIN:
            return
        http_log.error("HTTP error from %s: %s: %s", client_address, exc_type, exc_val)


def start_http_server(ssl_ctx: ssl.SSLContext | None) -> None:
    httpd = PhoneKeyHTTPServer(("0.0.0.0", _HTTP_PORT), PhoneKeyHTTPHandler)
    if ssl_ctx is not None:
        httpd.socket = ssl_ctx.wrap_socket(httpd.socket, server_side=True)
    proto = "HTTPS" if ssl_ctx else "HTTP"
    logger.info("🌐 %s server → port %d", proto, _HTTP_PORT)
    httpd.serve_forever()

# ─────────────────────────────────────────────
#  SSL
# ─────────────────────────────────────────────

def build_ssl_context(local_ip: str) -> ssl.SSLContext:
    try:
        from cryptography                              import x509
        from cryptography.x509.oid                    import NameOID
        from cryptography.hazmat.primitives            import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
    except ImportError:
        logger.error("❌  cryptography not installed. Run: pip install cryptography")
        sys.exit(1)

    if CERT_FILE.exists() and KEY_FILE.exists():
        try:
            existing  = x509.load_pem_x509_certificate(CERT_FILE.read_bytes())
            remaining = existing.not_valid_after_utc - datetime.now(timezone.utc)
            san_ext   = existing.extensions.get_extension_for_class(x509.SubjectAlternativeName)
            has_ip    = any(
                isinstance(s, x509.IPAddress) and s.value == ipaddress.IPv4Address(local_ip)
                for s in san_ext.value
            )
            if remaining.days > 7 and has_ip:
                logger.info("♻️  Reusing TLS certificate (%d days left).", remaining.days)
                ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
                ctx.load_cert_chain(certfile=str(CERT_FILE), keyfile=str(KEY_FILE))
                return ctx
        except Exception:
            pass

    logger.info("🔐 Generating self-signed TLS certificate…")
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME,         local_ip),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME,   "PhoneKey Local"),
    ])
    san_list = [x509.DNSName("localhost"), x509.DNSName("phonekey.local")]
    try:
        san_list.append(x509.IPAddress(ipaddress.IPv4Address(local_ip)))
    except ValueError:
        pass

    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(timezone.utc))
        .not_valid_after(datetime.now(timezone.utc) + timedelta(days=365))
        .add_extension(x509.SubjectAlternativeName(san_list), critical=False)
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(private_key, hashes.SHA256())
    )
    CERT_FILE.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    KEY_FILE.write_bytes(private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    ))
    logger.info("✅ TLS certificate saved → %s", CERT_FILE.name)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(certfile=str(CERT_FILE), keyfile=str(KEY_FILE))
    return ctx

# ─────────────────────────────────────────────
#  Networking helpers
# ─────────────────────────────────────────────
_HTTP_PORT: int = DEFAULT_HTTP_PORT     # set in main()


def get_local_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"

# ─────────────────────────────────────────────
#  QR Code + URL Display
# ─────────────────────────────────────────────

def print_qr_and_url(url: str) -> None:
    """
    Prints the ASCII QR code and the URL below it in the terminal.
    The QR encodes /index.html directly — no welcome page redirect.
    """
    # Always point QR directly at the app, not the welcome page
    app_url = url.rstrip("/") + "/index.html"

    try:
        import qrcode
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_L,
            box_size=1,
            border=1,
        )
        qr.add_data(app_url)
        qr.make(fit=True)
        print()
        print("  📷  Scan QR code with your phone camera:")
        print()
        for row in qr.get_matrix():
            print("  " + "".join("██" if cell else "  " for cell in row))
    except ImportError:
        print()
        print("  ⚠️  Install qrcode for QR display: pip install qrcode")

    print()
    print("  ─────────────────────────────────────────────────")
    print(f"  🔗  URL: {app_url}")
    print("  ─────────────────────────────────────────────────")
    print("  📱  Scan the QR code above OR manually enter")
    print("      the URL in your phone browser")
    print("  ─────────────────────────────────────────────────")
    print()

# ─────────────────────────────────────────────
#  Startup Banner
# ─────────────────────────────────────────────

def print_banner(local_ip: str, ssl_ctx: ssl.SSLContext | None,
                 tunnel_url: Optional[str] = None) -> None:
    proto    = "https" if (ssl_ctx or tunnel_url) else "http"
    main_url = tunnel_url or f"{proto}://{local_ip}:{_HTTP_PORT}"
    os_label = {"win32": "Windows", "darwin": "macOS", "linux": "Linux"}.get(sys.platform, sys.platform)
    pin_line = f"PIN: {_SESSION_PIN}" if _SESSION_PIN else "PIN: Disabled (--no-pin)"

    print()
    print("╔══════════════════════════════════════════════════╗")
    print(f"║         📱  PhoneKey  v{__version__}  💻            ║")
    print("╠══════════════════════════════════════════════════╣")
    print(f"║  OS   : {os_label:<41}║")
    if tunnel_url:
        print(f"║  Mode : {'HTTPS (Cloudflare Tunnel) 🌐':<41}║")
    else:
        print(f"║  Mode : {('HTTPS/WSS 🔒' if ssl_ctx else 'HTTP/WS  🔓'):<41}║")
    print(f"║  {pin_line:<48}║")
    print("╠══════════════════════════════════════════════════╣")
    print(f"║  URL : {main_url:<43}║")
    print("║                                                  ║")
    print("║  Scan QR code with phone camera                 ║")
    print("║  Phone & laptop must be on the same WiFi        ║" if not tunnel_url else
          "║  Phone & laptop can be on different networks!   ║")
    print("║  Press Ctrl+C to stop                           ║")
    print("╚══════════════════════════════════════════════════╝")
    if ssl_ctx and not tunnel_url:
        print("\n  ⚠️  HTTPS: Phone will show a certificate warning.")
        print("     Android: Advanced → Proceed to site")
        print("     iOS    : Show Details → Visit this website")
    print_qr_code(main_url)
    if _SESSION_PIN:
        print(f"  🔐  PIN:  {_SESSION_PIN}  ← Enter this on your phone\n")

# ─────────────────────────────────────────────
#  Welcome Page (served at /)
# ─────────────────────────────────────────────

def _build_welcome_page(main_url: str, ws_port: int) -> str:
    """
    Lightweight landing page served at /.
    Immediately meta-refreshes to /index.html.
    Shows a human-readable message in case the redirect is slow.
    The QR code in the terminal already points to /index.html directly,
    so this page is only reached by users who manually type the base URL.
    """
    app_url = main_url.rstrip("/") + "/index.html"
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8"/>
  <meta name="viewport" content="width=device-width,initial-scale=1,user-scalable=no"/>
  <meta http-equiv="refresh" content="0;url=/index.html"/>
  <meta name="theme-color" content="#0a0c14"/>
  <title>PhoneKey</title>
  <link rel="icon" href="/phonekey.ico" sizes="any"/>
  <link rel="icon" type="image/svg+xml" href="/phonekey.svg"/>
  <style>
    * {{ box-sizing:border-box; margin:0; padding:0; }}
    body {{
      background:#0a0c14; color:#e8eaf6;
      font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
      min-height:100vh; display:flex; align-items:center;
      justify-content:center; text-align:center; padding:24px;
    }}
    .card {{
      max-width:360px; width:100%;
    }}
    .logo {{ font-size:3rem; margin-bottom:16px; }}
    h1 {{ font-size:1.5rem; font-weight:700; margin-bottom:8px; }}
    p  {{ font-size:0.9rem; color:#9e9eb8; line-height:1.6; margin-bottom:20px; }}
    a  {{
      display:inline-block; padding:14px 28px;
      background:linear-gradient(135deg,#6c63ff,#5b52e0);
      color:#fff; border-radius:12px; font-weight:600;
      text-decoration:none; font-size:1rem;
    }}
  </style>
</head>
<body>
  <div class="card">
    <div class="logo">🔐</div>
    <h1>PhoneKey</h1>
    <p>Opening app… if nothing happens, tap the button below.</p>
    <a href="/index.html">Open PhoneKey →</a>
  </div>
  <script>window.location.replace("/index.html");</script>
</body>
</html>"""


# ─────────────────────────────────────────────
#  Environment Guard
# ─────────────────────────────────────────────

def _check_environment() -> None:
    if sys.platform == "linux":
        has_display   = bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
        has_dev_input = os.path.exists("/dev/input")
        is_cloud      = any(os.environ.get(k) for k in (
            "GOOGLE_CLOUD_PROJECT", "FIREBASE_PROJECT", "CODESPACES", "GITPOD_WORKSPACE_ID"
        ))
        if is_cloud:
            logger.error("❌  Cloud environment — run on your local laptop.")
            sys.exit(1)
        if not has_display and not has_dev_input:
            logger.error("❌  Headless environment — no display or /dev/input.")
            sys.exit(1)
    backend_map = {
        "win32": "Win32 SendInput API",
        "darwin": "macOS Quartz CGEvent",
        "linux": "X11 / Wayland / evdev",
    }
    logger.info("✅ Environment: %s | Backend: %s",
                sys.platform, backend_map.get(sys.platform, sys.platform))

# ─────────────────────────────────────────────
#  Signal Setup
# ─────────────────────────────────────────────

def _setup_signals(stop_event: asyncio.Event,
                   loop: asyncio.AbstractEventLoop) -> None:
    global _stop_event_ref, _loop_ref
    _stop_event_ref = stop_event
    _loop_ref       = loop

    def _stop(*_) -> None:
        logger.info("🛑 Shutting down PhoneKey…")
        loop.call_soon_threadsafe(stop_event.set)

    if sys.platform == "win32":
        _register_win_ctrl_handler()
        try:
            signal.signal(signal.SIGINT, _stop)
        except (ValueError, OSError):
            pass
    else:
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, _stop)

# ─────────────────────────────────────────────
#  Main — called by system.py
# ─────────────────────────────────────────────
_CLIPBOARD_AVAILABLE: bool = False


async def main(args: Namespace) -> None:
    """
    Server entry point. Receives parsed CLI args from system.py.
    All runtime state is initialised here — never at module level.
    """
    global _WS_PORT, _USE_HTTPS, _TUNNEL_URL, _SESSION_PIN, _MOUSE_SPEED
    global _HTTP_PORT, _CLIPBOARD_AVAILABLE, _WS_URL_OVERRIDE

    # ── Validate + store runtime config ──────────────────────────────────
    _WS_PORT     = args.ws_port
    _HTTP_PORT   = args.http_port
    _USE_HTTPS   = args.https
    _MOUSE_SPEED = max(0.1, min(5.0, args.mouse_speed))
    _SESSION_PIN = (
        None if args.no_pin
        else f"{random.SystemRandom().randint(0, 9999):04d}"
    )

    # ── Environment check ────────────────────────────────────────────────
    _check_environment()

    # ── pynput ───────────────────────────────────────────────────────────
    try:
        from pynput.keyboard import Controller as KbController
        from pynput.mouse    import Controller as MsController
        keyboard = KbController()
        mouse    = MsController()
        logger.info("✅ pynput keyboard + mouse initialized.")
    except Exception as exc:
        logger.error("❌  pynput failed: %s", exc)
        sys.exit(1)

    SPECIAL_KEY_MAP, MOUSE_BUTTON_MAP = _build_key_maps()

    # ── pyperclip ────────────────────────────────────────────────────────
    try:
        import pyperclip          # noqa: F401
        _CLIPBOARD_AVAILABLE = True
        logger.info("✅ pyperclip clipboard sync available.")
    except ImportError:
        _CLIPBOARD_AVAILABLE = False
        logger.warning("⚠️  pyperclip not installed — clipboard sync disabled.")

    # ── Network + SSL ─────────────────────────────────────────────────────
    local_ip = get_local_ip()
    ssl_ctx  = build_ssl_context(local_ip) if _USE_HTTPS else None

    # ── Tunnel (optional) ─────────────────────────────────────────────────
    tunnel_url:     Optional[str]           = None
    _TUNNEL_URL = None
    _WS_URL_OVERRIDE = None   # when set, client uses this WS URL instead of default

    if args.tunnel:
        if not TUNNEL_AVAILABLE:
            logger.error("❌  --tunnel requested but tunnel_manager module not found.")
            sys.exit(1)
        # HTTP must be up before tunnel can proxy it
        threading.Thread(target=start_http_server, args=(ssl_ctx,), daemon=True).start()
        import time
        time.sleep(2)
        tunnel_manager = TunnelManager(_HTTP_PORT)
        tunnel_url     = tunnel_manager.start()
        if tunnel_url:
            _TUNNEL_URL = tunnel_url
            # WebSocket also goes through the tunnel via wss://
            # Cloudflare tunnels support WS — strip https:// and use wss://
            tunnel_host      = tunnel_url.replace("https://", "").replace("http://", "")
            _WS_URL_OVERRIDE = f"wss://{tunnel_host}"
            logger.info("🌐 Tunnel URL: %s", tunnel_url)
            logger.info("🔌 WS via tunnel: %s", _WS_URL_OVERRIDE)
        else:
            logger.warning("⚠️  Tunnel failed — serving on local URL.")
    else:
        threading.Thread(target=start_http_server, args=(ssl_ctx,), daemon=True).start()

    # ── Banner ────────────────────────────────────────────────────────────
    print_banner(local_ip, ssl_ctx, tunnel_url)
    print_qr_and_url(_TUNNEL_URL or f"{'https' if ssl_ctx else 'http'}://{local_ip}:{_HTTP_PORT}")
    logger.info("🔌 WebSocket (%s) → port %d", ws_proto.upper(), _WS_PORT)

    # ── Event loop + signals ──────────────────────────────────────────────
    stop_event = asyncio.Event()
    loop       = asyncio.get_running_loop()
    _setup_signals(stop_event, loop)

    # ── Key worker ─────────────────────────────────────────────────────────
    key_worker = _make_key_worker(keyboard, mouse, SPECIAL_KEY_MAP, MOUSE_BUTTON_MAP)
    worker     = asyncio.create_task(key_worker())

    # ── WebSocket server ──────────────────────────────────────────────────
    async with websockets.serve(
        ws_handler,
        "0.0.0.0",
        _WS_PORT,
        ssl=ssl_ctx,
        ping_interval=WS_PING_INTERVAL,
        ping_timeout=WS_PING_TIMEOUT,
    ):
        logger.info("✅ Server ready — waiting for phone connection…")
        await stop_event.wait()

    worker.cancel()
    try:
        await worker
    except asyncio.CancelledError:
        pass

    if tunnel_manager:
        tunnel_manager.stop()