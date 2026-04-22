"""
PhoneKey - Lightweight Phone-as-Keyboard Server
Author: Mohammad Zeeshan
Version: 3.0.0
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

__version__ = "3.0.0"

# ─────────────────────────────────────────────
#  Standard Library Imports
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
from datetime import datetime, timedelta, timezone
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

# ─────────────────────────────────────────────
#  Third-Party Imports
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
#  Constants & Defaults
# ─────────────────────────────────────────────
DEFAULT_WS_PORT    = 8765
DEFAULT_HTTP_PORT  = 8080
LOCK_PORT_OFFSET   = 10000             # lock port = ws_port + LOCK_PORT_OFFSET
CLIENT_DIR         = Path(__file__).parent / "client" \
                     if not (getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS")) \
                     else Path(sys._MEIPASS) / "client"
CERT_FILE          = Path(__file__).parent / "phonekey-cert.pem"
KEY_FILE           = Path(__file__).parent / "phonekey-key.pem"
KEY_INJECT_DELAY   = 0.012            # 12ms — prevents Win32 SendInput drops
MOUSE_SPEED        = 1.0              # Mouse movement multiplier
WS_PING_INTERVAL   = 30
WS_PING_TIMEOUT    = 60

# ─────────────────────────────────────────────
#  CLI Argument Parser
# ─────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    """
    Parses command-line arguments.

    Usage examples:
        python server.py
        python server.py --ws-port 9000 --http-port 9001
        python server.py --https
        python server.py --no-pin
        python server.py --https --ws-port 9443 --http-port 9444
    """
    parser = argparse.ArgumentParser(
        prog="phonekey",
        description="PhoneKey — Use your phone as a wireless keyboard for your laptop.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python server.py                          # Default ports, plain HTTP
  python server.py --https                  # HTTPS/WSS (required for iOS Safari)
  python server.py --ws-port 9000           # Custom WebSocket port
  python server.py --no-pin                 # Disable PIN (trust all connections)
  python server.py --https --no-pin         # HTTPS without PIN prompt
        """,
    )

    parser.add_argument(
        "--ws-port",
        type=int,
        default=DEFAULT_WS_PORT,
        metavar="PORT",
        help=f"WebSocket server port (default: {DEFAULT_WS_PORT})",
    )
    parser.add_argument(
        "--http-port",
        type=int,
        default=DEFAULT_HTTP_PORT,
        metavar="PORT",
        help=f"HTTP/HTTPS server port (default: {DEFAULT_HTTP_PORT})",
    )
    parser.add_argument(
        "--https",
        action="store_true",
        default=False,
        help="Enable HTTPS/WSS with auto-generated self-signed certificate. "
             "Required for iOS Safari. Phone will show a certificate warning — tap 'Advanced → Proceed'.",
    )
    parser.add_argument(
        "--no-pin",
        action="store_true",
        default=False,
        help="Disable the 4-digit connection PIN. "
             "Not recommended on shared/public WiFi networks.",
    )
    parser.add_argument(
        "--mouse-speed",
        type=float,
        default=1.0,
        metavar="MULTIPLIER",
        help="Mouse movement speed multiplier (default: 1.0, range: 0.1–5.0)",
    )

    return parser.parse_args()


# ─────────────────────────────────────────────
#  Parse args early (before env check)
# ─────────────────────────────────────────────
ARGS = parse_args()

# Apply mouse speed (clamp to valid range)
MOUSE_SPEED = max(0.1, min(5.0, ARGS.mouse_speed))

# ─────────────────────────────────────────────
#  Socket-Based Instance Lock
# ─────────────────────────────────────────────
_lock_socket: socket.socket | None = None


