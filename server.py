"""
PhoneKey - Lightweight Phone-as-Keyboard Server
Author: You
Version: 1.0.0
License: MIT

Architecture:
    - asyncio WebSocket server (port 8765) handles real-time key injection
    - Threaded HTTP server (port 8080) serves the phone client UI
    - pynput simulates keystrokes natively on the OS level
"""

import asyncio
import json
import logging
import os
import signal
import socket
import sys
import threading
from functools import partial
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

import websockets
from pynput.keyboard import Controller, Key

# ─────────────────────────────────────────────
#  Logging Configuration
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
WS_PORT   = 8765
HTTP_PORT = 8080
CLIENT_DIR = Path(__file__).parent / "client"

# ─────────────────────────────────────────────
#  Special Key Mapping
#  Maps JS KeyboardEvent.key → pynput Key
# ─────────────────────────────────────────────
SPECIAL_KEY_MAP: dict[str, Key] = {
    "Enter":      Key.enter,
    "Backspace":  Key.backspace,
    "Tab":        Key.tab,
    "Escape":     Key.esc,
    "Delete":     Key.delete,
    "ArrowUp":    Key.up,
    "ArrowDown":  Key.down,
    "ArrowLeft":  Key.left,
    "ArrowRight": Key.right,
    "Home":       Key.home,
    "End":        Key.end,
    "PageUp":     Key.page_up,
    "PageDown":   Key.page_down,
    "CapsLock":   Key.caps_lock,
    "Shift":      Key.shift,
    "Control":    Key.ctrl,
    "Alt":        Key.alt,
    "Meta":       Key.cmd,
    "F1":         Key.f1,
    "F2":         Key.f2,
    "F3":         Key.f3,
    "F4":         Key.f4,
    "F5":         Key.f5,
    "F6":         Key.f6,
    "F7":         Key.f7,
    "F8":         Key.f8,
    "F9":         Key.f9,
    "F10":        Key.f10,
    "F11":        Key.f11,
    "F12":        Key.f12,
}

# ─────────────────────────────────────────────
#  Backend Verification (NixOS / Cloud Safe)
# ─────────────────────────────────────────────

def verify_pynput_backend() -> None:
    """
    Verifies that pynput can actually initialize a keyboard controller.
    On NixOS/cloud envs, this catches missing display or /dev/input issues
    early — before the server starts — with a clear error message.
    """
    try:
        from pynput.keyboard import Controller as _TestController
        _ctrl = _TestController()
        logger.info("✅ pynput backend initialized successfully.")
    except Exception as exc:
        logger.error("═" * 55)
        logger.error("❌ pynput failed to initialize a keyboard backend.")
        logger.error("   Error: %s", exc)
        logger.error("")
        logger.error("   You are likely in a headless/cloud environment.")
        logger.error("   Try one of the following:")
        logger.error("   1. Add pkgs.xorg.xorgserver to dev.nix (X11)")
        logger.error("   2. Run: export DISPLAY=:0 before python server.py")
        logger.error("   3. See HEADLESS_MODE note in README")
        logger.error("═" * 55)
        sys.exit(1)

verify_pynput_backend()

# ─────────────────────────────────────────────
#  Keyboard Controller (singleton)
# ─────────────────────────────────────────────
keyboard = Controller()

# ─────────────────────────────────────────────
#  Key Injection Logic
# ─────────────────────────────────────────────

def inject_key(data: dict) -> None:
    """
    Injects a keypress, keydown, or keyup event into the OS.

    Expected payload:
        { "action": "keypress" | "keydown" | "keyup", "key": "<key_value>" }
    """
    action: str = data.get("action", "")
    key_value: str = data.get("key", "")

    if not action or not key_value:
        logger.warning("Malformed payload received: %s", data)
        return

    # Resolve to pynput Key or raw character
    resolved_key = SPECIAL_KEY_MAP.get(key_value, key_value)

    try:
        if action == "keypress":
            # Single press-and-release (used for regular characters)
            keyboard.press(resolved_key)
            keyboard.release(resolved_key)

        elif action == "keydown":
            # Hold down (used for modifier keys: Shift, Ctrl, Alt)
            keyboard.press(resolved_key)

        elif action == "keyup":
            # Release (used for modifier keys)
            keyboard.release(resolved_key)

        else:
            logger.warning("Unknown action: %s", action)

    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to inject key '%s': %s", key_value, exc)


