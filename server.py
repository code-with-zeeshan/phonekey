"""
PhoneKey — Core Server  (server.py)
Contract : WebSocket handler, HTTP handler, device registry, key/mouse injection,
           SSL management, PIN, QR code, startup banner, and signal setup.
           Receives a parsed argparse.Namespace from system.py; never parses
           argv itself and never acquires the instance lock.
"""

__version__ = "3.2.0"

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
import uuid
from argparse import Namespace
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Dict, Optional, Callable, Awaitable

# ─────────────────────────────────────────────
#  Third-Party
# ─────────────────────────────────────────────
import websockets

# ─────────────────────────────────────────────
#  PhoneKey modules
# ─────────────────────────────────────────────
from logging_setup import get_logger
from config import get_config

logger     = get_logger("phonekey")
http_log   = get_logger("phonekey.http")
ws_log     = get_logger("phonekey.websocket")
input_log  = get_logger("phonekey.input")

# ─────────────────────────────────────────────
#  Tunnel (optional)
# ─────────────────────────────────────────────
TUNNEL_AVAILABLE: bool
try:
    from tunnel_manager import TunnelManager
    TUNNEL_AVAILABLE = True
except ImportError:
    TUNNEL_AVAILABLE = False

# ─────────────────────────────────────────────
#  Path Resolution (script + PyInstaller)
# ─────────────────────────────────────────────
BASE_DIR: Path
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
_WS_PORT: int
_USE_HTTPS: bool
_TUNNEL_URL: Optional[str]
_SESSION_PIN: Optional[str]
_MOUSE_SPEED: float
_WS_URL_OVERRIDE: Optional[str]
_CLIPBOARD_AVAILABLE: bool = False  # Set to True when pyperclip is available
_CLIPBOARD_HISTORY: list[str] = []
_MAX_CLIPBOARD_HISTORY = 10
_FILE_TRANSFER_ENABLED: bool = True
_MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB limit
# Removed executable extensions to prevent arbitrary code upload
_ALLOWED_FILE_EXTENSIONS = {'.txt', '.pdf', '.png', '.jpg', '.jpeg', '.gif', '.json', '.csv', '.md'}
# Clipboard synchronization direction control
_CLIPBOARD_SYNC_DIRECTION: str = "phone_to_laptop"  # phone_to_laptop, laptop_to_phone, bidirectional
_CLIPBOARD_LAST_LAPTOP_CONTENT: str = ""  # Track last known laptop clipboard content
_CLIPBOARD_LAPTOP_CHECK_TASK: Optional[asyncio.Task] = None  # Background task for laptop→phone sync
_CLIPBOARD_LAPTOP_READ_INTERVAL: float = 2.0  # Seconds between clipboard checks
# Keyboard layout support
_KEYBOARD_LAYOUT: str = "qwerty"  # qwerty, qwertz, azerty, dvorak, colemak
# Reconnection grace period settings (for exponential backoff and session resumption)
_RECONNECT_GRACE_SECONDS: int = 30  # How long to keep auth state after disconnect
_RECONNECT_BASE_DELAY_MS: int = 1000  # Initial reconnection delay (matches client)
_RECONNECT_MAX_DELAY_MS: int = 16000  # Maximum reconnection delay (matches client)
_RECONNECT_BACKOFF_FACTOR: float = 2.0  # Exponential backoff factor (matches client)

# Connection history and favorites
_CONNECTION_HISTORY: list[dict] = []  # List of past connections
_FAVORITE_DEVICES: set[str] = set()   # Set of favorite device IDs
_MAX_CONNECTION_HISTORY = 50          # Maximum history entries to keep
_CONNECTIONS_FILE: Optional[Path] = None  # File to store connections data
_LAST_QR_CODE_DATA: Optional[dict] = None  # Last generated QR code data for persistence
# Locks for shared mutable state
_CONNECTION_HISTORY_LOCK = threading.Lock()
_FAVORITE_DEVICES_LOCK = threading.Lock()
_CLIPBOARD_HISTORY_LOCK = threading.Lock()  # Use threading.Lock for sync contexts
_WEBSOCKETS_LOCK = asyncio.Lock()

# WebSocket connection tracking for broadcasts
_WEBSOCKETS: set[websockets.WebSocketServerProtocol] = set()

# Clipboard history persistence
_CLIPBOARD_HISTORY_FILE: Optional[Path] = None  # File to store clipboard history
_CLIPBOARD_HISTORY_PERSISTENCE_ENABLED: bool = True  # Enable/disable persistent storage of clipboard history

# Gesture-based command support
_GESTURE_SENSITIVITY: float = 0.5          # Gesture detection sensitivity
_GESTURE_SWIPE_THRESHOLD: int = 50         # Minimum swipe distance in pixels
_GESTURE_TAP_THRESHOLD: int = 300          # Maximum time in milliseconds for a tap
_LAST_TOUCH_POSITIONS: dict[str, dict] = {}  # Track touch positions per device
_GESTURE_COMMANDS: dict[str, str] = {      # Gesture to command mapping
    "swipe_left": "alt_tab_left",      # Three-finger swipe left
    "swipe_right": "alt_tab_right",    # Three-finger swipe right
    "swipe_up": "show_desktop",        # Three-finger swipe up
    "swipe_down": "open_task_view",    # Three-finger swipe down
    "pinch_in": "zoom_out",            # Pinch in gesture
    "pinch_out": "zoom_in",            # Pinch out gesture
    "double_tap": "lock_screen",       # Double tap gesture
}

# Input controllers (initialized in main())
keyboard = None
mouse = None

# Macro recording and playback support
_MACROS: dict[str, dict[str, list]] = {}   # Stored macros {device_id: {name: [actions]}}
_MACRO_RECORDING: dict[str, bool] = {}     # Track recording state per device
_CURRENT_MACRO: dict[str, list] = {}       # Current macro being recorded per device
_MAX_MACROS = 10                           # Maximum number of macros to store
_MAX_MACRO_LENGTH = 1000                   # Maximum actions per macro
_MACROS_FILE: Optional[Path] = None        # File to store macros
_MACROS_LOCK = threading.Lock()              # Lock for macro operations

# ─────────────────────────────────────────────
#  Clipboard & XSS sanitization
# ─────────────────────────────────────────────
def _sanitize_clipboard_text(text: str) -> str:
    """Sanitize clipboard text to prevent XSS when rendered in web UI.
    Escapes HTML special characters to prevent script injection."""
    if not text:
        return text
    return (
        text.replace("&", "&amp;")    # ✅ must be first to avoid double-escaping
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
            .replace("'", "&#x27;")
            .replace("/", "&#x2F;")
    )

# ─────────────────────────────────────────────
#  JSON schema validation
# ─────────────────────────────────────────────
def _validate_macro_actions(actions: list) -> bool:
    """Validate macro actions have required fields and safe types."""
    if not isinstance(actions, list):
        return False
    for action in actions:
        if not isinstance(action, dict):
            return False
        if "action" not in action or not isinstance(action["action"], str):
            return False
        if action["action"] not in ("keypress", "keydown", "keyup", "mouse_move", "mouse_click", "mouse_scroll"):
            return False
        if "device_id" in action and not isinstance(action.get("device_id"), str):
            return False
    return True


def _validate_clipboard_history(history: list) -> bool:
    """Validate clipboard history contains only strings."""
    if not isinstance(history, list):
        return False
    return all(isinstance(item, str) for item in history)


def _validate_connection_history(history: list) -> bool:
    """Validate connection history entries have required fields."""
    if not isinstance(history, list):
        return False
    for entry in history:
        if not isinstance(entry, dict):
            return False
        if "id" not in entry or not isinstance(entry["id"], str):
            return False
        if "name" not in entry or not isinstance(entry["name"], str):
            return False
        if "timestamp" not in entry or not isinstance(entry["timestamp"], str):
            return False
    return True


