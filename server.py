"""
PhoneKey - Lightweight Phone-as-Keyboard & Mouse Server
Author: Mohammad Zeeshan
Version: 3.1.0
License: MIT

Features:
    - QR code in terminal for easy phone URL scanning
    - Custom ports via CLI args (--ws-port, --http-port, --https)
    - HTTPS/WSS support with auto-generated self-signed certificate
    - 4-digit Connection PIN for security
    - Multi-device tracking with device names
    - Mouse trackpad control (move, click, scroll)
    - Clipboard sync (phone clipboard → laptop)

IMPORTANT: Run on your LOCAL LAPTOP, not a cloud IDE.
"""

__version__ = "3.1.0"

# ─────────────────────────────────────────────
#  Standard Library
# ─────────────────────────────────────────────
import argparse
import asyncio
import errno
import ipaddress
import json
import logging
import os
import random
import signal
import socket
import ssl
import sys
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

# ─────────────────────────────────────────────
#  Third-Party
# ─────────────────────────────────────────────
import websockets

# ─────────────────────────────────────────────
#  Logging
# ─────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("phonekey")

# ─────────────────────────────────────────────
#  Path Resolution (normal script + PyInstaller)
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
#  CLI Arguments
# ─────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="phonekey",
        description="PhoneKey — Use your phone as a wireless keyboard & Mouse.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python server.py
  python server.py --https
  python server.py --ws-port 9000 --http-port 9001
  python server.py --no-pin
  python server.py --https --no-pin --mouse-speed 2.0
        """,
    )
    parser.add_argument("--ws-port",   type=int, default=DEFAULT_WS_PORT,  metavar="PORT")
    parser.add_argument("--http-port", type=int, default=DEFAULT_HTTP_PORT, metavar="PORT")
    parser.add_argument("--https",     action="store_true", default=False)
    parser.add_argument("--no-pin",    action="store_true", default=False)
    parser.add_argument(
        "--mouse-speed", type=float, default=1.0, metavar="MULTIPLIER",
        help="Mouse speed multiplier (default 1.0)",
    )
    return parser.parse_args()


# ─────────────────────────────────────────────
#  Parse args early (before env check)
# ─────────────────────────────────────────────
ARGS        = parse_args()
MOUSE_SPEED = max(0.1, min(5.0, ARGS.mouse_speed))

# ─────────────────────────────────────────────
#  Windows Ctrl+C Fix — SetConsoleCtrlHandler
#
#  WHY: PyInstaller .exe freezes signal.signal(SIGINT).
#  The Windows console sends WM_CLOSE / CTRL_C_EVENT
#  directly to the process, bypassing Python's signal
#  module entirely when frozen.
#
#  Fix: Register a Win32 ConsoleCtrlHandler via ctypes.
#  This fires for CTRL_C_EVENT (Ctrl+C) and
#  CTRL_CLOSE_EVENT (window X button) in both:
#    - Normal python script
#    - PyInstaller .exe
# ─────────────────────────────────────────────
_stop_event_ref: asyncio.Event | None = None
_loop_ref: asyncio.AbstractEventLoop | None = None


if sys.platform == "win32":
    import ctypes
    import ctypes.wintypes

    CTRL_C_EVENT     = 0
    CTRL_CLOSE_EVENT = 2

    _HandlerRoutine = ctypes.WINFUNCTYPE(ctypes.wintypes.BOOL, ctypes.wintypes.DWORD)

    def _win_ctrl_handler(ctrl_type: int) -> bool:
        if ctrl_type in (CTRL_C_EVENT, CTRL_CLOSE_EVENT):
            logger.info("🛑 Shutting down PhoneKey...")
            if _stop_event_ref and _loop_ref:
                _loop_ref.call_soon_threadsafe(_stop_event_ref.set)
            return True    # True = we handled it — don't pass to default handler
        return False       # False = pass to next handler

    _handler_ref = _HandlerRoutine(_win_ctrl_handler)   # keep reference — prevents GC

    def _register_win_ctrl_handler() -> None:
        """Register the Windows console control handler."""
        ctypes.windll.kernel32.SetConsoleCtrlHandler(_handler_ref, True)
        logger.debug("✅ Windows SetConsoleCtrlHandler registered.")


# ─────────────────────────────────────────────
#  Socket-Based Instance Lock
# ─────────────────────────────────────────────
_lock_socket: socket.socket | None = None


def _acquire_instance_lock() -> None:
    global _lock_socket
    lock_port = ARGS.ws_port + 10000
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
    try:
        sock.bind(("127.0.0.1", lock_port))
        sock.listen(1)
        _lock_socket = sock
    except OSError as exc:
        if exc.errno in (errno.EADDRINUSE, 10048):
            logger.error("═" * 60)
            logger.error("❌  PhoneKey is already running on port %d.", ARGS.ws_port)
            logger.error("    Use a different port: python server.py --ws-port 9000")
            logger.error("═" * 60)
            sys.exit(1)
        raise


def _release_instance_lock() -> None:
    global _lock_socket
    if _lock_socket:
        try:
            _lock_socket.close()
        except Exception:  # noqa: BLE001
            pass
        _lock_socket = None


# ─────────────────────────────────────────────
#  Environment Guard
# ─────────────────────────────────────────────

def _check_environment() -> None:
    system = sys.platform

    if system == "linux":
        has_display   = bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
        has_dev_input = os.path.exists("/dev/input")
        is_cloud      = bool(
            os.environ.get("GOOGLE_CLOUD_PROJECT") or
            os.environ.get("FIREBASE_PROJECT")      or
            os.environ.get("CODESPACES")             or
            os.environ.get("GITPOD_WORKSPACE_ID")
        )
        if is_cloud:
            logger.error("❌  Cloud environment — run on your local laptop.")
            sys.exit(1)
        if not has_display and not has_dev_input:
            logger.error("❌  Headless environment — no display or /dev/input.")
            sys.exit(1)

    backend_map = {
        "win32":  "Win32 SendInput API",
        "darwin": "macOS Quartz CGEvent",
        "linux":  "X11 / Wayland / evdev",
    }
    logger.info("✅ Environment: %s | Backend: %s", system, backend_map.get(system, system))


_check_environment()

# ─────────────────────────────────────────────
#  pynput
# ─────────────────────────────────────────────
try:
    from pynput.keyboard import Controller as KbController, Key
    from pynput.mouse    import Controller as MsController, Button
    keyboard = KbController()
    mouse    = MsController()
    logger.info("✅ pynput keyboard + mouse initialized.")
except Exception as exc:
    logger.error("❌  pynput failed: %s", exc)
    sys.exit(1)

# ─────────────────────────────────────────────
#  pyperclip
# ─────────────────────────────────────────────
try:
    import pyperclip
    CLIPBOARD_AVAILABLE = True
    logger.info("✅ pyperclip clipboard sync available.")
except ImportError:
    CLIPBOARD_AVAILABLE = False
    logger.warning("⚠️  pyperclip not installed — clipboard sync disabled.")

# ─────────────────────────────────────────────
#  qrcode
# ─────────────────────────────────────────────
try:
    import qrcode
    QR_AVAILABLE = True
except ImportError:
    QR_AVAILABLE = False
    logger.warning("⚠️  qrcode not installed — QR code disabled.")

# ─────────────────────────────────────────────
#  Key Maps
# ─────────────────────────────────────────────
SPECIAL_KEY_MAP: dict[str, Key] = {
    "Enter": Key.enter, "Backspace": Key.backspace, "Tab": Key.tab,
    "Escape": Key.esc,  "Delete": Key.delete,
    "ArrowUp": Key.up,  "ArrowDown": Key.down,
    "ArrowLeft": Key.left, "ArrowRight": Key.right,
    "Home": Key.home, "End": Key.end,
    "PageUp": Key.page_up, "PageDown": Key.page_down,
    "CapsLock": Key.caps_lock,
    "Shift": Key.shift, "Control": Key.ctrl, "Alt": Key.alt, "Meta": Key.cmd,
    "ShiftRight": Key.shift_r, "ControlRight": Key.ctrl_r, "AltRight": Key.alt_r,
    "F1": Key.f1,  "F2": Key.f2,  "F3": Key.f3,  "F4": Key.f4,
    "F5": Key.f5,  "F6": Key.f6,  "F7": Key.f7,  "F8": Key.f8,
    "F9": Key.f9,  "F10": Key.f10, "F11": Key.f11, "F12": Key.f12,
}

MOUSE_BUTTON_MAP = {
    "left": Button.left, "right": Button.right, "middle": Button.middle,
}

# ─────────────────────────────────────────────
#  PIN
# ─────────────────────────────────────────────
SESSION_PIN: str | None = (
    None if ARGS.no_pin
    else f"{random.SystemRandom().randint(0, 9999):04d}"
)

# ─────────────────────────────────────────────
#  Connected Device Registry
# ─────────────────────────────────────────────

@dataclass
class ConnectedDevice:
    device_id: str
    name:      str
    websocket: object
    authed:    bool = False
    tab_id:    str | None = None


_device_registry: dict[str, ConnectedDevice] = {}
_tab_id_to_device: dict[str, str] = {}  # tab_id -> device_id for deduplication
_registry_lock = threading.Lock()


def _register_device(d: ConnectedDevice) -> None:
    with _registry_lock:
        _device_registry[d.device_id] = d
        if d.tab_id:
            _tab_id_to_device[d.tab_id] = d.device_id


def _unregister_device(device_id: str) -> None:
    with _registry_lock:
        device = _device_registry.get(device_id)
        if device and device.tab_id:
            _tab_id_to_device.pop(device.tab_id, None)
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
        except Exception:  # noqa: BLE001
            pass

# ─────────────────────────────────────────────
#  Key/Mouse Queue
# ─────────────────────────────────────────────
key_queue: asyncio.Queue = asyncio.Queue()


def _inject_key(data: dict) -> None:
    action, key_value = data.get("action", ""), data.get("key", "")
    if not action or not key_value:
        return
    resolved = SPECIAL_KEY_MAP.get(key_value, key_value)
    try:
        if   action == "keypress": keyboard.press(resolved); keyboard.release(resolved)
        elif action == "keydown":  keyboard.press(resolved)
        elif action == "keyup":    keyboard.release(resolved)
    except Exception as exc:  # noqa: BLE001
        logger.error("Key inject failed '%s': %s", key_value, exc)


def _inject_mouse(data: dict) -> None:
    action = data.get("action", "")
    try:
        if action == "mouse_move":
            dx = int(data.get("dx", 0) * MOUSE_SPEED)
            dy = int(data.get("dy", 0) * MOUSE_SPEED)
            if dx or dy:
                mouse.move(dx, dy)
        elif action == "mouse_click":
            btn = MOUSE_BUTTON_MAP.get(data.get("button", "left"), Button.left)
            mouse.click(btn, 2 if data.get("double") else 1)
        elif action == "mouse_scroll":
            mouse.scroll(data.get("dx", 0), data.get("dy", 0))
    except Exception as exc:  # noqa: BLE001
        logger.error("Mouse inject failed '%s': %s", action, exc)


async def key_worker() -> None:
    while True:
        data   = await key_queue.get()
        action = data.get("action", "")
        if action in ("mouse_move", "mouse_click", "mouse_scroll"):
            _inject_mouse(data)
        else:
            _inject_key(data)
        key_queue.task_done()
        if action not in ("mouse_move", "mouse_scroll"):
            await asyncio.sleep(KEY_INJECT_DELAY)

# ─────────────────────────────────────────────
#  WebSocket Handler
# ─────────────────────────────────────────────

def _try_register_device(device: ConnectedDevice, tab_id: str | None, client_addr: tuple) -> bool:
    """
    Atomically check for duplicate tab_id and register device.
    Returns True if registration successful, False if rejected (duplicate tab).
    """
    with _registry_lock:
        if tab_id and tab_id in _tab_id_to_device:
            existing_device_id = _tab_id_to_device[tab_id]
            logger.warning("Duplicate tab connection rejected: tabId=%s from %s (already connected as device %s)",
                          tab_id, client_addr, existing_device_id[:8])
            return False
        # Register device
        _device_registry[device.device_id] = device
        if device.tab_id:
            _tab_id_to_device[device.tab_id] = device.device_id
    return True


async def ws_handler(websocket) -> None:
    client_addr = websocket.remote_address
    device_id   = str(uuid.uuid4())
    tab_id:     str | None = None
    device:     ConnectedDevice | None = None

    logger.info("📱 Phone connecting: %s", client_addr)

    try:
        # ── Wait for first message (auth or hello) with tab_id ─────────────
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

        # Validate tab_id: must be a non-empty string
        if tab_id is not None:
            if not isinstance(tab_id, str) or not tab_id.strip():
                tab_id = None

        # ── Handle initial action ─────────────────────────────────────────
        action = msg.get("action", "")

        if action == "hello":
            # No-PIN mode: immediate connection
            if SESSION_PIN is not None:
                await websocket.close(1008, "pin_required")
                return
            device = ConnectedDevice(
                device_id=device_id,
                name=f"Device-{device_id[:4]}",
                websocket=websocket,
                authed=True,
                tab_id=tab_id,
            )
            # Atomic check for duplicate tab_id and registration
            if not _try_register_device(device, tab_id, client_addr):
                await websocket.close(1008, "duplicate_tab")
                device = None
                return
            logger.info("📱 Connected (no-PIN): %s (id=%s, tabId=%s)", client_addr, device_id[:8], tab_id)

        elif action == "pin_auth":
            if SESSION_PIN is None:
                await websocket.close(1008, "pin_not_required")
                return

            pin = msg.get("pin", "")
            if pin != SESSION_PIN:
                logger.warning("🔒 Wrong PIN from %s", client_addr)
                await websocket.send(json.dumps(
                    {"type": "auth_fail", "reason": "wrong_pin"}
                ))
                await websocket.close(1008, "wrong_pin")
                return

            device = ConnectedDevice(
                device_id=device_id,
                name=f"Device-{device_id[:4]}",
                websocket=websocket,
                authed=True,
                tab_id=tab_id,
            )
            # Atomic check for duplicate tab_id and registration
            if not _try_register_device(device, tab_id, client_addr):
                await websocket.close(1008, "duplicate_tab")
                device = None
                return
            logger.info("🔓 PIN verified: %s (id=%s, tabId=%s)", client_addr, device_id[:8], tab_id)

        else:
            await websocket.close(1008, "expected_auth")
            return

        # Send auth success
        await websocket.send(json.dumps({"type": "auth_ok", "device_id": device_id}))
        await _broadcast_device_list()

        # ── Message Loop ──────────────────────────────────────────────────
        async for raw in websocket:
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue

            action = msg.get("action", "")

            if action == "device_name":
                if device:
                    device.name = str(msg.get("name", ""))[:32].strip() or device.name
                    logger.info("📛 Device: '%s' (%s)", device.name, client_addr)
                    await _broadcast_device_list()
                continue

            if action == "clipboard_push":
                text = msg.get("text", "")
                if text and CLIPBOARD_AVAILABLE:
                    try:
                        pyperclip.copy(text)
                        await websocket.send(json.dumps({"type": "clipboard_ack"}))
                        logger.info("📋 Clipboard: %d chars from %s", len(text), client_addr)
                    except Exception as exc:  # noqa: BLE001
                        await websocket.send(json.dumps(
                            {"type": "clipboard_ack", "error": str(exc)}
                        ))
                elif not CLIPBOARD_AVAILABLE:
                    await websocket.send(json.dumps(
                        {"type": "clipboard_ack", "error": "pyperclip not installed"}
                    ))
                continue

            if action in (
                "keypress", "keydown", "keyup",
                "mouse_move", "mouse_click", "mouse_scroll",
            ):
                await key_queue.put(msg)

    except websockets.exceptions.ConnectionClosedOK:
        logger.info("📴 Disconnected (clean): %s", client_addr)
    except websockets.exceptions.ConnectionClosedError as exc:
        logger.warning("📴 Disconnected (error): %s | %s", client_addr, exc)
    except Exception as exc:  # noqa: BLE001
        logger.error("WS error from %s: %s", client_addr, exc)
    finally:
        if device:
            _unregister_device(device.device_id)
            await _broadcast_device_list()

# ─────────────────────────────────────────────
#  SSL Certificate — FIXED
#
#  Key fix: SSLContext is built ONCE and passed to
#  BOTH servers. The HTTPServer socket is wrapped
#  BEFORE serve_forever() using the same context.
#  websockets.serve(ssl=ctx) handles WSS internally.
# ─────────────────────────────────────────────

def build_ssl_context(local_ip: str) -> ssl.SSLContext:
    """
    Generates (or reuses) a self-signed certificate and returns
    a properly configured SSLContext for both HTTP and WebSocket servers.

    The certificate CN is set to the local IP address for better compatibility
    when accessing via IP. The certificate is regenerated if the LAN IP changes.
    """
    try:
        from cryptography                              import x509
        from cryptography.x509.oid                    import NameOID
        from cryptography.hazmat.primitives            import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
    except ImportError:
        logger.error("❌  cryptography not installed. Run: pip install cryptography")
        sys.exit(1)

    # ── Reuse existing cert if valid AND contains current local IP ─────────
    if CERT_FILE.exists() and KEY_FILE.exists():
        try:
            cert_pem = CERT_FILE.read_bytes()
            existing = x509.load_pem_x509_certificate(cert_pem)
            remaining = existing.not_valid_after_utc - datetime.now(timezone.utc)

            # Check if current local IP is in SAN
            san_contains_ip = False
            try:
                san_ext = existing.extensions.get_extension_for_class(x509.SubjectAlternativeName)
                for san in san_ext.value:
                    if isinstance(san, x509.IPAddress) and san.value == ipaddress.IPv4Address(local_ip):
                        san_contains_ip = True
                        break
            except x509.ExtensionNotFound:
                pass

            if remaining.days > 7 and san_contains_ip:
                logger.info("♻️  Reusing TLS certificate (%d days left, IP matches).", remaining.days)
                ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
                ctx.load_cert_chain(certfile=str(CERT_FILE), keyfile=str(KEY_FILE))
                return ctx
            else:
                logger.info("🔐 Regenerating TLS certificate (IP changed or expiring soon).")
        except Exception:
            pass  # Fall through to regenerate

    # ── Generate new self-signed cert ─────────────────────────────────────
    logger.info("🔐 Generating self-signed TLS certificate…")

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    # Set CN to local IP for better IP-based access compatibility
    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, local_ip),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "PhoneKey Local"),
    ])

    # Build SAN list — must include the LAN IP for browser cert validation
    san_list: list = [
        x509.DNSName("localhost"),
        x509.DNSName("phonekey.local"),
    ]
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
        .add_extension(
            x509.BasicConstraints(ca=True, path_length=None), critical=True
        )
        .sign(private_key, hashes.SHA256())
    )

    CERT_FILE.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    KEY_FILE.write_bytes(
        private_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )
    logger.info("✅ TLS certificate saved → %s", CERT_FILE.name)

    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(certfile=str(CERT_FILE), keyfile=str(KEY_FILE))
    return ctx

# ─────────────────────────────────────────────
#  HTTP Server
# ─────────────────────────────────────────────

_WS_PORT:    int = DEFAULT_WS_PORT
_USE_HTTPS:  bool = False
_LAUNCH_URL: str = ""  # Deprecated - no longer used (direct QR URL)


class PhoneKeyHTTPHandler(BaseHTTPRequestHandler):
    """
    Custom HTTP handler that:
    - Serves / and /index.html → PhoneKey UI
    - Serves all other static files from CLIENT_DIR
    - Suppresses access logs
    """

    def do_GET(self):
        # ── Static files from CLIENT_DIR ──────────────────────────────────
        path = self.path.split("?")[0].lstrip("/") or "index.html"
        file_path = CLIENT_DIR / path

        if not file_path.exists() or not file_path.is_file():
            # Default to index.html for unknown paths (SPA behaviour)
            file_path = CLIENT_DIR / "index.html"

        if not file_path.exists():
            self.send_error(404, "Not Found")
            return

        # Determine MIME type
        suffix = file_path.suffix.lower()
        mime_map = {
            ".html": "text/html; charset=utf-8",
            ".css":  "text/css",
            ".js":   "application/javascript",
            ".ico":  "image/x-icon",
            ".png":  "image/png",
            ".svg":  "image/svg+xml",
        }
        mime = mime_map.get(suffix, "application/octet-stream")

        content = file_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(content)

    def log_message(self, format, *args):  # noqa: A002
        pass  # Suppress per-request HTTP logs


class PhoneKeyHTTPServer(HTTPServer):
    """Silently drops expected mobile disconnect errors."""
    _IGNORED_WIN = {10053, 10054, 10058}

    def handle_error(self, request, client_address):
        exc_type, exc_val, _ = sys.exc_info()
        if exc_type in (BrokenPipeError, ConnectionResetError):
            return
        if exc_type is OSError and getattr(exc_val, "winerror", None) in self._IGNORED_WIN:
            return
        logger.error("HTTP error from %s: %s: %s", client_address, exc_type, exc_val)


def start_http_server(ssl_ctx: ssl.SSLContext | None) -> None:
    """
    Starts the HTTP(S) server.

    SSL FIX: The socket wrapping must happen AFTER HTTPServer.__init__
    (which calls bind + listen internally) but BEFORE serve_forever().
    We wrap httpd.socket in-place — this is the correct order.
    """
    httpd = PhoneKeyHTTPServer(("0.0.0.0", ARGS.http_port), PhoneKeyHTTPHandler)

    if ssl_ctx is not None:
        # ── Correct SSL wrapping order ────────────────────────────────────
        # httpd.socket is already bound and listening.
        # Wrap it with TLS — replaces the plain socket with an SSL socket.
        # server_side=True → this end presents the certificate.
        httpd.socket = ssl_ctx.wrap_socket(httpd.socket, server_side=True)

    proto = "HTTPS" if ssl_ctx else "HTTP"
    logger.info("🌐 %s server → port %d", proto, ARGS.http_port)
    httpd.serve_forever()

# ─────────────────────────────────────────────
#  Network Utility
# ─────────────────────────────────────────────

def get_local_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"

# ─────────────────────────────────────────────
#  QR Code
# ─────────────────────────────────────────────

def print_qr_code(url: str) -> None:
    """Prints ASCII QR code for the PhoneKey app URL."""
    if not QR_AVAILABLE:
        return
    try:
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_L,
            box_size=1,
            border=1,
        )
        qr.add_data(url)
        qr.make(fit=True)
        print()
        print("  📷  Scan to open PhoneKey:")
        print()
        for row in qr.get_matrix():
            print("  " + "".join("██" if cell else "  " for cell in row))
        print()
    except Exception as exc:  # noqa: BLE001
        logger.debug("QR render failed: %s", exc)

# ─────────────────────────────────────────────
#  Startup Banner
# ─────────────────────────────────────────────

def print_banner(local_ip: str, ssl_ctx: ssl.SSLContext | None) -> None:
    proto      = "https" if ssl_ctx else "http"
    main_url   = f"{proto}://{local_ip}:{ARGS.http_port}"
    os_label   = {"win32": "Windows", "darwin": "macOS", "linux": "Linux"}.get(sys.platform, sys.platform)
    pin_line   = f"PIN: {SESSION_PIN}" if SESSION_PIN else "PIN: Disabled (--no-pin)"

    print()
    print("╔══════════════════════════════════════════════════╗")
    print(f"║         📱  PhoneKey  v{__version__}  💻            ║")
    print("╠══════════════════════════════════════════════════╣")
    print(f"║  OS   : {os_label:<41}║")
    print(f"║  Mode : {('HTTPS/WSS 🔒' if ssl_ctx else 'HTTP/WS  🔓'):<41}║")
    print(f"║  {pin_line:<48}║")
    print("╠══════════════════════════════════════════════════╣")
    print(f"║  URL : {main_url:<39}║")
    print("║                                                  ║")
    print("║  Scan QR code with phone camera                 ║")
    print("║  Phone & laptop must be on the same WiFi        ║")
    print("║  Press Ctrl+C to stop                           ║")
    print("╚══════════════════════════════════════════════════╝")

    if ssl_ctx:
        print()
        print("  ⚠️  HTTPS: Phone will show a certificate warning.")
        print("     Android: Advanced → Proceed to site")
        print("     iOS    : Show Details → Visit this website")

    # QR encodes direct app URL (not a launch page)
    print_qr_code(main_url)

    if SESSION_PIN:
        print(f"  🔐  PIN:  {SESSION_PIN}  ← Enter this on your phone")
        print()

# ─────────────────────────────────────────────
#  Cross-Platform Signal Handling — FIXED
# ─────────────────────────────────────────────

def _setup_signals(stop_event: asyncio.Event, loop: asyncio.AbstractEventLoop) -> None:
    """
    Registers graceful shutdown handlers.

    Windows (.exe + script):
      1. SetConsoleCtrlHandler via ctypes — handles Ctrl+C in frozen .exe
      2. signal.signal(SIGINT) as secondary fallback

    Unix (Linux/macOS):
      loop.add_signal_handler() — asyncio-native, non-blocking
    """
    global _stop_event_ref, _loop_ref
    _stop_event_ref = stop_event
    _loop_ref       = loop

    def _stop(*_) -> None:
        logger.info("🛑 Shutting down PhoneKey...")
        loop.call_soon_threadsafe(stop_event.set)

    if sys.platform == "win32":
        # Primary: Win32 console handler (works in .exe)
        _register_win_ctrl_handler()
        # Secondary: Python signal (works in normal script)
        try:
            signal.signal(signal.SIGINT, _stop)
        except (ValueError, OSError):
            pass  # May fail in frozen exe — that's fine, Win32 handler covers it
    else:
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, _stop)

# ─────────────────────────────────────────────
#  Main
# ─────────────────────────────────────────────

async def main() -> None:
    global _LAUNCH_URL, _WS_PORT, _USE_HTTPS

    local_ip    = get_local_ip()
    ssl_ctx     = build_ssl_context(local_ip) if ARGS.https else None
    proto       = "https" if ssl_ctx else "http"
    ws_proto    = "wss"   if ssl_ctx else "ws"

    _WS_PORT    = ARGS.ws_port
    _USE_HTTPS  = ARGS.https
    _LAUNCH_URL = f"{proto}://{local_ip}:{ARGS.http_port}"

    print_banner(local_ip, ssl_ctx)

    # ── HTTP(S) server — daemon thread ────────────────────────────────────
    threading.Thread(
        target=start_http_server,
        args=(ssl_ctx,),
        daemon=True,
    ).start()

    logger.info("🔌 WebSocket (%s) → port %d", ws_proto.upper(), ARGS.ws_port)

    stop_event = asyncio.Event()
    loop       = asyncio.get_running_loop()

    _setup_signals(stop_event, loop)

    worker = asyncio.create_task(key_worker())

    # ── WebSocket server ──────────────────────────────────────────────────
    # ssl=ssl_ctx: None = plain ws://, SSLContext = wss://
    # This is the correct way — websockets handles TLS internally.
    # Do NOT wrap the WS server socket manually.
    async with websockets.serve(
        ws_handler,
        "0.0.0.0",
        ARGS.ws_port,
        ssl=ssl_ctx,
        ping_interval=WS_PING_INTERVAL,
        ping_timeout=WS_PING_TIMEOUT,
    ):
        logger.info("✅ Server ready — waiting for phone connection...")
        await stop_event.wait()

    worker.cancel()
    try:
        await worker
    except asyncio.CancelledError:
        pass


if __name__ == "__main__":
    _acquire_instance_lock()
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    finally:
        _release_instance_lock()
        logger.info("✅ PhoneKey stopped cleanly.")