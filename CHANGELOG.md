# Changelog

All notable changes to PhoneKey are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

## [2.1.0] — 2026-04-22 — First Public Release

### Added
- Virtual cursor with tap-to-reposition in live preview
- Cursor sync: arrow-key delta sent to laptop on every preview tap
- Right-side modifier keys (Right Shift, Right Ctrl, AltGr)
- Socket-based instance lock — prevents duplicate server processes
- `PhoneKeyHTTPServer` — silently suppresses WinError 10054 / BrokenPipe
- OS auto-detection with backend label in startup banner
- Sync indicator ("↔ N keys sent") shown after cursor reposition
- asyncio key queue with 12ms spacing — eliminates fast-typing key drops
- Exponential backoff WebSocket reconnect (1s → 2s → 4s → max 16s)
- Page Visibility API — auto-reconnects when phone screen turns on
- Connection deduplication by browser tab ID
- Type hints for better maintainability
- Configurable parameters via environment variables
- Unit tests for core logic
- Connection metrics counter
- PyInstaller standalone binary support — zero Python install required

### Fixed
- Only first character appearing after tap-to-type (focus stolen by preview)
- Modifier keys not applied to special/function keys (ALT+F4, CTRL+←, etc.)
- Duplicate server processes printing double banners
- WinError 10054 traceback on every phone screen-off event
- Cursor position mismatch between live preview and target application

### Architecture
- Sentinel + input event pattern (fixes mobile keydown:229 bug)
- Pure string virtual cursor (no Selection API, no focus juggling)
- Cross-platform signal handling (Windows SIGINT / Unix SIGTERM)

---

## [1.0.0] — 2026-04-21 — Initial Development Version

### Added
- WebSocket server (port 8765) + HTTP server (port 8080)
- pynput OS-level keystroke injection
- Phone browser UI (vanilla HTML/CSS/JS — no framework)
- Quick keys: Enter, Tab, Backspace, Escape, Delete, CapsLock, Arrows
- Modifier keys: Shift, Ctrl, Alt
- Function keys: F1–F12
- Basic live preview