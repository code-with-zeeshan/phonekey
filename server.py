"""
PhoneKey - Lightweight Phone-as-Keyboard Server
Author: Mohammad Zeeshan
Version: 2.1.0
License: MIT

Architecture:
    - asyncio WebSocket server (port 8765) — real-time key injection
    - Threaded HTTP server   (port 8080)   — serves phone client UI
    - asyncio key Queue + worker           — prevents fast-typing key drops
    - Socket-based instance lock           — prevents duplicate server processes
    - pynput                               — OS-level keystroke injection
    - Cross-platform: Windows / macOS / Linux

IMPORTANT: Run this server on your LOCAL LAPTOP, not a cloud IDE.
"""

# ─────────────────────────────────────────────
#  Version
# ─────────────────────────────────────────────
__version__ = "2.1.0"

import asyncio
import errno
import json
import logging
import os
import signal
import socket
import sys
import threading
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

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
#  Constants
# ─────────────────────────────────────────────
WS_PORT          = 8765
HTTP_PORT        = 8080
LOCK_PORT        = 18765          # Internal socket lock port — not exposed to network
CLIENT_DIR       = Path(__file__).parent / "client"
KEY_INJECT_DELAY = 0.012          # 12ms between keystrokes — prevents Win32 SendInput drops
WS_PING_INTERVAL = 30             # WebSocket keepalive ping interval (seconds)
WS_PING_TIMEOUT  = 60             # WebSocket keepalive pong timeout (seconds)

# ─────────────────────────────────────────────
#  Socket-Based Instance Lock
#
#  WHY SOCKET LOCK instead of PID file:
#  - PID files on Windows: os.kill(pid, 0) doesn't reliably detect
#    living processes across PowerShell sessions and restarts.
#  - Socket lock: OS guarantees only ONE process can bind to a
#    given (host, port) at a time. If bind succeeds → we are the
#    only instance. If bind fails (EADDRINUSE) → another instance
#    is running. Works identically on Windows / macOS / Linux.
#  - The lock socket is bound to 127.0.0.1 (loopback only) so it
#    is invisible to the network and does not consume a public port.
# ─────────────────────────────────────────────
_lock_socket: socket.socket | None = None


def _acquire_instance_lock() -> None:
    """
    Acquires a socket-based instance lock on loopback.
    Exits with a clear message if another PhoneKey instance is already running.
    """
    global _lock_socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
    try:
        sock.bind(("127.0.0.1", LOCK_PORT))
        sock.listen(1)
        _lock_socket = sock
    except OSError as exc:
        if exc.errno in (errno.EADDRINUSE, 10048):  # 10048 = Windows WSAEADDRINUSE
            logger.error("═" * 60)
            logger.error("❌  PhoneKey is already running.")
            logger.error("    Stop the existing instance first:")
            logger.error("    Windows : Press Ctrl+C in its terminal, or:")
            logger.error("              taskkill /IM python.exe /F")
            logger.error("    macOS / Linux : kill $(lsof -ti:8765)")
            logger.error("═" * 60)
            sys.exit(1)
        raise


def _release_instance_lock() -> None:
    """Releases the socket lock on shutdown."""
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
    """
    Validates that this process is running on a local machine
    (not a cloud IDE / headless server) capable of keyboard injection.
    """
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
            logger.error("    See README.md for instructions.")
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
        "linux":  "X11 / Wayland / evdev (auto-detected)",
    }
    logger.info(
        "✅ Environment: %s | Backend: %s",
        system,
        backend_map.get(system, f"unknown ({system})"),
    )


_check_environment()

# ─────────────────────────────────────────────
#  pynput Import
# ─────────────────────────────────────────────
try:
    from pynput.keyboard import Controller, Key
    keyboard = Controller()
    logger.info("✅ pynput keyboard controller initialized.")
except Exception as exc:
    logger.error("═" * 60)
    logger.error("❌  pynput failed: %s", exc)
    logger.error("    Windows : pip install pynput==1.7.6")
    logger.error("    macOS   : System Settings → Privacy → Accessibility")
    logger.error("    Linux   : sudo apt install linux-headers-$(uname -r)")
    logger.error("═" * 60)
    sys.exit(1)