# ─────────────────────────────────────────────
#  Clipboard monitoring for laptop→phone sync
# ─────────────────────────────────────────────
async def _monitor_laptop_clipboard() -> None:
    """Monitor laptop clipboard for changes and push to phone when direction allows."""
    global _CLIPBOARD_HISTORY, _CLIPBOARD_LAST_LAPTOP_CONTENT

    if not _CLIPBOARD_AVAILABLE:
        return
     
    try:
        import pyperclip
    except ImportError:
        logger.warning("⚠️  pyperclip not available for laptop clipboard monitoring")
        return
     
    logger.info("🔄 Starting laptop clipboard monitoring loop")
    while True:
        try:
            # Check if we should monitor based on direction
            if _CLIPBOARD_SYNC_DIRECTION in ["laptop_to_phone", "bidirectional"]:
                # Get current laptop clipboard content
                current_content = pyperclip.paste()
                 
                # Only act if content has changed and is not empty
                if (current_content and
                    current_content != _CLIPBOARD_LAST_LAPTOP_CONTENT and
                    current_content.strip()):  # Ignore whitespace-only changes
                     
                    logger.info("📋 Detected laptop clipboard change: %d chars", len(current_content))
                    _CLIPBOARD_LAST_LAPTOP_CONTENT = current_content
                     
                    # Add to clipboard history (avoid duplicates)
                    with _CLIPBOARD_HISTORY_LOCK:
                        if current_content not in _CLIPBOARD_HISTORY:
                            _CLIPBOARD_HISTORY.insert(0, current_content)
                            # Keep only the most recent items
                            if len(_CLIPBOARD_HISTORY) > _MAX_CLIPBOARD_HISTORY:
                                _CLIPBOARD_HISTORY = _CLIPBOARD_HISTORY[:_MAX_CLIPBOARD_HISTORY]
                     
                    # Notify all connected devices about the clipboard update from laptop
                    # This enables laptop→phone clipboard sync
                    laptop_clipboard_msg = json.dumps({
                        "type": "laptop_clipboard_update",
                        "text": current_content
                    })
                    # Send to all connected authenticated devices
                    with _registry_lock:
                        for device in _device_registry.values():
                            if device.authed:
                                try:
                                    await device.websocket.send(laptop_clipboard_msg)
                                except:
                                    pass  # Ignore errors sending to individual clients
                     
            # Wait before next check
            await asyncio.sleep(_CLIPBOARD_LAPTOP_READ_INTERVAL)
              
        except asyncio.CancelledError:
            logger.info("🔄 Laptop clipboard monitoring cancelled")
            break
        except Exception as exc:
            logger.error("❌ Error in laptop clipboard monitoring: %s", exc)
            await asyncio.sleep(5)  # Wait longer on error


# ─────────────────────────────────────────────
#  Clipboard history persistence
# ─────────────────────────────────────────────
def _load_clipboard_history() -> None:
    """Load clipboard history from persistent storage."""
    global _CLIPBOARD_HISTORY, _CLIPBOARD_HISTORY_FILE
    
    if _CLIPBOARD_HISTORY_FILE is None:
        # Default to a clipboard_history.json file in the base directory
        _CLIPBOARD_HISTORY_FILE = BASE_DIR / "clipboard_history.json"
    
    try:
        if _CLIPBOARD_HISTORY_FILE.exists():
            with open(_CLIPBOARD_HISTORY_FILE, 'r') as f:
                data = json.load(f)
                with _CLIPBOARD_HISTORY_LOCK:
                    _CLIPBOARD_HISTORY = data.get("history", [])
                logger.info("📋 Loaded clipboard history (%d entries)", len(_CLIPBOARD_HISTORY))
        else:
            logger.info("📋 No existing clipboard history file found, starting fresh")
            with _CLIPBOARD_HISTORY_LOCK:
                _CLIPBOARD_HISTORY = []
    except Exception as exc:
        logger.error("❌ Failed to load clipboard history: %s", exc)
        with _CLIPBOARD_HISTORY_LOCK:
            _CLIPBOARD_HISTORY = []


def _save_clipboard_history() -> None:
    """Save clipboard history to persistent storage."""
    global _CLIPBOARD_HISTORY, _CLIPBOARD_HISTORY_FILE
    
    if not _CLIPBOARD_HISTORY_PERSISTENCE_ENABLED:
        return
        
    if _CLIPBOARD_HISTORY_FILE is None:
        _CLIPBOARD_HISTORY_FILE = BASE_DIR / "clipboard_history.json"
    
    try:
        with _CLIPBOARD_HISTORY_LOCK:
            history_copy = list(_CLIPBOARD_HISTORY)
        data = {
            "history": history_copy
        }
        with open(_CLIPBOARD_HISTORY_FILE, 'w') as f:
            json.dump(data, f, indent=2)
        logger.debug("💾 Saved clipboard history (%d entries)", len(history_copy))
    except Exception as exc:
        logger.error("❌ Failed to save clipboard history: %s", exc)


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

# Keyboard layout mappings
# These map the visual key labels to the actual keycodes that should be sent
_KEYBOARD_LAYOUTS = {
    "qwerty": {
        # Standard US QWERTY layout - direct mapping
    },
    "qwertz": {
        # German/Swiss layout - swap Y and Z
        "y": "z",
        "z": "y",
        "Y": "Z",
        "Z": "Y",
    },
    "azerty": {
        # French layout
        "a": "q",
        "q": "a",
        "z": "w",
        "w": "z",
        "A": "Q",
        "Q": "A",
        "Z": "W",
        "W": "Z",
        "m": ";",
        "M": ":",
        ";": "m",
        ":": "M",
    },
    "dvorak": {
        # Dvorak layout - approximate mapping for common keys
        "'": "[",
        "[": "'",
        '"': "{",
        "{": '"',
        ",": "p",
        "p": ",",
        "?": ".",
        ".": "?",
        "<": ">",
        ">": "<",
        "a": "p",
        "p": "a",
        "A": "P",
        "P": "A",
        "o": "e",
        "e": "o",
        "O": "E",
        "E": "O",
        "e": "o",
        "o": "e",
        "E": "O",
        "O": "E",
        "u": "h",
        "h": "u",
        "U": "H",
        "H": "U",
        "i": "t",
        "t": "i",
        "I": "T",
        "T": "I",
        "d": "r",
        "r": "d",
        "D": "R",
        "R": "D",
        "c": "g",
        "g": "c",
        "C": "G",
        "G": "C",
        "f": "d",
        "d": "f",
        "F": "D",
        "D": "F",
        "k": "x",
        "x": "k",
        "K": "X",
        "X": "K",
        "l": "b",
        "b": "l",
        "L": "B",
        "B": "L",
        ";": "q",
        "q": ";",
        ":": "Q",
        "Q": ":",
    },
    "colemak": {
        # Colemak layout - approximate mapping
        "s": "t",
        "t": "s",
        "S": "T",
        "T": "S",
        "d": "h",
        "h": "d",
        "D": "H",
        "H": "D",
        "g": "n",
        "n": "g",
        "G": "N",
        "N": "G",
        "l": "e",
        "e": "l",
        "L": "E",
        "E": "L",
    }
}

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


# ─────────────────────────────────────────────
#  Reconnection Grace Period Support
# ─────────────────────────────────────────────
# When a device disconnects, we keep its auth state for a short grace period.
# This allows the client's exponential backoff reconnection to resume the
# session without forcing the user to re-authenticate.

_GRACE_PERIOD_SECONDS = 30  # How long to keep auth state after disconnect
_DISCONNECTED_DEVICE_STATE: dict[str, dict] = {}  # device_id → {name, tab_id, authed, expires_at}
_GRACE_LOCK = threading.Lock()

# ─────────────────────────────────────────────
#  Rate Limiting
# ─────────────────────────────────────────────
# Prevent abuse by limiting connection attempts from the same IP address.
_CONNECTION_ATTEMPTS: dict[str, list[float]] = {}  # ip → [timestamps]
_CONNECTION_RATE_LOCK = threading.Lock()
_MAX_CONNECTION_ATTEMPTS = 10  # Max attempts per window
_CONNECTION_RATE_WINDOW = 60  # Time window in seconds