def _acquire_instance_lock() -> None:
    """
    Binds a loopback socket as a process lock.
    Lock port = ws_port + LOCK_PORT_OFFSET (unique per port config).
    """
    global _lock_socket
    lock_port = ARGS.ws_port + LOCK_PORT_OFFSET
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
            logger.error("    Stop the existing instance, or use a different port:")
            logger.error("    python server.py --ws-port 9000 --http-port 9001")
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
            logger.error("═" * 60)
            logger.error("❌  CLOUD ENVIRONMENT — Cannot inject keystrokes.")
            logger.error("    Run server.py on your LOCAL LAPTOP instead.")
            logger.error("═" * 60)
            sys.exit(1)

        if not has_display and not has_dev_input:
            logger.error("═" * 60)
            logger.error("❌  HEADLESS ENVIRONMENT — No display or /dev/input.")
            logger.error("    pynput needs X11, Wayland, or evdev access.")
            logger.error("═" * 60)
            sys.exit(1)

    backend_map = {
        "win32":  "Win32 SendInput API",
        "darwin": "macOS Quartz CGEvent",
        "linux":  "X11 / Wayland / evdev",
    }
    logger.info(
        "✅ Environment: %s | Backend: %s",
        system,
        backend_map.get(system, system),
    )


_check_environment()

# ─────────────────────────────────────────────
#  pynput Import
# ─────────────────────────────────────────────
try:
    from pynput.keyboard import Controller as KeyboardController, Key
    from pynput.mouse    import Controller as MouseController, Button
    keyboard = KeyboardController()
    mouse    = MouseController()
    logger.info("✅ pynput keyboard + mouse controllers initialized.")
except Exception as exc:
    logger.error("❌  pynput failed: %s", exc)
    sys.exit(1)

# ─────────────────────────────────────────────
#  pyperclip Import (clipboard sync)
# ─────────────────────────────────────────────
try:
    import pyperclip
    CLIPBOARD_AVAILABLE = True
    logger.info("✅ pyperclip clipboard sync available.")
except ImportError:
    CLIPBOARD_AVAILABLE = False
    logger.warning("⚠️  pyperclip not installed — clipboard sync disabled.")
    logger.warning("   Install with: pip install pyperclip")

# ─────────────────────────────────────────────
#  qrcode Import
# ─────────────────────────────────────────────
try:
    import qrcode
    QR_AVAILABLE = True
except ImportError:
    QR_AVAILABLE = False
    logger.warning("⚠️  qrcode not installed — QR code display disabled.")
    logger.warning("   Install with: pip install qrcode")

# ─────────────────────────────────────────────
#  Special Key Map
# ─────────────────────────────────────────────
SPECIAL_KEY_MAP: dict[str, Key] = {
    "Enter":        Key.enter,
    "Backspace":    Key.backspace,
    "Tab":          Key.tab,
    "Escape":       Key.esc,
    "Delete":       Key.delete,
    "ArrowUp":      Key.up,
    "ArrowDown":    Key.down,
    "ArrowLeft":    Key.left,
    "ArrowRight":   Key.right,
    "Home":         Key.home,
    "End":          Key.end,
    "PageUp":       Key.page_up,
    "PageDown":     Key.page_down,
    "CapsLock":     Key.caps_lock,
    "Shift":        Key.shift,
    "Control":      Key.ctrl,
    "Alt":          Key.alt,
    "Meta":         Key.cmd,
    "ShiftRight":   Key.shift_r,
    "ControlRight": Key.ctrl_r,
    "AltRight":     Key.alt_r,
    "F1":  Key.f1,  "F2":  Key.f2,  "F3":  Key.f3,
    "F4":  Key.f4,  "F5":  Key.f5,  "F6":  Key.f6,
    "F7":  Key.f7,  "F8":  Key.f8,  "F9":  Key.f9,
    "F10": Key.f10, "F11": Key.f11, "F12": Key.f12,
}

MOUSE_BUTTON_MAP: dict[str, Button] = {
    "left":   Button.left,
    "right":  Button.right,
    "middle": Button.middle,
}

# ─────────────────────────────────────────────
#  PIN Management
# ─────────────────────────────────────────────

def generate_pin() -> str:
    """Generates a secure 4-digit PIN."""
    return f"{random.SystemRandom().randint(0, 9999):04d}"


# Generate PIN at startup (or disable if --no-pin)
SESSION_PIN: str | None = None if ARGS.no_pin else generate_pin()