# ─────────────────────────────────────────────
#  Special Key Map  JS key → pynput Key
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
    # ── Left modifiers ──────────────────────
    "Shift":        Key.shift,
    "Control":      Key.ctrl,
    "Alt":          Key.alt,
    "Meta":         Key.cmd,
    # ── Right modifiers ─────────────────────
    "ShiftRight":   Key.shift_r,
    "ControlRight": Key.ctrl_r,
    "AltRight":     Key.alt_r,
    # ── Function keys ───────────────────────
    "F1":  Key.f1,  "F2":  Key.f2,  "F3":  Key.f3,
    "F4":  Key.f4,  "F5":  Key.f5,  "F6":  Key.f6,
    "F7":  Key.f7,  "F8":  Key.f8,  "F9":  Key.f9,
    "F10": Key.f10, "F11": Key.f11, "F12": Key.f12,
}

# ─────────────────────────────────────────────
#  Key Injection Queue
#
#  WHY: Win32 SendInput drops keystrokes when injected
#  faster than ~80/sec. The queue serialises all keystrokes
#  with KEY_INJECT_DELAY spacing — zero drops guaranteed.
# ─────────────────────────────────────────────
key_queue: asyncio.Queue = asyncio.Queue()


def _inject_now(data: dict) -> None:
    """Immediately injects one keystroke via pynput."""
    action: str    = data.get("action", "")
    key_value: str = data.get("key", "")

    if not action or not key_value:
        logger.warning("Malformed payload: %s", data)
        return

    resolved = SPECIAL_KEY_MAP.get(key_value, key_value)

    try:
        if action == "keypress":
            keyboard.press(resolved)
            keyboard.release(resolved)
        elif action == "keydown":
            keyboard.press(resolved)
        elif action == "keyup":
            keyboard.release(resolved)
        else:
            logger.warning("Unknown action: %s", action)
    except Exception as exc:  # noqa: BLE001
        logger.error("Key inject failed '%s': %s", key_value, exc)


async def key_worker() -> None:
    """Drains key_queue with KEY_INJECT_DELAY between each call."""
    while True:
        data = await key_queue.get()
        _inject_now(data)
        key_queue.task_done()
        await asyncio.sleep(KEY_INJECT_DELAY)


# ─────────────────────────────────────────────
#  WebSocket Handler
# ─────────────────────────────────────────────

async def ws_handler(websocket) -> None:
    """Handles one phone browser WebSocket connection."""
    client_addr = websocket.remote_address
    logger.info("📱 Phone connected: %s", client_addr)

    try:
        async for raw in websocket:
            try:
                data = json.loads(raw)
                await key_queue.put(data)
            except json.JSONDecodeError:
                logger.warning("Bad JSON from %s: %s", client_addr, raw)

    except websockets.exceptions.ConnectionClosedOK:
        logger.info("📴 Phone disconnected (clean): %s", client_addr)

    except websockets.exceptions.ConnectionClosedError as exc:
        logger.warning("📴 Phone disconnected (error): %s | %s", client_addr, exc)

    except Exception as exc:  # noqa: BLE001
        logger.error("Unexpected WS error from %s: %s", client_addr, exc)


# ─────────────────────────────────────────────
#  HTTP Server
#
#  PhoneKeyHTTPServer overrides handle_error() to suppress
#  expected / harmless connection errors that occur when the
#  phone browser closes the TCP connection abruptly:
#
#  WinError 10054 : "An existing connection was forcibly closed"
#    → Phone screen turned off, browser tab closed, etc.
#    → Completely normal — not a bug, not a crash.
#
#  BrokenPipeError (Linux/macOS equivalent of WinError 10054)
#    → Same cause, different OS error code.
#
#  Without this override Python's HTTPServer prints a full
#  traceback for every phone screen-off event which clutters
#  the terminal and looks like a crash.
# ─────────────────────────────────────────────

