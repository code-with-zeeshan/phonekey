# Changelog

All notable changes to PhoneKey are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

## [3.2.0] — 2026-05-02 — Thread-Safety & Feature Release

### Added
- **Clipboard History**: Persistent clipboard history with file-based storage
- **File Transfer**: Send files from phone to laptop (up to 10MB, multiple formats)
- **Macro Recording**: Record and playback keyboard/mouse sequences per device
- **Gesture Commands**: Touch gestures for window management (alt-tab, show desktop, etc.)
- **Multi-Language UI**: English, 中文, Español support with language switcher
- **Clipboard Sync Direction**: Control clipboard sync direction (phone→laptop, laptop→phone, bidirectional)
- **Connection History**: Track past connections and mark favorites
- **QR Code Persistence**: Last QR code data stored for quick reconnection
- **Grace Period Reconnection**: Session state preserved after disconnect for quick reconnection
- **Rate Limiting**: Prevent brute-force attacks on PIN authentication
- **WebSocket Broadcast**: Track connected clients for broadcast messages
- **Laptop→Phone Clipboard Sync**: Background monitoring for clipboard changes on laptop

### Fixed
- **Thread-Safety**: Added `_WEBSOCKETS_LOCK` for WebSocket set operations
- **Thread-Safety**: All shared state (`_CLIPBOARD_HISTORY`, `_MACROS`, `_CONNECTION_HISTORY`, `_FAVORITE_DEVICES`) properly locked
- **Thread-Safety**: Added `_GRACE_PERIOD_STATES` lock for reconnection state management
- **Undefined Function**: Implemented `_process_gesture_command()` for gesture handling
- **Missing Function**: Implemented `changeLanguage()` in client for UI language switching
- **Duplicate IDs**: Fixed duplicate `clipboard-section-label` ID in HTML
- **QR Code Aspect Ratio**: Changed to double-width rendering for more square appearance
- **File Input**: Added `onchange="sendFile()"` handler to file input element

### Changed
- **Version**: Bumped from 3.1.0 to 3.2.0
- **README.md**: Updated features list and Phone UI documentation
- **WORKFLOW.md**: Fixed git clone URL to correct repository
- **config.json**: Enhanced documentation for battery, security, sensitivity, and macro options
- **system.py**: Refactored interactive setup with clearer comments and input validation
- **QR Code Rendering**: Changed from single-width to double-width characters for better aspect ratio

### Architecture
- **Async Locks**: All shared mutable state protected by asyncio locks
- **Modular Design**: Clear separation of concerns between connection management, clipboard, macros, and gestures

### Fixed
- **Ctrl+C in `.exe`**: `SetConsoleCtrlHandler` now registered before
  `asyncio.run()`, resolving the PyInstaller SIGINT capture issue
- **`esc()` XSS encoding**: `&` was being replaced with itself (`"&"`) instead
  of `"&amp;"` — device names with `&` characters could corrupt rendered HTML
- **Double `visibilitychange` listener**: Was registered twice in
  `client/index.html`, causing a reconnect race condition on tab focus
- **Stale PIN cache on auth failure**: `sessionStorage.removeItem("pk_pin")`
  is now called on `auth_fail` — previously a wrong cached PIN would loop
  forever without user prompt
- **`tunnel_manager.py` missing imports**: `re`, `subprocess`, `platform`,
  `shutil`, `os`, `time`, `sys`, `json`, `Path`, `Optional`, `urlopen` were
  all used but not imported — file crashed on first import
- **`start_http_server` used module-level `ARGS`**: Refactored to accept port
  as a parameter, removing the module-level side-effect dependency

### Changed
- **Entry point**: `server.py` → `system.py` (update your run commands and
  shortcuts). `server.py` is now a pure server module accepting `args` as a
  parameter.