# ─────────────────────────────────────────────
#  Connected Device Registry
#
#  Tracks all authenticated WebSocket connections.
#  device_id  : UUID string (per-connection, generated server-side)
#  name       : device name sent by phone (e.g. "Zeeshan's Phone")
#  websocket  : the WebSocket connection object
#  authed     : True once PIN verified (or if PIN disabled)
# ─────────────────────────────────────────────
from dataclasses import dataclass, field

@dataclass
class ConnectedDevice:
    device_id:  str
    name:       str
    websocket:  object
    authed:     bool = False


# Registry: device_id → ConnectedDevice
_device_registry: dict[str, ConnectedDevice] = {}
_registry_lock = threading.Lock()


def _register_device(device: ConnectedDevice) -> None:
    with _registry_lock:
        _device_registry[device.device_id] = device


def _unregister_device(device_id: str) -> None:
    with _registry_lock:
        _device_registry.pop(device_id, None)


def _get_device_list() -> list[dict]:
    """Returns a serializable list of connected authenticated devices."""
    with _registry_lock:
        return [
            {"id": d.device_id, "name": d.name}
            for d in _device_registry.values()
            if d.authed
        ]


async def _broadcast_device_list() -> None:
    """Sends the updated device list to all authenticated clients."""
    payload = json.dumps({"type": "device_list", "devices": _get_device_list()})
    with _registry_lock:
        targets = [d.websocket for d in _device_registry.values() if d.authed]
    for ws in targets:
        try:
            await ws.send(payload)
        except Exception:  # noqa: BLE001
            pass


# ─────────────────────────────────────────────
#  Key Injection Queue
# ─────────────────────────────────────────────
key_queue: asyncio.Queue = asyncio.Queue()


def _inject_key_now(data: dict) -> None:
    """Injects a keyboard event via pynput."""
    action    = data.get("action", "")
    key_value = data.get("key", "")

    if not action or not key_value:
        return

    resolved = SPECIAL_KEY_MAP.get(key_value, key_value)
    try:
        if   action == "keypress": keyboard.press(resolved); keyboard.release(resolved)
        elif action == "keydown":  keyboard.press(resolved)
        elif action == "keyup":    keyboard.release(resolved)
    except Exception as exc:  # noqa: BLE001
        logger.error("Key inject failed '%s': %s", key_value, exc)


def _inject_mouse_now(data: dict) -> None:
    """Handles a mouse action via pynput."""
    action = data.get("action", "")

    try:
        if action == "mouse_move":
            dx = int(data.get("dx", 0) * MOUSE_SPEED)
            dy = int(data.get("dy", 0) * MOUSE_SPEED)
            if dx or dy:
                mouse.move(dx, dy)

        elif action == "mouse_click":
            btn_name = data.get("button", "left")
            btn      = MOUSE_BUTTON_MAP.get(btn_name, Button.left)
            double   = data.get("double", False)
            if double:
                mouse.click(btn, 2)
            else:
                mouse.click(btn, 1)

        elif action == "mouse_scroll":
            dx = data.get("dx", 0)
            dy = data.get("dy", 0)
            mouse.scroll(dx, dy)

    except Exception as exc:  # noqa: BLE001
        logger.error("Mouse action failed '%s': %s", action, exc)


async def key_worker() -> None:
    """Drains key_queue with KEY_INJECT_DELAY between keystrokes."""
    while True:
        data = await key_queue.get()
        action = data.get("action", "")

        if action in ("mouse_move", "mouse_click", "mouse_scroll"):
            _inject_mouse_now(data)
        else:
            _inject_key_now(data)

        key_queue.task_done()
        # Only delay between keyboard events — mouse moves need no delay
        if action not in ("mouse_move", "mouse_scroll"):
            await asyncio.sleep(KEY_INJECT_DELAY)


# ─────────────────────────────────────────────
#  WebSocket Handler
# ─────────────────────────────────────────────