class QuietHTTPHandler(SimpleHTTPRequestHandler):
    """Serves client/ directory, suppresses per-request access logs."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(CLIENT_DIR), **kwargs)

    def log_message(self, format, *args):  # noqa: A002
        pass  # Suppress HTTP access log lines


class PhoneKeyHTTPServer(HTTPServer):
    """
    HTTPServer subclass that silently drops expected mobile
    disconnect errors instead of printing tracebacks.
    """

    # WinError codes that mean "client disconnected abruptly" — not bugs
    _IGNORED_WIN_ERRORS = {
        10053,  # WSAECONNABORTED — connection aborted by software
        10054,  # WSAECONNRESET  — connection forcibly closed by remote
        10058,  # WSAESHUTDOWN   — send/recv after socket shutdown
    }

    def handle_error(self, request, client_address):
        exc_type, exc_value, _ = sys.exc_info()

        # BrokenPipeError — Linux/macOS abrupt disconnect
        if exc_type is BrokenPipeError:
            logger.debug("HTTP: client %s disconnected abruptly (BrokenPipe)", client_address)
            return

        # ConnectionResetError — can wrap WinError 10054
        if exc_type is ConnectionResetError:
            logger.debug("HTTP: client %s reset connection", client_address)
            return

        # OSError with known Windows disconnect codes
        if exc_type is OSError and hasattr(exc_value, "winerror"):
            if exc_value.winerror in self._IGNORED_WIN_ERRORS:
                logger.debug(
                    "HTTP: client %s WinError %d (normal disconnect)",
                    client_address,
                    exc_value.winerror,
                )
                return

        # Anything else is a real unexpected error — log it properly
        logger.error(
            "HTTP: unexpected error from %s: %s: %s",
            client_address,
            exc_type.__name__ if exc_type else "Unknown",
            exc_value,
        )


def start_http_server() -> None:
    """Starts HTTP server in a background daemon thread."""
    httpd = PhoneKeyHTTPServer(("0.0.0.0", HTTP_PORT), QuietHTTPHandler)
    logger.info("🌐 HTTP server running → http://0.0.0.0:%d", HTTP_PORT)
    httpd.serve_forever()


# ─────────────────────────────────────────────
#  Network Utility
# ─────────────────────────────────────────────

def get_local_ip() -> str:
    """Detects LAN IP of this machine (Windows / macOS / Linux)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"


# ─────────────────────────────────────────────
#  Startup Banner
# ─────────────────────────────────────────────

def print_banner(local_ip: str) -> None:
    url = f"http://{local_ip}:{HTTP_PORT}"
    os_labels = {"win32": "Windows", "darwin": "macOS", "linux": "Linux"}
    os_label  = os_labels.get(sys.platform, sys.platform)
    print()
    print("╔══════════════════════════════════════════════╗")
    print(f"║       📱  PhoneKey  v{__version__}  💻          ║")
    print("╠══════════════════════════════════════════════╣")
    print(f"║  OS : {os_label:<39}║")
    print(f"║  Open on your phone:                         ║")
    print(f"║  👉  {url:<41}║")
    print("║                                              ║")
    print("║  Phone & laptop must be on the same WiFi    ║")
    print("║  Press Ctrl+C to stop                       ║")
    print("╚══════════════════════════════════════════════╝")
    print()


# ─────────────────────────────────────────────
#  Cross-Platform Signal Handlers
# ─────────────────────────────────────────────

def _setup_signals(stop_event: asyncio.Event, loop: asyncio.AbstractEventLoop) -> None:
    """
    Windows  → signal.signal() for SIGINT only
    Unix     → loop.add_signal_handler() for SIGINT + SIGTERM
    """
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
    print_banner(local_ip)

    threading.Thread(target=start_http_server, daemon=True).start()
    logger.info("🔌 WebSocket server running → ws://0.0.0.0:%d", WS_PORT)

    stop_event = asyncio.Event()
    loop       = asyncio.get_running_loop()
    _setup_signals(stop_event, loop)

    worker_task = asyncio.create_task(key_worker())

    async with websockets.serve(
        ws_handler,
        "0.0.0.0",
        WS_PORT,
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