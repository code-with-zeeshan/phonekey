"""
PhoneKey — System Entry Point  (system.py)
Contract : Interactive startup configuration, CLI argument parsing,
           instance locking, logging bootstrap, and process lifecycle.
           All server logic lives in server.py.
"""

__version__ = "3.1.0"

import argparse
import asyncio
import errno
import socket
import sys

from logging_setup import setup_logging

_DEFAULT_WS_PORT   = 8765
_DEFAULT_HTTP_PORT = 8080
_lock_socket: socket.socket | None = None

# ── Windows Ctrl+C — must be registered BEFORE asyncio.run() ─────────────────
# PyInstaller captures SIGINT at the bootloader level; the Win32 handler
# bypasses that and works in both script and .exe contexts.
if sys.platform == "win32":
    import ctypes
    import ctypes.wintypes

    _CTRL_C_EVENT     = 0
    _CTRL_CLOSE_EVENT = 2
    _WinHandler = ctypes.WINFUNCTYPE(ctypes.wintypes.BOOL, ctypes.wintypes.DWORD)

    # Placeholder — replaced in _setup_win_ctrl() once loop + event exist
    _win_handler_ref = None

    def _setup_win_ctrl(stop_cb):
        """
        Register the Win32 console control handler.
        Must be called with a concrete stop callback before asyncio.run().
        """
        global _win_handler_ref

        def _handler(ctrl_type: int) -> bool:
            if ctrl_type in (_CTRL_C_EVENT, _CTRL_CLOSE_EVENT):
                stop_cb()
                return True
            return False

        _win_handler_ref = _WinHandler(_handler)   # keep ref — prevents GC
        ctypes.windll.kernel32.SetConsoleCtrlHandler(_win_handler_ref, True)