async def ws_handler(websocket) -> None:
    """
    Handles one phone WebSocket connection.

    Protocol flow:
    1. Server assigns device_id and registers the device (unauthed)
    2. If PIN enabled: wait for {action:"pin_auth", pin:"XXXX"}
       - Wrong PIN → send auth_fail, close
       - Correct PIN → send auth_ok, continue
    3. If PIN disabled: immediately authed
    4. Wait for {action:"device_name", name:"..."} to set display name
    5. Normal operation: handle key/mouse/clipboard messages
    6. On disconnect: unregister, broadcast updated device list
    """
    client_addr = websocket.remote_address
    device_id   = str(uuid.uuid4())
    device      = ConnectedDevice(
        device_id=device_id,
        name=f"Device-{device_id[:4]}",   # temporary name until device_name received
        websocket=websocket,
        authed=(SESSION_PIN is None),      # auto-authed if PIN disabled
    )
    _register_device(device)
    logger.info("📱 Phone connecting: %s (id=%s)", client_addr, device_id[:8])

    try:
        # ── Step 1: PIN authentication ─────────────────────────────────────
        if SESSION_PIN is not None:
            try:
                raw = await asyncio.wait_for(websocket.recv(), timeout=30.0)
                msg = json.loads(raw)
            except asyncio.TimeoutError:
                await websocket.close(1008, "auth_timeout")
                return
            except json.JSONDecodeError:
                await websocket.close(1003, "bad_json")
                return

            if msg.get("action") != "pin_auth":
                await websocket.send(json.dumps({
                    "type": "auth_fail", "reason": "expected_pin_auth"
                }))
                await websocket.close(1008, "auth_required")
                return

            if msg.get("pin") != SESSION_PIN:
                logger.warning("🔒 Wrong PIN from %s", client_addr)
                await websocket.send(json.dumps({
                    "type": "auth_fail", "reason": "wrong_pin"
                }))
                await websocket.close(1008, "wrong_pin")
                return

            device.authed = True
            logger.info("🔓 PIN verified: %s", client_addr)

        await websocket.send(json.dumps({"type": "auth_ok", "device_id": device_id}))

        # ── Step 2: Normal message loop ────────────────────────────────────
        await _broadcast_device_list()

        async for raw in websocket:
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                logger.warning("Bad JSON from %s", client_addr)
                continue

            action = msg.get("action", "")

            # Device name registration
            if action == "device_name":
                new_name = str(msg.get("name", ""))[:32].strip() or device.name
                device.name = new_name
                logger.info("📛 Device named: '%s' (%s)", new_name, client_addr)
                await _broadcast_device_list()
                continue

            # Clipboard push: phone clipboard → laptop clipboard
            if action == "clipboard_push":
                text = msg.get("text", "")
                if text and CLIPBOARD_AVAILABLE:
                    try:
                        pyperclip.copy(text)
                        await websocket.send(json.dumps({"type": "clipboard_ack"}))
                        logger.info(
                            "📋 Clipboard updated: %d chars from %s",
                            len(text), client_addr
                        )
                    except Exception as exc:  # noqa: BLE001
                        logger.error("Clipboard sync failed: %s", exc)
                elif not CLIPBOARD_AVAILABLE:
                    await websocket.send(json.dumps({
                        "type": "clipboard_ack",
                        "error": "pyperclip not installed on laptop"
                    }))
                continue

            # All key/mouse events → queue
            if action in (
                "keypress", "keydown", "keyup",
                "mouse_move", "mouse_click", "mouse_scroll",
            ):
                await key_queue.put(msg)

    except websockets.exceptions.ConnectionClosedOK:
        logger.info("📴 Phone disconnected (clean): %s", client_addr)

    except websockets.exceptions.ConnectionClosedError as exc:
        logger.warning("📴 Phone disconnected (error): %s | %s", client_addr, exc)

    except Exception as exc:  # noqa: BLE001
        logger.error("WS error from %s: %s", client_addr, exc)

    finally:
        _unregister_device(device_id)
        await _broadcast_device_list()
        logger.info("📴 Device unregistered: %s", device_id[:8])


# ─────────────────────────────────────────────
#  SSL Certificate (HTTPS/WSS)
# ─────────────────────────────────────────────