def _check_rate_limit(client_addr: tuple[str, int]) -> bool:
    """Check if client IP has exceeded connection rate limit. Returns True if allowed."""
    ip = client_addr[0]
    now = datetime.now(timezone.utc).timestamp()
    
    with _CONNECTION_RATE_LOCK:
        if ip not in _CONNECTION_ATTEMPTS:
            _CONNECTION_ATTEMPTS[ip] = []
        
        # Remove old attempts outside the window
        _CONNECTION_ATTEMPTS[ip] = [
            ts for ts in _CONNECTION_ATTEMPTS[ip]
            if now - ts < _CONNECTION_RATE_WINDOW
        ]
        
        # Check if limit exceeded
        if len(_CONNECTION_ATTEMPTS[ip]) >= _MAX_CONNECTION_ATTEMPTS:
            return False
        
        # Record this attempt
        _CONNECTION_ATTEMPTS[ip].append(now)
        return True


def _store_disconnected_state(device_id: str, name: str, tab_id: str | None, authed: bool) -> None:
    """Store device auth state temporarily after disconnect to allow reconnection."""
    with _GRACE_LOCK:
        _DISCONNECTED_DEVICE_STATE[device_id] = {
            "name": name,
            "tab_id": tab_id,
            "authed": authed,
            "expires_at": datetime.now(timezone.utc).timestamp() + _GRACE_PERIOD_SECONDS,
        }


def _get_and_consume_disconnected_state(device_id: str) -> dict | None:
    """Retrieve and remove stored state for a reconnecting device. Returns None if expired or not found."""
    with _GRACE_LOCK:
        state = _DISCONNECTED_DEVICE_STATE.pop(device_id, None)
        if state is None:
            return None
        if datetime.now(timezone.utc).timestamp() > state["expires_at"]:
            return None
        return state


def _cleanup_expired_grace_states() -> None:
    """Remove expired entries from the grace period cache."""
    now = datetime.now(timezone.utc).timestamp()
    with _GRACE_LOCK:
        expired = [k for k, v in _DISCONNECTED_DEVICE_STATE.items() if v["expires_at"] <= now]
        for k in expired:
            _DISCONNECTED_DEVICE_STATE.pop(k, None)


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
    # Store state for graceful reconnection
    if dev:
        _store_disconnected_state(dev.device_id, dev.name, dev.tab_id, dev.authed)


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


# Connection history, favorites, and QR code persistence management
def _load_connections_data() -> None:
    """Load connection history, favorites, and QR code data from persistent storage."""
    global _CONNECTION_HISTORY, _FAVORITE_DEVICES, _CONNECTIONS_FILE, _LAST_QR_CODE_DATA
    
    if _CONNECTIONS_FILE is None:
        # Default to a connections.json file in the base directory
        _CONNECTIONS_FILE = BASE_DIR / "connections.json"
    
    try:
        if _CONNECTIONS_FILE.exists():
            with open(_CONNECTIONS_FILE, 'r') as f:
                data = json.load(f)
                with _CONNECTION_HISTORY_LOCK:
                    _CONNECTION_HISTORY = data.get("history", [])
                with _FAVORITE_DEVICES_LOCK:
                    _FAVORITE_DEVICES = set(data.get("favorites", []))
                _LAST_QR_CODE_DATA = data.get("qr_code_data")
                logger.info("📋 Loaded connection history (%d entries), favorites (%d devices), and QR code data",
                           len(_CONNECTION_HISTORY), len(_FAVORITE_DEVICES))
        else:
            logger.info("📋 No existing connections file found, starting fresh")
    except Exception as exc:
        logger.error("❌ Failed to load connections data: %s", exc)
        with _CONNECTION_HISTORY_LOCK:
            _CONNECTION_HISTORY = []
        with _FAVORITE_DEVICES_LOCK:
            _FAVORITE_DEVICES = set()
        _LAST_QR_CODE_DATA = None


def _save_connections_data() -> None:
    """Save connection history, favorites, and QR code data to persistent storage.
    
    NOTE: This function is called from within _CONNECTION_HISTORY_LOCK and
    _FAVORITE_DEVICES_LOCK contexts. Do NOT acquire locks here to avoid deadlock.
    Callers must ensure data consistency.
    """
    global _CONNECTION_HISTORY, _FAVORITE_DEVICES, _CONNECTIONS_FILE, _LAST_QR_CODE_DATA
    
    if _CONNECTIONS_FILE is None:
        _CONNECTIONS_FILE = BASE_DIR / "connections.json"
    
    try:
        # NOTE: Locks are NOT acquired here - callers must hold them
        history_copy = list(_CONNECTION_HISTORY)
        favorites_copy = list(_FAVORITE_DEVICES)
        data = {
            "history": history_copy,
            "favorites": favorites_copy,
            "qr_code_data": _LAST_QR_CODE_DATA
        }
        with open(_CONNECTIONS_FILE, 'w') as f:
            json.dump(data, f, indent=2)
        logger.debug("💾 Saved connection history, favorites, and QR code data")
    except Exception as exc:
        logger.error("❌ Failed to save connections data: %s", exc)


def _add_to_connection_history(device_id: str, device_name: str) -> None:
    """Add a device to connection history."""
    global _CONNECTION_HISTORY, _MAX_CONNECTION_HISTORY
    
    with _CONNECTION_HISTORY_LOCK:
        # Check if device already in history (update timestamp and move to front)
        for entry in _CONNECTION_HISTORY:
            if entry["id"] == device_id:
                entry["name"] = device_name  # Update name in case it changed
                entry["timestamp"] = datetime.now(timezone.utc).isoformat()
                _CONNECTION_HISTORY.remove(entry)
                break
        else:
            # New device entry
            _CONNECTION_HISTORY.append({
                "id": device_id,
                "name": device_name,
                "timestamp": datetime.now(timezone.utc).isoformat()
            })
        
        # Move to front of list (most recent first)
        _CONNECTION_HISTORY.reverse()
        
        # Trim to max size
        if len(_CONNECTION_HISTORY) > _MAX_CONNECTION_HISTORY:
            _CONNECTION_HISTORY = _CONNECTION_HISTORY[:_MAX_CONNECTION_HISTORY]
        
        # Reverse back to maintain chronological order (newest first)
        _CONNECTION_HISTORY.reverse()
        
        # Save to persistent storage
        _save_connections_data()


def _toggle_favorite(device_id: str) -> bool:
    """Toggle a device as favorite. Returns True if now favorite, False if not."""
    global _FAVORITE_DEVICES
    
    with _FAVORITE_DEVICES_LOCK:
        if device_id in _FAVORITE_DEVICES:
            _FAVORITE_DEVICES.remove(device_id)
            is_favorite = False
        else:
            _FAVORITE_DEVICES.add(device_id)
            is_favorite = True
        
        # Save to persistent storage
        _save_connections_data()
    
    return is_favorite


def _get_connection_history() -> list[dict]:
    """Get connection history, sorted by most recent first."""
    with _CONNECTION_HISTORY_LOCK:
        return list(_CONNECTION_HISTORY)


def _get_favorite_devices() -> list[str]:
    """Get list of favorite device IDs."""
    with _FAVORITE_DEVICES_LOCK:
        return list(_FAVORITE_DEVICES)


def _is_favorite(device_id: str) -> bool:
    """Check if a device is marked as favorite."""
    with _FAVORITE_DEVICES_LOCK:
        return device_id in _FAVORITE_DEVICES

# ─────────────────────────────────────────────
#  Key / Mouse Queue
# ─────────────────────────────────────────────
key_queue: asyncio.Queue[Dict[str, Any]] = asyncio.Queue()


def _inject_key(data: Dict[str, Any], keyboard: Any, SPECIAL_KEY_MAP: Dict[str, Any]) -> None:
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


def _inject_mouse(data: Dict[str, Any], mouse: Any, MOUSE_BUTTON_MAP: Dict[str, Any]) -> None:
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