# ─────────────────────────────────────────────────────────────────────────────
#  CLI
# ─────────────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="phonekey",
        description="PhoneKey — Use your phone as a wireless keyboard & mouse.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python system.py                          # interactive setup
  python system.py --no-pin                 # skip PIN
  python system.py --https                  # HTTPS on same WiFi
  python system.py --tunnel                 # Cloudflare tunnel (cross-network)
  python system.py --ws-port 9000 --http-port 9001
  python system.py --mouse-speed 2.0
  python system.py --yes                    # skip interactive prompt, use defaults
        """,
    )
    parser.add_argument("--ws-port",    type=int, default=None, metavar="PORT",
                        help=f"WebSocket port (default: {_DEFAULT_WS_PORT})")
    parser.add_argument("--http-port",  type=int, default=None, metavar="PORT",
                        help=f"HTTP port (default: {_DEFAULT_HTTP_PORT})")
    parser.add_argument("--https",      action="store_true", default=None,
                        help="Enable HTTPS/WSS with a self-signed certificate")
    parser.add_argument("--no-pin",     action="store_true", default=None,
                        help="Disable 4-digit PIN authentication")
    parser.add_argument("--mouse-speed",type=float, default=None, metavar="MULT",
                        help="Mouse speed multiplier 0.1–5.0 (default: 1.0)")
    parser.add_argument("--tunnel",     action="store_true", default=None,
                        help="Enable Cloudflare Quick Tunnel (cross-network, no cert warning)")
    parser.add_argument("--log-level",  default="INFO",
                        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
                        help="Logging verbosity (default: INFO)")
    parser.add_argument("--yes", "-y",  action="store_true", default=False,
                        help="Skip interactive setup, use defaults / CLI flags only")
    return parser.parse_args()


# ─────────────────────────────────────────────────────────────────────────────
#  Interactive Startup TUI
#  Shown when running the .exe by double-click or `python system.py` with no flags.
#  Skipped entirely when --yes is passed or all relevant flags are explicit.
# ─────────────────────────────────────────────────────────────────────────────

def _banner():
    print()
    print("╔══════════════════════════════════════════════════════════╗")
    print(f"║           📱  PhoneKey  v{__version__}  💻                  ║")
    print("║      Use your phone as a wireless keyboard & mouse      ║")
    print("╚══════════════════════════════════════════════════════════╝")
    print()


def _interactive_setup(args: argparse.Namespace) -> argparse.Namespace:
    """
    Walk the user through connection mode selection.
    Only prompts for options that were NOT already supplied via CLI flags.
    Returns a fully populated Namespace.
    """
    _banner()

    # Detect if we are running inside a PyInstaller bundle (double-clicked .exe)
    is_frozen = getattr(sys, "frozen", False)

    print("  Choose connection mode:\n")
    print("  [1]  Local WiFi    — phone & laptop on same network (default)")
    print("       HTTP, no certificate warning, simplest setup")
    print()
    print("  [2]  Local HTTPS   — same WiFi, encrypted")
    print("       Phone will show a one-time certificate warning")
    print()
    print("  [3]  Cloudflare Tunnel  — phone & laptop on ANY network")
    print("       Secure HTTPS, no cert warning, requires internet")
    print()

    if is_frozen:
        print("  (Type a number and press Enter. Close window to exit.)")
    else:
        print("  (Press Enter for default [1] or Ctrl+C to cancel)")
    print()

    # ── Mode ─────────────────────────────────────────────────────────────
    if args.tunnel is None and args.https is None:
        while True:
            try:
                choice = input("  Mode [1/2/3] → ").strip() or "1"
            except (EOFError, KeyboardInterrupt):
                print("\n  Cancelled.")
                sys.exit(0)
            if choice == "1":
                args.https  = False
                args.tunnel = False
                break
            elif choice == "2":
                args.https  = True
                args.tunnel = False
                break
            elif choice == "3":
                args.https  = False
                args.tunnel = True
                break
            else:
                print("  Please enter 1, 2, or 3.")
    else:
        # Flags were explicit — show what will be used
        mode = "Cloudflare Tunnel" if args.tunnel else ("HTTPS" if args.https else "Local WiFi")
        print(f"  Mode: {mode}  (from CLI flags)")

    print()

    # ── PIN ──────────────────────────────────────────────────────────────
    if args.no_pin is None:
        try:
            pin_input = input("  Enable connection PIN? [Y/n] → ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\n  Cancelled.")
            sys.exit(0)
        args.no_pin = pin_input in ("n", "no")
    else:
        status = "disabled" if args.no_pin else "enabled"
        print(f"  PIN: {status}  (from CLI flags)")

    print()

    # ── Mouse Speed ───────────────────────────────────────────────────────
    if args.mouse_speed is None:
        try:
            speed_input = input("  Mouse speed [0.1–5.0, Enter=1.0] → ").strip()
            args.mouse_speed = float(speed_input) if speed_input else 1.0
            args.mouse_speed = max(0.1, min(5.0, args.mouse_speed))
        except (ValueError, EOFError, KeyboardInterrupt):
            args.mouse_speed = 1.0
    else:
        print(f"  Mouse speed: {args.mouse_speed}  (from CLI flags)")

    print()

    # ── Ports (advanced — only shown if not already set) ──────────────────
    if args.ws_port is None:
        args.ws_port = _DEFAULT_WS_PORT
    if args.http_port is None:
        args.http_port = _DEFAULT_HTTP_PORT

    # ── Confirm ───────────────────────────────────────────────────────────
    mode_label = (
        "Cloudflare Tunnel 🌐" if args.tunnel
        else ("HTTPS/WSS 🔒"   if args.https
        else  "Local WiFi HTTP 🔓")
    )
    pin_label  = "disabled" if args.no_pin else "enabled"

    print("  ── Starting with ──────────────────────────────")
    print(f"  Mode        : {mode_label}")
    print(f"  PIN         : {pin_label}")
    print(f"  Mouse speed : {args.mouse_speed}x")
    print(f"  WS port     : {args.ws_port}")
    print(f"  HTTP port   : {args.http_port}")
    print("  ────────────────────────────────────────────────")
    print()

    if is_frozen:
        print("  Press Enter to start, or close this window to cancel.")
        try:
            input()
        except (EOFError, KeyboardInterrupt):
            sys.exit(0)
    else:
        print("  Starting in 1 second…  (Ctrl+C to cancel)")
        import time
        time.sleep(1)

    return args


def _needs_interactive(args: argparse.Namespace) -> bool:
    """
    Return True if interactive setup should run.
    Skip if --yes is set OR if all key flags were explicitly provided.
    """
    if args.yes:
        return False
    # If none of the mode flags were set, show the TUI
    return args.tunnel is None and args.https is None and args.no_pin is None


# ─────────────────────────────────────────────────────────────────────────────
#  Instance Lock
# ─────────────────────────────────────────────────────────────────────────────

def acquire_instance_lock(ws_port: int) -> None:
    global _lock_socket
    lock_port = ws_port + 10_000
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
    try:
        sock.bind(("127.0.0.1", lock_port))
        sock.listen(1)
        _lock_socket = sock
    except OSError as exc:
        if exc.errno in (errno.EADDRINUSE, 10048):
            print(
                f"\n❌  PhoneKey is already running on port {ws_port}.\n"
                f"    Close the other instance first, or use a different port:\n"
                f"    python system.py --ws-port 9000\n",
                file=sys.stderr,
            )
            sys.exit(1)
        raise


def release_instance_lock() -> None:
    global _lock_socket
    if _lock_socket:
        try:
            _lock_socket.close()
        except Exception:
            pass
        _lock_socket = None


# ─────────────────────────────────────────────────────────────────────────────
#  Bootstrap
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    args = parse_args()

    # 1. Apply defaults for any flags still None (--yes path or partial flags)
    if args.ws_port    is None: args.ws_port    = _DEFAULT_WS_PORT
    if args.http_port  is None: args.http_port  = _DEFAULT_HTTP_PORT
    if args.https      is None: args.https      = False
    if args.no_pin     is None: args.no_pin     = False
    if args.mouse_speed is None: args.mouse_speed = 1.0
    if args.tunnel     is None: args.tunnel     = False

    # 2. Interactive setup (skipped if --yes or all flags explicit)
    if _needs_interactive(args):
        args = _interactive_setup(args)

    # 3. Logging must be ready before server.py is imported
    log = setup_logging(level=args.log_level)

    # 4. Windows Ctrl+C — register BEFORE asyncio.run()
    #    We use a threading.Event so the callback works from any thread
    if sys.platform == "win32":
        import threading
        _win_stop_flag = threading.Event()

        def _win_stop_cb():
            log.info("🛑 Ctrl+C detected — shutting down…")
            _win_stop_flag.set()

        _setup_win_ctrl(_win_stop_cb)

    # 5. Prevent duplicate processes
    acquire_instance_lock(args.ws_port)

    try:
        # 6. Import server only after logging is configured
        from server import main
        asyncio.run(main(args))
    except KeyboardInterrupt:
        pass
    finally:
        release_instance_lock()
        log.info("✅ PhoneKey stopped cleanly.")