- **`phonekey.spec`**: Entry point updated from `server.py` to `system.py`
- **`_build_welcome_page`**: Replaced complex browser-chooser JavaScript
  (which didn't work on mobile) with a clean instant redirect to `/index.html`
- **QR code target**: Now encodes `/index.html` directly — no welcome page
  redirect on QR scan
- **`client/index.html` boot sequence**: `boot()` async function fetches
  `/api/config` before opening WebSocket — PIN overlay decision is now
  data-driven, not dependent on server HTML injection
- **`client/index.html` WebSocket URL**: Reads `ws_url` from `/api/config`
  response — uses tunnel WSS URL when provided, falls back to LAN calculation

### Removed
- **`logging_config.py`** — deleted; replaced by `logging_setup.py`
- **`logging_context.py`** — deleted; `LogContext`, `log_context`,
  `trace_span` were imported but never called in production code
- **`logging_formatter.py`** — deleted; `logging.Formatter()` is sufficient
- **`logging_handlers.py`** — deleted; `StreamHandler(sys.stdout)` is sufficient
- **`logging_schema.py`** — deleted; no structured log consumers exist
- **`logging_utils.py`** — deleted; `TimedLog`, `ExceptionLogger`,
  `StructuredLogger` were imported but never invoked
- **`log_ingestion.py`** — deleted; no log aggregation pipeline exists
- **`logging_design.md`** — deleted; documents infrastructure that no longer exists
- **`LOGGING.md`** — deleted; user-facing docs for deleted infrastructure
- **`test_logging.py`** — deleted; tests for deleted modules
- **`test_logging.py.bak`** — deleted; forgotten artifact, not tracked by git
- **Module-level side effects in `server.py`**: `parse_args()`, `ARGS =`,
  `_check_environment()`, `keyboard = KbController()`, `mouse = MsController()`
  no longer execute at import time — all initialisation happens inside `main(args)`

### Architecture
```
Before:  server.py (entry point + all logic) + 7 logging files
After:   system.py (entry point) + server.py (logic) + logging_setup.py (30 lines)
```

---

## [3.1.0] — 2026-04-23 — Direct Connection Release

### Added
- **Direct QR Connection**: Removed browser chooser page, direct connection via QR code scan
- **Tab-ID Deduplication**: Prevent duplicate connections from same browser tab using tab_id tracking
- **Enhanced SSL Certificate Reuse**: Improved certificate validation with IP address matching for better compatibility
- **UI Refinements**: Updated favicon, logo styling with icon, theme toggle size adjustment, footer text enhancement

### Fixed
- **PyInstaller Exclusions**: Removed problematic imports (`"email"`, `"urllib"`, `"html"`) causing build failures
- **Git Workflow**: Resolved tag conflict issues in release automation
- **Connection Metrics**: Ensured proper cleanup on disconnect
- **Duplicate Tab Connections**: Fixed race condition in device registration with atomic tab_id checking

### Changed
- **Version Updates**: Bumped client and README to v3.1.0 branding
- **Dependencies**: Minor updates to `cryptography` and `websockets` for stability
- **Connection Flow**: Simplified authentication flow - direct connection with optional PIN verification

---

## [3.0.0] — 2026-04-22 — Major Feature Release

### Added
- **Security & Authentication**:
  - 4-digit PIN authentication for secure connections
  - HTTPS/WSS support with auto-generated self-signed certificates
  - Connection deduplication preventing duplicate browser tabs

- **Advanced Input Control**:
  - Mouse trackpad with touch gestures (move, click, scroll)
  - Speed-adjustable mouse movement (0.1x - 5.0x)
  - Full modifier key support (Shift, Ctrl, Alt combinations)
  - Function keys F1-F12 in scrollable interface

- **Cross-Device Features**:
  - Clipboard synchronization (phone → laptop)
  - Multi-device support with device naming
  - Real-time device connection tracking and status

- **User Experience**:
  - Dark/light theme toggle with localStorage persistence
  - Tab-based UI: Keyboard, Mouse, Clipboard, Devices tabs
  - QR code terminal display for instant URL sharing
  - Responsive design optimized for mobile screens

- **CLI & Configuration**:
  - Command-line arguments: `--ws-port`, `--http-port`, `--https`, `--no-pin`, `--mouse-speed`
  - Environment variable configuration for all settings
  - Type hints throughout codebase for maintainability

- **Developer Features**:
  - Unit tests with `unittest` for core functionality
  - Connection metrics and active device counting
  - Comprehensive error handling and logging

- **Dependencies**:
  - `qrcode` for terminal QR code generation
  - `cryptography` for SSL certificate generation
  - `pyperclip` for cross-platform clipboard access

### Changed
- **UI Redesign**: Complete interface overhaul with modern tabbed layout
- **Architecture**: Enhanced WebSocket protocol with authentication and device management
- **Dependencies**: Added optional libraries for advanced features (fallback if not installed)
- **Configuration**: All settings now configurable via CLI args or environment variables

### Fixed
- **PyInstaller Builds**: Fixed module exclusion issues for standalone executables
- **Connection Handling**: Improved WebSocket reconnection and error recovery
- **Mobile Compatibility**: Better touch event handling and gesture recognition

### Architecture
- **Message Protocol**: Extended WebSocket messages for mouse, clipboard, and device management
- **Security Model**: PIN-based authentication with optional HTTPS encryption
- **Multi-Device**: Registry-based device tracking with real-time updates
- **Async Processing**: Enhanced asyncio patterns for mouse and clipboard operations

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