def _make_key_worker(keyboard: Any, mouse: Any, SPECIAL_KEY_MAP: Dict[str, Any], MOUSE_BUTTON_MAP: Dict[str, Any]) -> Callable[[], Awaitable[None]]:
    async def key_worker() -> None:
        while True:
            data   = await key_queue.get()
            action = data.get("action", "")
            device_id = data.get("device_id", "")
            
            # Handle macro recording
            if device_id and action in ("keypress", "keydown", "keyup", "mouse_move", "mouse_click", "mouse_scroll"):
                _add_macro_action(device_id, data)
            
            # Handle input injection
            if action in ("mouse_move", "mouse_click", "mouse_scroll"):
                _inject_mouse(data, mouse, MOUSE_BUTTON_MAP)
            else:
                _inject_key(data, keyboard, SPECIAL_KEY_MAP)
            key_queue.task_done()
            if action not in ("mouse_move", "mouse_scroll"):
                await asyncio.sleep(KEY_INJECT_DELAY)
    return key_worker


# Gesture-based command processing
async def _process_gesture_command(device_id: str, command: str, msg: Dict[str, Any]) -> None:
    """Process gesture commands and convert them to appropriate input actions."""
    from pynput.keyboard import Key
    from pynput.mouse import Button
    
    # Get the device's websocket for feedback
    device = _device_registry.get(device_id)
    if not device or not device.authed:
        return
    
    try:
        # Map gesture commands to actual key/mouse actions
        if command == "alt_tab_left":
            # Alt+Tab left (previous window)
            keyboard.press(Key.alt)
            keyboard.press(Key.tab)
            keyboard.release(Key.tab)
            keyboard.release(Key.alt)
        elif command == "alt_tab_right":
            # Alt+Shift+Tab right (next window)
            keyboard.press(Key.alt)
            keyboard.press(Key.shift)
            keyboard.press(Key.tab)
            keyboard.release(Key.tab)
            keyboard.release(Key.shift)
            keyboard.release(Key.alt)
        elif command == "show_desktop":
            # Win+D (show desktop)
            keyboard.press(Key.cmd)
            keyboard.press('d')
            keyboard.release('d')
            keyboard.release(Key.cmd)
        elif command == "open_task_view":
            # Win+Tab (task view)
            keyboard.press(Key.cmd)
            keyboard.press(Key.tab)
            keyboard.release(Key.tab)
            keyboard.release(Key.cmd)
        elif command == "zoom_in":
            # Ctrl+Plus (zoom in)
            keyboard.press(Key.ctrl)
            keyboard.press('+')
            keyboard.release('+')
            keyboard.release(Key.ctrl)
        elif command == "zoom_out":
            # Ctrl+Minus (zoom out)
            keyboard.press(Key.ctrl)
            keyboard.press('-')
            keyboard.release('-')
            keyboard.release(Key.ctrl)
        elif command == "lock_screen":
            # Win+L (lock screen)
            keyboard.press(Key.cmd)
            keyboard.press(Key.l)
            keyboard.release(Key.l)
            keyboard.release(Key.cmd)
        else:
            # Unknown command, log it
            logger.warning("⚠️  Unknown gesture command: %s", command)
            return
            
        # Small delay to ensure the action is registered
        await asyncio.sleep(KEY_INJECT_DELAY)

    except Exception as exc:
         input_log.error("❌ Gesture command failed '%s': %s", command, exc)

# ─────────────────────────────────────────────
#  Macro recording and playback support
# ─────────────────────────────────────────────
def _load_macros() -> None:
    """Load macros from persistent storage."""
    global _MACROS, _MACROS_FILE
    
    if _MACROS_FILE is None:
        # Default to a macros.json file in the base directory
        _MACROS_FILE = BASE_DIR / "macros.json"
    
    try:
        if _MACROS_FILE.exists():
            with open(_MACROS_FILE, 'r') as f:
                data = json.load(f)
                loaded = data.get("macros", {})
                # Backward compatibility: if top-level keys don't look like device IDs,
                # migrate to per-device format under a default device
                if loaded and not any(
                    len(k) == 36 and k.count("-") == 4  # UUID-like device ID
                    for k in loaded.keys()
                ):
                    # Old format: {"macro_name": [actions]} -> migrate to per-device
                    _MACROS = {"legacy_device": loaded}
                    logger.info("📋 Migrated %d macros from legacy format to per-device storage", len(loaded))
                else:
                    _MACROS = loaded
                logger.info("📋 Loaded macros for %d device(s) from persistent storage", len(_MACROS))
        else:
            logger.info("📋 No existing macros file found, starting fresh")
    except Exception as exc:
        logger.error("❌ Failed to load macros data: %s", exc)
        _MACROS = {}


def _save_macros() -> None:
    """Save macros to persistent storage."""
    global _MACROS, _MACROS_FILE
    
    if _MACROS_FILE is None:
        _MACROS_FILE = BASE_DIR / "macros.json"
    
    try:
        data = {
            "macros": _MACROS
        }
        with open(_MACROS_FILE, 'w') as f:
            json.dump(data, f, indent=2)
        logger.debug("💾 Saved %d macros to persistent storage", len(_MACROS))
    except Exception as exc:
        logger.error("❌ Failed to save macros data: %s", exc)


def _start_macro_recording(device_id: str, macro_name: str) -> bool:
    """Start recording a new macro."""
    global _MACRO_RECORDING, _CURRENT_MACRO, _MAX_MACROS, _MAX_MACRO_LENGTH
    
    # Check if we've reached the maximum number of macros
    with _MACROS_LOCK:
        if len(_MACROS) >= _MAX_MACROS and macro_name not in _MACROS:
            return False
    
    # Initialize recording state
    _MACRO_RECORDING[device_id] = True
    _CURRENT_MACRO[device_id] = []
    return True


def _stop_macro_recording(device_id: str, macro_name: str) -> bool:
    """Stop recording a macro and save it."""
    global _MACRO_RECORDING, _CURRENT_MACRO, _MACROS
    
    if device_id not in _MACRO_RECORDING or not _MACRO_RECORDING[device_id]:
        return False
    
    # Stop recording
    _MACRO_RECORDING[device_id] = False
    
    # Save the recorded macro if it has actions
    if device_id in _CURRENT_MACRO and _CURRENT_MACRO[device_id]:
        # Check macro length limit
        if len(_CURRENT_MACRO[device_id]) <= _MAX_MACRO_LENGTH:
            with _MACROS_LOCK:
                if device_id not in _MACROS:
                    _MACROS[device_id] = {}
                _MACROS[device_id][macro_name] = _CURRENT_MACRO[device_id].copy()
                _save_macros()
            logger.info("📼 Saved macro '%s' with %d actions", macro_name, len(_MACROS[device_id][macro_name]))
        else:
            logger.warning("⚠️  Macro '%s' too long (%d actions, max %d)",
                          macro_name, len(_CURRENT_MACRO[device_id]), _MAX_MACRO_LENGTH)
            return False
    
    # Clear current macro
    if device_id in _CURRENT_MACRO:
        del _CURRENT_MACRO[device_id]
    
    return True


def _add_macro_action(device_id: str, action: Dict[str, Any]) -> None:
    """Add an action to the currently recording macro."""
    global _CURRENT_MACRO
    
    if device_id in _MACRO_RECORDING and _MACRO_RECORDING[device_id]:
        if device_id in _CURRENT_MACRO:
            _CURRENT_MACRO[device_id].append(action)
        else:
            _CURRENT_MACRO[device_id] = [action]