# ─────────────────────────────────────────────
#  WebSocket Handler
# ─────────────────────────────────────────────

async def ws_handler(websocket) -> None:
    """Handles a single WebSocket client connection."""
    client_addr = websocket.remote_address
    logger.info("📱 Phone connected: %s", client_addr)

    try:
        async for raw_message in websocket:
            try:
                data = json.loads(raw_message)
                inject_key(data)
            except json.JSONDecodeError:
                logger.warning("Invalid JSON from %s: %s", client_addr, raw_message)

    except websockets.exceptions.ConnectionClosedOK:
        logger.info("📴 Phone disconnected (clean): %s", client_addr)

    except websockets.exceptions.ConnectionClosedError as exc:
        logger.warning("📴 Phone disconnected (error): %s | %s", client_addr, exc)


# ─────────────────────────────────────────────
#  HTTP Server (serves client/index.html)
# ─────────────────────────────────────────────

class QuietHTTPHandler(SimpleHTTPRequestHandler):
    """SimpleHTTPRequestHandler with suppressed request logs."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(CLIENT_DIR), **kwargs)

    def log_message(self, format, *args):  # noqa: A002
        # Suppress per-request HTTP logs for cleaner terminal output
        pass


def start_http_server() -> None:
    """Starts the HTTP server in a background daemon thread."""
    httpd = HTTPServer(("0.0.0.0", HTTP_PORT), QuietHTTPHandler)
    logger.info("🌐 HTTP server running → http://0.0.0.0:%d", HTTP_PORT)
    httpd.serve_forever()


# ─────────────────────────────────────────────
#  Network Utility
# ─────────────────────────────────────────────

def get_local_ip() -> str:
    """Detects the machine's LAN IP address."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))  # Does not send data; just resolves routing
            return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"


# ─────────────────────────────────────────────
#  Startup Banner
# ─────────────────────────────────────────────

def print_banner(local_ip: str) -> None:
    url = f"http://{local_ip}:{HTTP_PORT}"
    print()
    print("╔══════════════════════════════════════════════╗")
    print("║           📱  PhoneKey  v1.0.0  💻           ║")
    print("╠══════════════════════════════════════════════╣")
    print(f"║  Open on your phone:                         ║")
    print(f"║  👉  {url:<41}║")
    print("║                                              ║")
    print("║  Make sure phone & laptop are on same WiFi  ║")
    print("║  Press Ctrl+C to stop the server            ║")
    print("╚══════════════════════════════════════════════╝")
    print()


# ─────────────────────────────────────────────
#  Main Entry Point
# ─────────────────────────────────────────────

async def main() -> None:
    local_ip = get_local_ip()
    print_banner(local_ip)

    # Start HTTP server in background daemon thread
    http_thread = threading.Thread(target=start_http_server, daemon=True)
    http_thread.start()

    # Start WebSocket server
    logger.info("🔌 WebSocket server running → ws://0.0.0.0:%d", WS_PORT)

    stop_event = asyncio.get_event_loop().create_future()

    # Graceful shutdown on Ctrl+C
    def handle_signal(*_):
        logger.info("🛑 Shutting down PhoneKey...")
        stop_event.set_result(None)

    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, handle_signal)

    async with websockets.serve(ws_handler, "0.0.0.0", WS_PORT):
        await stop_event  # Wait until Ctrl+C


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass  # Handled by signal handler
    finally:
        logger.info("✅ PhoneKey stopped cleanly.")