def _generate_self_signed_cert(local_ip: str) -> ssl.SSLContext:
    """
    Generates a self-signed TLS certificate for HTTPS/WSS.

    The certificate is valid for 1 year and includes:
    - Common Name: PhoneKey
    - Subject Alternative Names: the laptop's LAN IP + localhost

    WHY SELF-SIGNED:
    A CA-signed cert requires a domain name, which LAN IPs don't have.
    Self-signed works fine — phone just needs to accept it once.
    iOS Safari REQUIRES HTTPS for WebSocket on some network configs.
    """
    try:
        from cryptography                       import x509
        from cryptography.x509.oid             import NameOID
        from cryptography.hazmat.primitives     import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
    except ImportError:
        logger.error("❌  cryptography package not installed.")
        logger.error("    Run: pip install cryptography")
        sys.exit(1)

    # Reuse existing cert if still valid (more than 7 days remaining)
    if CERT_FILE.exists() and KEY_FILE.exists():
        try:
            from cryptography.hazmat.primitives.serialization import load_pem_private_key
            cert_pem = CERT_FILE.read_bytes()
            existing = x509.load_pem_x509_certificate(cert_pem)
            remaining = existing.not_valid_after_utc - datetime.now(timezone.utc)
            if remaining.days > 7:
                logger.info("♻️  Reusing existing TLS certificate (%d days left).", remaining.days)
                return _build_ssl_context()
        except Exception:
            pass  # Regenerate if anything goes wrong

    logger.info("🔐 Generating self-signed TLS certificate…")

    # Generate RSA private key
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
    )

    # Build certificate
    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, "PhoneKey"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "PhoneKey"),
    ])

    san_list = [
        x509.DNSName("localhost"),
        x509.DNSName("phonekey.local"),
    ]
    # Add the LAN IP as a Subject Alternative Name
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
        .add_extension(
            x509.SubjectAlternativeName(san_list),
            critical=False,
        )
        .add_extension(
            x509.BasicConstraints(ca=True, path_length=None),
            critical=True,
        )
        .sign(private_key, hashes.SHA256())
    )

    # Write cert and key to disk
    CERT_FILE.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    KEY_FILE.write_bytes(
        private_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )
    logger.info("✅ TLS certificate saved: %s", CERT_FILE.name)
    return _build_ssl_context()


def _build_ssl_context() -> ssl.SSLContext:
    """Builds an SSL context from the saved certificate files."""
    ctx = ssl.SSLContext(ssl.SSL_PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(certfile=str(CERT_FILE), keyfile=str(KEY_FILE))
    return ctx


# ─────────────────────────────────────────────
#  HTTP Server
# ─────────────────────────────────────────────

class QuietHTTPHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(CLIENT_DIR), **kwargs)

    def log_message(self, format, *args):  # noqa: A002
        pass


class PhoneKeyHTTPServer(HTTPServer):
    """HTTPServer that silently drops expected mobile disconnect errors."""
    _IGNORED_WIN_ERRORS = {10053, 10054, 10058}

    def handle_error(self, request, client_address):
        exc_type, exc_value, _ = sys.exc_info()
        if exc_type in (BrokenPipeError, ConnectionResetError):
            return
        if exc_type is OSError and getattr(exc_value, "winerror", None) in self._IGNORED_WIN_ERRORS:
            return
        logger.error(
            "HTTP error from %s: %s: %s",
            client_address,
            exc_type.__name__ if exc_type else "Unknown",
            exc_value,
        )


def start_http_server(ssl_ctx: ssl.SSLContext | None) -> None:
    httpd = PhoneKeyHTTPServer(("0.0.0.0", ARGS.http_port), QuietHTTPHandler)
    if ssl_ctx:
        httpd.socket = ssl_ctx.wrap_socket(httpd.socket, server_side=True)
    proto = "HTTPS" if ssl_ctx else "HTTP"
    logger.info("🌐 %s server running → port %d", proto, ARGS.http_port)
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
#  QR Code Generator
# ─────────────────────────────────────────────