async def _playback_macro(device_id: str, macro_name: str) -> bool:
    """Playback a recorded macro."""
    global _MACROS, keyboard, mouse, SPECIAL_KEY_MAP, MOUSE_BUTTON_MAP
    
    with _MACROS_LOCK:
        if macro_name not in _MACROS:
            return False
        # Get macro for this device
        device_macros = _MACROS.get(device_id, {})
        if macro_name not in device_macros:
            return False
        macro_actions = device_macros[macro_name].copy()
    
    # Get the device's websocket for feedback
    device = _device_registry.get(device_id)
    if not device or not device.authed:
        return False
    
    # Check if controllers are initialized
    if keyboard is None or mouse is None:
        logger.error("❌ Cannot playback macro: input controllers not initialized")
        return False
    
    try:
        # Playback each action in the macro
        for action in macro_actions:
            action_type = action.get("action", "")
            if action_type in ("keypress", "keydown", "keyup"):
                _inject_key(action, keyboard, SPECIAL_KEY_MAP)
            elif action_type in ("mouse_move", "mouse_click", "mouse_scroll"):
                _inject_mouse(action, mouse, MOUSE_BUTTON_MAP)
            
            # Small delay between actions
            await asyncio.sleep(0.01)  # 10ms delay between actions
        
        logger.info("▶️  Played back macro '%s'", macro_name)
        return True
    except Exception as exc:
        input_log.error("❌ Macro playback failed '%s': %s", macro_name, exc)
        return False


def _delete_macro(device_id: str, macro_name: str) -> bool:
    """Delete a stored macro for a specific device."""
    global _MACROS
    
    with _MACROS_LOCK:
        device_macros = _MACROS.get(device_id)
        if device_macros and macro_name in device_macros:
            del device_macros[macro_name]
            if not device_macros:
                del _MACROS[device_id]
            _save_macros()
            logger.info("🗑️  Deleted macro '%s' for device %s", macro_name, device_id)
            return True
    return False


def _get_macro_list(device_id: str) -> list[str]:
    """Get list of stored macro names for a specific device."""
    with _MACROS_LOCK:
        device_macros = _MACROS.get(device_id)
        return list(device_macros.keys()) if device_macros else []


def _get_macro_details(device_id: str, macro_name: str) -> Optional[list]:
    """Get the actions for a specific macro for a specific device."""
    with _MACROS_LOCK:
        device_macros = _MACROS.get(device_id)
        return device_macros.get(macro_name).copy() if device_macros and macro_name in device_macros else None


# ─────────────────────────────────────────────
#  WebSocket Handler
# ─────────────────────────────────────────────