def print_qr_code(url: str) -> None:
    """
    Prints a compact ASCII QR code to the terminal.
    Scanned by phone camera → opens PhoneKey UI automatically.
    Falls back to text-only if qrcode is not installed.
    """
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
        print("  📷  Scan QR code with your phone camera:")
        print()

        # Print using Unicode block characters for compact display
        matrix = qr.get_matrix()
        for row in matrix:
            line = "  "
            for cell in row:
                line += "██" if cell else "  "
            print(line)
        print()

    except Exception as exc:  # noqa: BLE001
        logger.debug("QR render failed: %s", exc)


# ─────────────────────────────────────────────
#  Startup Banner
# ─────────────────────────────────────────────

def print_banner(local_ip: str, ssl_ctx: ssl.SSLContext | None) -> None:
    proto      = "https" if ssl_ctx else "http"
    ws_proto   = "wss"   if ssl_ctx else "ws"
    url        = f"{proto}://{local_ip}:{ARGS.http_port}"
    os_labels  = {"win32": "Windows", "darwin": "macOS", "linux": "Linux"}
    os_label   = os_labels.get(sys.platform, sys.platform)
    pin_status = f"PIN: {SESSION_PIN}" if SESSION_PIN else "PIN: DISABLED"

    print()
    print("╔══════════════════════════════════════════════════╗")
    print(f"║         📱  PhoneKey  v{__version__}  💻            ║")
    print("╠══════════════════════════════════════════════════╣")
    print(f"║  OS      : {os_label:<39}║")
    print(f"║  Mode    : {('HTTPS/WSS 🔒' if ssl_ctx else 'HTTP/WS  🔓'):<39}║")
    print(f"║  {pin_status:<48}║")
    print("╠══════════════════════════════════════════════════╣")
    print(f"║  Open on your phone:                             ║")
    print(f"║  👉  {url:<45}║")
    print("║                                                  ║")
    print("║  Phone & laptop must be on the same WiFi        ║")
    print("║  Press Ctrl+C to stop                           ║")
    print("╚══════════════════════════════════════════════════╝")

    if ssl_ctx:
        print()
        print("  ⚠️  HTTPS NOTE: Your phone will show a certificate")
        print("     warning on first visit. Tap:")
        print("     'Advanced' → 'Proceed to site' (Chrome/Android)")
        print("     'Show Details' → 'visit this website' (iOS Safari)")

    # Print QR code below the banner
    print_qr_code(url)

    if SESSION_PIN:
        print(f"  🔐  When prompted on phone, enter PIN:  {SESSION_PIN}")
        print()


# ─────────────────────────────────────────────
#  Cross-Platform Signal Handlers
# ─────────────────────────────────────────────

def _setup_signals(stop_event: asyncio.Event, loop: asyncio.AbstractEventLoop) -> None:
    def _stop(*_) -> None:
        logger.info("🛑 Shutting down PhoneKey...")
        loop.call_soon_threadsafe(stop_event.set)

    if sys.platform == "win32":
        signal.signal(signal.SIGINT, _stop)
    else:
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, _stop)


# ─────────────────────────────────────────────
#  Main
# ─────────────────────────────────────────────

async def main() -> None:
    local_ip = get_local_ip()

    # Build SSL context if --https requested
    ssl_ctx: ssl.SSLContext | None = None
    if ARGS.https:
        ssl_ctx = _generate_self_signed_cert(local_ip)

    print_banner(local_ip, ssl_ctx)

    # HTTP/HTTPS server (daemon thread)
    threading.Thread(
        target=start_http_server,
        args=(ssl_ctx,),
        daemon=True,
    ).start()

    proto = "wss" if ssl_ctx else "ws"
    logger.info("🔌 WebSocket (%s) running → port %d", proto.upper(), ARGS.ws_port)

    stop_event = asyncio.Event()
    loop       = asyncio.get_running_loop()
    _setup_signals(stop_event, loop)

    worker_task = asyncio.create_task(key_worker())

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

    worker_task.cancel()
    try:
        await worker_task
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

    # For PyInstaller exe on Windows, ensure Ctrl+C works
    if getattr(sys, "frozen", False) and sys.platform == "win32":
        try:
            import msvcrt
            while True:
                if msvcrt.kbhit() and ord(msvcrt.getch()) == 3:  # Ctrl+C
                    break
        except ImportError:
            pass