async def ws_handler(websocket: websockets.WebSocketServerProtocol) -> None:
    global _CLIPBOARD_HISTORY, _CLIPBOARD_LAST_LAPTOP_CONTENT
    client_addr = websocket.remote_address
    device_id   = str(uuid.uuid4())
    device:     ConnectedDevice | None = None

    ws_log.info("📱 Phone connecting: %s", client_addr)
    
    # Track this websocket for broadcasts (e.g., laptop clipboard sync)
    async with _WEBSOCKETS_LOCK:
        _WEBSOCKETS.add(websocket)
     
    # Check rate limit
    if not _check_rate_limit(client_addr):
        ws_log.warning("🚫 Rate limit exceeded: %s", client_addr)
        async with _WEBSOCKETS_LOCK:
            _WEBSOCKETS.discard(websocket)
        await websocket.close(1008, "rate_limit_exceeded")
        return

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

        # Check for reconnection attempt with grace period state
        reconnected_device_id = msg.get("reconnect_device_id")
        reconnected_state = None
        if reconnected_device_id:
            reconnected_state = _get_and_consume_disconnected_state(reconnected_device_id)

        if action == "hello":
            if _SESSION_PIN is not None:
                await websocket.send(json.dumps({"type": "auth_fail", "reason": "pin_required"}))
                # Small delay to ensure auth_fail is received before closing
                await asyncio.sleep(0.1)
                await websocket.close(1008, "pin_required")
                return
            
            # If reconnection with valid grace state, restore previous device_id and name
            if reconnected_state and reconnected_state.get("authed"):
                device_id = reconnected_device_id  # Restore original device_id
                device = ConnectedDevice(
                    device_id=device_id,
                    name=reconnected_state["name"],
                    websocket=websocket,
                    authed=True,
                    tab_id=tab_id or reconnected_state.get("tab_id"),
                )
                # Try to register; if tab_id conflict, fall back to new device
                if not _try_register_device(device, device.tab_id, client_addr):
                    # Tab already in use - create new device instead
                    device_id = str(uuid.uuid4())
                    device = ConnectedDevice(
                        device_id=device_id,
                        name=f"Device-{device_id[:4]}",
                        websocket=websocket,
                        authed=True,
                        tab_id=tab_id,
                    )
                    if not _try_register_device(device, tab_id, client_addr):
                        await websocket.send(json.dumps({"type": "auth_fail", "reason": "duplicate_tab"}))
                        # Small delay to ensure auth_fail is received before closing
                        await asyncio.sleep(0.1)
                        await websocket.close(1008, "duplicate_tab")
                        device = None
                        return
                ws_log.info("🔄 Reconnected (grace): %s (id=%s tabId=%s)", client_addr, device_id[:8], device.tab_id)
            else:
                # Fresh connection
                device = ConnectedDevice(
                    device_id=device_id,
                    name=f"Device-{device_id[:4]}",
                    websocket=websocket,
                    authed=True,
                    tab_id=tab_id,
                )
                if not _try_register_device(device, tab_id, client_addr):
                    await websocket.send(json.dumps({"type": "auth_fail", "reason": "duplicate_tab"}))
                    # Small delay to ensure auth_fail is received before closing
                    await asyncio.sleep(0.1)
                    await websocket.close(1008, "duplicate_tab")
                    device = None
                    return
                ws_log.info("📱 Connected (no-PIN): %s (id=%s tabId=%s)", client_addr, device_id[:8], tab_id)

        elif action == "pin_auth":
            if _SESSION_PIN is None:
                await websocket.send(json.dumps({"type": "auth_fail", "reason": "pin_not_required"}))
                # Small delay to ensure auth_fail is received before closing
                await asyncio.sleep(0.1)
                await websocket.close(1008, "pin_not_required")
                return
            if msg.get("pin", "") != _SESSION_PIN:
                ws_log.warning("🔒 Wrong PIN from %s", client_addr)
                await websocket.send(json.dumps({"type": "auth_fail", "reason": "wrong_pin"}))
                # Small delay to ensure auth_fail is received before closing
                await asyncio.sleep(0.1)
                await websocket.close(1008, "wrong_pin")
                return
            
            # If reconnection with valid grace state, restore previous device_id and name
            if reconnected_state and reconnected_state.get("authed"):
                device_id = reconnected_device_id  # Restore original device_id
                device = ConnectedDevice(
                    device_id=device_id,
                    name=reconnected_state["name"],
                    websocket=websocket,
                    authed=True,
                    tab_id=tab_id or reconnected_state.get("tab_id"),
                )
                if not _try_register_device(device, device.tab_id, client_addr):
                    # Tab conflict - create new device
                    device_id = str(uuid.uuid4())
                    device = ConnectedDevice(
                        device_id=device_id,
                        name=f"Device-{device_id[:4]}",
                        websocket=websocket,
                        authed=True,
                        tab_id=tab_id,
                    )
                    if not _try_register_device(device, tab_id, client_addr):
                        await websocket.send(json.dumps({"type": "auth_fail", "reason": "duplicate_tab"}))
                        # Small delay to ensure auth_fail is received before closing
                        await asyncio.sleep(0.1)
                        await websocket.close(1008, "duplicate_tab")
                        device = None
                        return
                ws_log.info("🔓 PIN verified (reconnect): %s (id=%s tabId=%s)", client_addr, device_id[:8], device.tab_id)
            else:
                # Fresh PIN auth
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
            await websocket.send(json.dumps({"type": "auth_fail", "reason": "expected_auth"}))
            # Small delay to ensure auth_fail is received before closing
            await asyncio.sleep(0.1)
            await websocket.close(1008, "expected_auth")
            return

        await websocket.send(json.dumps({"type": "auth_ok", "device_id": device_id}))
        await _broadcast_device_list()
        
        # Load connection data on first connection
        if not hasattr(_load_connections_data, '_called'):
            _load_connections_data()
            _load_connections_data._called = True
        
        # Add device to connection history (only for fresh connections, not reconnections)
        if device and not reconnected_state:
            _add_to_connection_history(device.device_id, device.name)

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
                    # Check if we allow phone→laptop clipboard sync based on direction setting
                    if _CLIPBOARD_SYNC_DIRECTION in ["phone_to_laptop", "bidirectional"]:
                        try:
                            import pyperclip
                            pyperclip.copy(text)
                            # Track this as laptop clipboard content for laptop→phone sync prevention
                            _CLIPBOARD_LAST_LAPTOP_CONTENT = text
                            # Add to clipboard history (avoid duplicates) - use lock
                            with _CLIPBOARD_HISTORY_LOCK:
                                if text not in _CLIPBOARD_HISTORY:
                                    _CLIPBOARD_HISTORY.insert(0, text)
                                    # Keep only the most recent items
                                    if len(_CLIPBOARD_HISTORY) > _MAX_CLIPBOARD_HISTORY:
                                        _CLIPBOARD_HISTORY = _CLIPBOARD_HISTORY[:_MAX_CLIPBOARD_HISTORY]
                            await websocket.send(json.dumps({"type": "clipboard_ack"}))
                            logger.info("📋 Clipboard: %d chars from %s (direction: %s)", len(text), client_addr, _CLIPBOARD_SYNC_DIRECTION)
                        except Exception as exc:
                            await websocket.send(json.dumps({"type": "clipboard_ack", "error": str(exc)}))
                    else:
                        # Direction is laptop_to_phone only, ignore phone→laptop push
                        await websocket.send(json.dumps({"type": "clipboard_ack", "info": "Clipboard push ignored (laptop_to_phone only mode)"}))
                        logger.info("📋 Clipboard push ignored from %s (direction: %s)", client_addr, _CLIPBOARD_SYNC_DIRECTION)
                elif not _CLIPBOARD_AVAILABLE:
                    await websocket.send(json.dumps(
                        {"type": "clipboard_ack", "error": "pyperclip not installed"}
                    ))
                continue
                
            if action == "file_transfer":
                if not _FILE_TRANSFER_ENABLED:
                    await websocket.send(json.dumps({
                        "type": "file_transfer_ack",
                        "error": "File transfer disabled"
                    }))
                    continue
                    
                file_name = msg.get("file_name", "")
                file_data = msg.get("file_data", "")  # Base64 encoded
                file_size = msg.get("file_size", 0)
                
                if not file_name or not file_data:
                    await websocket.send(json.dumps({
                        "type": "file_transfer_ack",
                        "error": "Missing file name or data"
                    }))
                    continue
                    
                # Validate file size
                if file_size > _MAX_FILE_SIZE:
                    await websocket.send(json.dumps({
                        "type": "file_transfer_ack",
                        "error": f"File too large. Maximum size: {_MAX_FILE_SIZE // (1024*1024)} MB"
                    }))
                    continue
                    
                # Validate file extension
                file_ext = "." + file_name.split(".")[-1].lower() if "." in file_name else ""
                if file_ext not in _ALLOWED_FILE_EXTENSIONS:
                    await websocket.send(json.dumps({
                        "type": "file_transfer_ack",
                        "error": f"File type not allowed. Allowed: {', '.join(sorted(_ALLOWED_FILE_EXTENSIONS))}"
                    }))
                    continue
                
                try:
                    import base64
                    import os
                    from pathlib import Path
                    
                    # Decode base64 data
                    file_bytes = base64.b64decode(file_data)
                    
                    # Save to temporary directory (in actual implementation, you might want to save to a specific folder)
                    # For now, we'll just acknowledge receipt and log it
                    # In a production app, you might save to a downloads folder or prompt user
                    
                    logger.info("📎 File received: %s (%d bytes) from %s", file_name, len(file_bytes), client_addr)
                    
                    # For demo purposes, we'll just acknowledge - in a real app you'd save the file
                    # You could implement actual file saving here if desired
                    
                    await websocket.send(json.dumps({
                        "type": "file_transfer_ack",
                        "success": True,
                        "message": f"File '{file_name}' received successfully"
                    }))
                    
                except Exception as exc:
                    logger.error("File transfer error: %s", exc)
                    await websocket.send(json.dumps({
                        "type": "file_transfer_ack",
                        "error": f"Failed to process file: {str(exc)}"
                    }))
                continue

            if action == "clipboard_history":
                with _CLIPBOARD_HISTORY_LOCK:
                    history_copy = _CLIPBOARD_HISTORY.copy()
                await websocket.send(json.dumps({
                    "type": "clipboard_history",
                    "history": history_copy
                }))
                continue
                
            if action == "connection_history":
                await websocket.send(json.dumps({
                    "type": "connection_history",
                    "history": _get_connection_history(),
                    "favorites": _get_favorite_devices()
                }))
                continue
                
            if action == "get_qr_code":
                await websocket.send(json.dumps({
                    "type": "qr_code_data",
                    "data": _LAST_QR_CODE_DATA
                }))
                continue
                
            if action == "toggle_favorite":
                device_id = msg.get("device_id", "")
                if device_id:
                    is_favorite = _toggle_favorite(device_id)
                    await websocket.send(json.dumps({
                        "type": "toggle_favorite",
                        "device_id": device_id,
                        "is_favorite": is_favorite
                    }))
                else:
                    await websocket.send(json.dumps({
                        "type": "toggle_favorite",
                        "error": "Missing device_id"
                    }))
                continue
                
            if action == "gesture_command":
                device_id = msg.get("device_id", "")
                gesture_type = msg.get("gesture_type", "")
                if device_id and gesture_type:
                    # Process gesture command
                    command = _GESTURE_COMMANDS.get(gesture_type)
                    if command:
                        # Convert gesture to appropriate key/mouse actions
                        await _process_gesture_command(device_id, command, msg)
                        await websocket.send(json.dumps({
                            "type": "gesture_ack",
                            "gesture_type": gesture_type,
                            "command": command
                        }))
                    else:
                        await websocket.send(json.dumps({
                            "type": "gesture_ack",
                            "error": f"Unknown gesture type: {gesture_type}"
                        }))
                else:
                    await websocket.send(json.dumps({
                        "type": "gesture_ack",
                        "error": "Missing device_id or gesture_type"
                    }))
                continue

            # Macro recording and playback commands
            if action == "macro_start_record":
                device_id = msg.get("device_id", "")
                macro_name = msg.get("macro_name", "")
                if device_id and macro_name:
                    success = _start_macro_recording(device_id, macro_name)
                    await websocket.send(json.dumps({
                        "type": "macro_start_record",
                        "success": success,
                        "macro_name": macro_name
                    }))
                    if success:
                        logger.info("🔴 Started recording macro '%s' for device %s", macro_name, device_id)
                    else:
                        logger.warning("⚠️  Failed to start recording macro '%s' for device %s", macro_name, device_id)
                else:
                    await websocket.send(json.dumps({
                        "type": "macro_start_record",
                        "success": False,
                        "error": "Missing device_id or macro_name"
                    }))
                continue

            if action == "macro_stop_record":
                device_id = msg.get("device_id", "")
                macro_name = msg.get("macro_name", "")
                if device_id and macro_name:
                    success = _stop_macro_recording(device_id, macro_name)
                    await websocket.send(json.dumps({
                        "type": "macro_stop_record",
                        "success": success,
                        "macro_name": macro_name
                    }))
                    if success:
                        logger.info("⏹️  Stopped recording macro '%s' for device %s", macro_name, device_id)
                    else:
                        logger.warning("⚠️  Failed to stop recording macro '%s' for device %s", macro_name, device_id)
                else:
                    await websocket.send(json.dumps({
                        "type": "macro_stop_record",
                        "success": False,
                        "error": "Missing device_id or macro_name"
                    }))
                continue

            if action == "macro_playback":
                device_id = msg.get("device_id", "")
                macro_name = msg.get("macro_name", "")
                if device_id and macro_name:
                    success = await _playback_macro(device_id, macro_name)
                    await websocket.send(json.dumps({
                        "type": "macro_playback",
                        "success": success,
                        "macro_name": macro_name
                    }))
                    if success:
                        logger.info("▶️  Played back macro '%s' for device %s", macro_name, device_id)
                    else:
                        logger.warning("⚠️  Failed to playback macro '%s' for device %s", macro_name, device_id)
                else:
                    await websocket.send(json.dumps({
                        "type": "macro_playback",
                        "success": False,
                        "error": "Missing device_id or macro_name"
                    }))
                continue

            if action == "macro_delete":
                device_id = msg.get("device_id", "")
                macro_name = msg.get("macro_name", "")
                if device_id and macro_name:
                    success = _delete_macro(device_id, macro_name)
                    await websocket.send(json.dumps({
                        "type": "macro_delete",
                        "success": success,
                        "macro_name": macro_name
                    }))
                    if success:
                        logger.info("🗑️  Deleted macro '%s' for device %s", macro_name, device_id)
                    else:
                        logger.warning("⚠️  Failed to delete macro '%s' for device %s", macro_name, device_id)
                else:
                    await websocket.send(json.dumps({
                        "type": "macro_delete",
                        "success": False,
                        "error": "Missing device_id or macro_name"
                    }))
                continue

            if action == "macro_list":
                device_id = msg.get("device_id", "")
                if device_id:
                    macro_list = _get_macro_list(device_id)
                    await websocket.send(json.dumps({
                        "type": "macro_list",
                        "macros": macro_list
                    }))
                else:
                    await websocket.send(json.dumps({
                        "type": "macro_list",
                        "macros": [],
                        "error": "Missing device_id"
                    }))
                continue

            if action == "macro_details":
                device_id = msg.get("device_id", "")
                macro_name = msg.get("macro_name", "")
                if device_id and macro_name:
                    details = _get_macro_details(device_id, macro_name)
                    await websocket.send(json.dumps({
                        "type": "macro_details",
                        "macro_name": macro_name,
                        "details": details
                    }))
                else:
                    await websocket.send(json.dumps({
                        "type": "macro_details",
                        "error": "Missing device_id or macro_name"
                    }))
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
        # Remove from websocket tracking
        async with _WEBSOCKETS_LOCK:
            _WEBSOCKETS.discard(websocket)
        if device:
            _unregister_device(device.device_id)
            await _broadcast_device_list()

# ─────────────────────────────────────────────
#  HTTP Server
# ─────────────────────────────────────────────

class PhoneKeyHTTPHandler(BaseHTTPRequestHandler):
    """
    Serves:
      GET /            -> animated welcome / browser-chooser page
      GET /api/config  -> JSON config for the client (pin_required, version)
      GET /index.html  -> main PhoneKey SPA
      GET /*           -> static files from CLIENT_DIR
    """

    def do_GET(self):

        # ── /api/config  - replaces the brittle PIN string-injection ───────
        if self.path == "/api/config":
            payload = json.dumps({
                "pin_required": _SESSION_PIN is not None,
                "version": __version__,
                # When tunnel is active, client must use wss://tunnel-host (not LAN IP)
                # When None, client uses its default WS_URL calculation
                "ws_url":        _WS_URL_OVERRIDE,
                "ws_port":       _HTTP_PORT,    # WS is now on same port as HTTP
                # Clipboard sync direction for client UI adaptation
                "clipboard_sync_direction": _CLIPBOARD_SYNC_DIRECTION,
                # Reconnection settings for exponential backoff and graceful reconnection
                "reconnect": {
                    "grace_seconds": _RECONNECT_GRACE_SECONDS,
                    "base_delay_ms": _RECONNECT_BASE_DELAY_MS,
                    "max_delay_ms": _RECONNECT_MAX_DELAY_MS,
                    "backoff_factor": _RECONNECT_BACKOFF_FACTOR,
                },
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
#  Combined HTTP+WS request handler (process_request)
#  Dual-API: works with BOTH websockets legacy (path, headers)
#            AND websockets 12.x new (connection, request) signatures.
#  The Cloudflare tunnel path routes through websockets.legacy.server,
#  while direct local connections may use the new server — so we must
#  detect which API is active at call time.
# ─────────────────────────────────────────────

async def _http_process_request(*args):
    """
    Handles both websockets API variants transparently:

    Legacy API  →  process_request(path: str, request_headers: Headers)
    New 12.x    →  process_request(connection, request)
                   where request has .path and .headers attributes

    Detection:  if args[0] is a plain str  → legacy
                otherwise                  → new 12.x
    """
    from http import HTTPStatus

    # ── 1. Detect API variant and normalise to (path, headers) ───────────
    if isinstance(args[0], str):
        # ── Legacy API: (path: str, request_headers: Headers) ─────────────
        path            = args[0]
        request_headers = args[1]        # already a Headers-like object
        _legacy         = True
    else:
        # ── New 12.x API: (connection, request) ───────────────────────────
        request         = args[1]
        path            = request.path
        request_headers = request.headers
        _legacy         = False

    # ── 2. WebSocket upgrade → let websockets handle the handshake ────────
    upgrade = request_headers.get("Upgrade", "")
    if upgrade.lower() == "websocket":
        return None

    clean_path = path.split("?")[0]

    # ── 3. Build response body ────────────────────────────────────────────

    # /api/config
    if clean_path == "/api/config":
        body = json.dumps({
            "pin_required":             _SESSION_PIN is not None,
            "version":                  __version__,
            "ws_url":                   _WS_URL_OVERRIDE,
            "ws_port":                  _HTTP_PORT,
            "clipboard_sync_direction": _CLIPBOARD_SYNC_DIRECTION,
            "reconnect": {
                "grace_seconds":  _RECONNECT_GRACE_SECONDS,
                "base_delay_ms":  _RECONNECT_BASE_DELAY_MS,
                "max_delay_ms":   _RECONNECT_MAX_DELAY_MS,
                "backoff_factor": _RECONNECT_BACKOFF_FACTOR,
            },
        }).encode("utf-8")
        return _make_ws_response(
            HTTPStatus.OK,
            [("Content-Type",   "application/json"),
             ("Content-Length", str(len(body))),
             ("Cache-Control",  "no-cache")],
            body,
            _legacy,
        )

    # / → redirect
    if clean_path == "/":
        return _make_ws_response(
            HTTPStatus.MOVED_PERMANENTLY,
            [("Location", "/index.html")],
            b"",
            _legacy,
        )

    # Static files
    rel       = clean_path.lstrip("/") or "index.html"
    file_path = CLIENT_DIR / rel
    if not file_path.exists() or not file_path.is_file():
        file_path = CLIENT_DIR / "index.html"   # SPA fallback
    if not file_path.exists():
        return _make_ws_response(
            HTTPStatus.NOT_FOUND,
            [("Content-Type", "text/plain")],
            b"Not Found",
            _legacy,
        )

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
    return _make_ws_response(
        HTTPStatus.OK,
        [("Content-Type",   mime),
         ("Content-Length", str(len(content))),
         ("Cache-Control",  "no-cache")],
        content,
        _legacy,
    )


def _make_ws_response(status, headers_list: list, body: bytes, legacy: bool):
    """
    Return the correct response object for the active websockets API.

    Legacy path  → plain (status, headers_list, body) tuple
    New 12.x     → websockets.http11.Response  (or datastructures shim)
    """
    if legacy:
        # websockets legacy server accepts a raw tuple
        return (status, headers_list, body)

    # New 12.x server requires a Response object
    try:
        from websockets.http11 import Response
        from websockets.datastructures import Headers
        return Response(status, Headers(headers_list), body)
    except ImportError:
        # Absolute fallback: return legacy tuple (websockets will usually accept it)
        return (status, headers_list, body)

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
_HTTP_PORT: int  # set in main()


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
    The QR encodes /index.html directly - no welcome page redirect.
    """
    # Always point QR directly at the app, not the welcome page
    app_url = url.rstrip("/") + "/index.html"
    
    # Store QR code data for persistence
    global _LAST_QR_CODE_DATA
    _LAST_QR_CODE_DATA = {
        "url": url,
        "app_url": app_url,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }
    _save_connections_data()

    # ── Push QR URL to GUI panel (no-op if running in terminal mode) ──────
    try:
        from gui_launcher import notify_qr
        notify_qr(app_url)
    except Exception:
        pass                            # GUI not running — safe to ignore

    try:
        import qrcode
        qr = qrcode.QRCode(
            version=None,                                    # ✅ let library pick smallest version
            error_correction=qrcode.constants.ERROR_CORRECT_L,  # smallest = fewest modules
            box_size=1,
            border=1,
        )
        qr.add_data(app_url)
        qr.make(fit=True)
        matrix = qr.get_matrix()

        # Pad to even number of rows for half-block rendering
        if len(matrix) % 2 != 0:
            matrix.append([False] * len(matrix[0]))

        print()
        print("  📷  Scan QR code with your phone camera:")
        print()
        # Half-block technique: encode 2 rows per terminal line → square output
        for i in range(0, len(matrix), 2):
            line = "  "
            for top, bot in zip(matrix[i], matrix[i + 1]):
                if top and bot:
                    line += "█"   # both filled
                elif top:
                    line += "▀"   # top half filled
                elif bot:
                    line += "▄"   # bottom half filled
                else:
                    line += " "   # both empty
            print(line)
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
    return """<!DOCTYPE html>
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
_CLIPBOARD_AVAILABLE: bool


async def main(args: Namespace) -> None:
    """
    Server entry point. Receives parsed CLI args from system.py.
    All runtime state is initialised here — never at module level.
    """
    global _WS_PORT, _USE_HTTPS, _TUNNEL_URL, _SESSION_PIN, _MOUSE_SPEED
    global _HTTP_PORT, _CLIPBOARD_AVAILABLE, _WS_URL_OVERRIDE
    global _CLIPBOARD_LAPTOP_CHECK_TASK, _GRACE_LOCK
    global _CLIPBOARD_SYNC_DIRECTION, _CLIPBOARD_LAST_LAPTOP_CONTENT

    # ── Validate + store runtime config ──────────────────────────────────
    _WS_PORT = args.ws_port
    _HTTP_PORT = args.http_port
    _USE_HTTPS = args.https
    _MOUSE_SPEED = max(0.1, min(5.0, args.mouse_speed))
    _SESSION_PIN = (
        None if args.no_pin
        else f"{random.SystemRandom().randint(0, 9999):04d}"
    )

    # ── Environment check ────────────────────────────────────────────────
    _check_environment()

    # ── pynput ───────────────────────────────────────────────────────────
    global keyboard, mouse
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

    # ── Clipboard sync direction ─────────────────────────────────────────
    # Load from config or use default
    _CLIPBOARD_SYNC_DIRECTION = getattr(args, 'clipboard_sync_direction', 'phone_to_laptop')
    logger.info("📋 Clipboard sync direction: %s", _CLIPBOARD_SYNC_DIRECTION)
    
    # Start laptop→phone clipboard sync if needed
    if _CLIPBOARD_SYNC_DIRECTION in ["laptop_to_phone", "bidirectional"] and _CLIPBOARD_AVAILABLE:
        _CLIPBOARD_LAPTOP_CHECK_TASK = asyncio.create_task(_monitor_laptop_clipboard())
        logger.info("🔄 Started laptop→phone clipboard monitoring")

    # ── Network + SSL ─────────────────────────────────────────────────────
    local_ip = get_local_ip()
    ssl_ctx  = build_ssl_context(local_ip) if _USE_HTTPS else None

    # ── Tunnel (optional) ─────────────────────────────────────────────────
    tunnel_url:     Optional[str]           = None
    _TUNNEL_URL = None
    _WS_URL_OVERRIDE = None  # when set, client uses this WS URL instead of default
    tunnel_manager = None

    if args.tunnel:
        if not TUNNEL_AVAILABLE:
            logger.error("❌  --tunnel requested but tunnel_manager module not found.")
            sys.exit(1)
        # Start tunnel — no need to pre-start HTTP server, websockets handles HTTP    
        tunnel_manager = TunnelManager(_HTTP_PORT)
        tunnel_url     = tunnel_manager.start()
        if tunnel_url:
            _TUNNEL_URL = tunnel_url
            # For tunnel, WS goes through the same tunnel host on port 443 (standard HTTPS)
            # No port number needed — Cloudflare terminates at 443
            tunnel_host      = tunnel_url.replace("https://", "").replace("http://", "").rstrip("/")
            _WS_URL_OVERRIDE = f"wss://{tunnel_host}"   # port 443 implicit 
            logger.info("🌐 Tunnel URL: %s", tunnel_url)
            logger.info("🔌 WS via tunnel: %s", _WS_URL_OVERRIDE)
        else:
            logger.warning("⚠️  Tunnel failed — serving on local URL.")

    # ── Banner ────────────────────────────────────────────────────────────
    print_banner(local_ip, ssl_ctx, tunnel_url)
    ws_proto = "wss" if (ssl_ctx or tunnel_url) else "ws"
    print_qr_and_url(_TUNNEL_URL or f"{'https' if ssl_ctx else 'http'}://{local_ip}:{_HTTP_PORT}")
    logger.info("🔌 WebSocket (%s) + HTTP → port %d", ws_proto.upper(), _HTTP_PORT)

    # ── Event loop + signals ──────────────────────────────────────────────
    stop_event = asyncio.Event()
    loop       = asyncio.get_running_loop()
    _setup_signals(stop_event, loop)

    # ── Key worker ─────────────────────────────────────────────────────────
    key_worker = _make_key_worker(keyboard, mouse, SPECIAL_KEY_MAP, MOUSE_BUTTON_MAP)
    worker     = asyncio.create_task(key_worker())

    # ── Grace period cleanup task ──────────────────────────────────────────
    async def _grace_cleanup_task():
        """Periodically clean up expired grace period states."""
        while True:
            await asyncio.sleep(60)  # Run every minute
            _cleanup_expired_grace_states()
    
    grace_cleanup = asyncio.create_task(_grace_cleanup_task())
    
    async def _rate_limit_cleanup_task():
        """Periodically clean up old rate limit entries."""
        while True:
            await asyncio.sleep(300)  # Run every 5 minutes
            now = datetime.now(timezone.utc).timestamp()
            with _CONNECTION_RATE_LOCK:
                for ip in list(_CONNECTION_ATTEMPTS.keys()):
                    _CONNECTION_ATTEMPTS[ip] = [
                        ts for ts in _CONNECTION_ATTEMPTS[ip]
                        if now - ts < _CONNECTION_RATE_WINDOW
                    ]
                    if not _CONNECTION_ATTEMPTS[ip]:
                        del _CONNECTION_ATTEMPTS[ip]
    
    rate_limit_cleanup = asyncio.create_task(_rate_limit_cleanup_task())

    # ── WebSocket server ──────────────────────────────────────────────────
    async with websockets.serve(
        ws_handler,
        "0.0.0.0",
        _HTTP_PORT,                         # HTTP port (8080) — single port for everything
        ssl=ssl_ctx,
        ping_interval=WS_PING_INTERVAL,
        ping_timeout=WS_PING_TIMEOUT,
        process_request=_http_process_request,  # Serve HTTP files + /api/config
    ):
        logger.info("✅ Server ready — waiting for phone connection…")
        await stop_event.wait()

    worker.cancel()
    try:
        await worker
    except asyncio.CancelledError:
        pass

    # Cleanup clipboard monitoring task
    if _CLIPBOARD_LAPTOP_CHECK_TASK and not _CLIPBOARD_LAPTOP_CHECK_TASK.done():
        _CLIPBOARD_LAPTOP_CHECK_TASK.cancel()
        try:
            await _CLIPBOARD_LAPTOP_CHECK_TASK
        except asyncio.CancelledError:
            pass
    
    # Cleanup grace period task
    grace_cleanup.cancel()
    try:
        await grace_cleanup
    except asyncio.CancelledError:
        pass
    
    # Cleanup rate limit task
    rate_limit_cleanup.cancel()
    try:
        await rate_limit_cleanup
    except asyncio.CancelledError:
        pass
    
    if tunnel_manager:
        tunnel_manager.stop()