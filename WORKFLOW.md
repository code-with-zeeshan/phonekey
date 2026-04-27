# PhoneKey — Development Workflow

This document covers the development setup, project structure, build process,
release procedure, and coding conventions.

---

## 📁 Repository Layout

```
phonekey/
├── system.py              # ENTRY POINT — run this, not server.py
├── server.py              # Core async server (WebSocket + HTTP + input)
├── tunnel_manager.py      # Cloudflare cloudflared process manager
├── logging_setup.py       # Logging — setup_logging() + get_logger()
├── config.py              # Config loader (reads config.json + env overrides)
├── config.json            # Config schema, defaults, and environment profiles
│
├── client/
│   ├── index.html         # Phone SPA — the entire client-side application
│   ├── phonekey.ico       # Multi-resolution favicon (ICO format)
│   └── phonekey.svg       # Vector icon
│
├── test_server.py         # All tests live here
├── phonekey.spec          # PyInstaller build spec (entry: system.py)
│
├── requirements.txt       # Runtime dependencies
├── requirements-dev.txt   # Dev + build (includes pyinstaller, ruff)
│
├── .github/
│   └── workflows/
│       └── release.yml    # Build + release automation
│
└── docs/
    ├── README.md
    ├── CHANGELOG.md
    ├── WORKFLOW.md         # ← you are here
    ├── CONTRIBUTING.md
    └── SECURITY.md
```

---

## 🔧 Development Setup

```bash
# 1. Clone
git clone https://github.com/yourusername/phonekey.git
cd phonekey

# 2. Create virtual environment
python -m venv .venv

# Windows
.venv\Scripts\activate

# macOS / Linux
source .venv/bin/activate

# 3. Install all dependencies (runtime + dev)
pip install -r requirements-dev.txt

# 4. Run in development mode
python system.py
```

---

## 📐 Module Contracts

Each module has a single, clearly bounded responsibility.
Violating these boundaries creates the exact duplication + drift the
architecture is designed to prevent.

### `system.py` — Entry Point
**Owns:** CLI argument parsing, interactive TUI, instance locking,
logging bootstrap, Windows Ctrl+C registration, `asyncio.run()`.

**Must NOT:** contain any server logic, WebSocket code, or HTTP handlers.

```python
# Correct usage
from server import main
asyncio.run(main(args))   # passes fully resolved Namespace
```

### `server.py` — Core Server
**Owns:** WebSocket handler, HTTP handler + `/api/config`, device registry,
key/mouse injection, SSL certificate, QR code, startup banner, signal setup.

**Must NOT:** call `parse_args()`, `sys.exit()` at module level, or
initialise pynput at import time. All setup happens inside `main(args)`.

```python
# Correct — lazy initialisation inside main()
async def main(args: Namespace) -> None:
    keyboard = KbController()   # ← inside main(), not module level
```

### `tunnel_manager.py` — Tunnel Lifecycle
**Owns:** Finding, downloading, and running the `cloudflared` binary.
Returns the public tunnel URL. Cleans up the process on stop.

**Must NOT:** serve HTTP, handle WebSocket connections, or know about
PhoneKey's internal routing.

### `logging_setup.py` — Logging
**Owns:** `setup_logging(level)` and `get_logger(name)`. Nothing else.

```python
# Every module that needs a logger does exactly this:
from logging_setup import get_logger
logger = get_logger("phonekey.mymodule")
```

**Must NOT:** contain application logic, import server modules, or
configure anything other than the `phonekey` logger hierarchy.

### `config.py` — Configuration
**Owns:** Loading `config.json`, applying environment overrides,
exposing values via `.get("dot.path")` and backward-compat properties.

**Must NOT:** hold runtime state (PIDs, active connections, session PIN).

### `client/index.html` — Browser Client
**Owns:** All phone-side UI, WebSocket client, gesture handling.
Reads server config from `GET /api/config` on boot.

**Must NOT:** rely on server-injected variables (the old `SESSION_PIN`
string-replace approach is removed). All server-to-client config flows
through `/api/config`.

---

## 🧪 Testing

```bash
# Run all tests
python -m pytest test_server.py -v

# Run with coverage (requires pytest-cov)
python -m pytest test_server.py -v --cov=server --cov-report=term-missing
```

### Test Structure

```
TestClientFiles          — validates client/ directory and file formats
TestServerComponents     — tests device registry and duplicate tab detection
                           (auto-skipped if pynput not available)
```

### Adding Tests

- Add new test classes to `test_server.py`
- Do NOT import `server` at module level — always inside `setUpClass`
  with `sys.argv = ['test']` guard (server no longer does module-level
  side effects, but the guard is still good practice)
- Tests that require real hardware (keyboard, mouse) belong in manual
  testing — keep the automated suite CI-friendly

---

## 🏗️ Building Binaries

```bash
# Install build dependencies
pip install -r requirements-dev.txt

# Build for current platform
pyinstaller phonekey.spec

# Output
dist/phonekey        # Linux / macOS
dist/phonekey.exe    # Windows
```

### What `phonekey.spec` Bundles

| Item | Destination in binary |
|---|---|
| `system.py` | Entry point |
| `server.py` | Included automatically |
| `tunnel_manager.py` | Included automatically |
| `logging_setup.py` | Included automatically |
| `client/index.html` | `client/` directory |
| `client/phonekey.ico` | `client/` directory |
| pynput backends | Hidden imports (all platforms) |

`client/phonekey.svg` is intentionally excluded — the `.ico` covers all
favicon use cases and the SVG is only needed in the repo for reference.

### Testing the Binary

```bash
# Rename with platform suffix (matches release convention)
mv dist/phonekey dist/phonekey-linux   # Linux
mv dist/phonekey dist/phonekey-macos   # macOS
mv dist/phonekey.exe dist/phonekey-windows.exe  # Windows

# Smoke test
./dist/phonekey-linux --yes --no-pin
```

---

## 🚀 Release Process

### Version Bump Checklist

Before tagging a release, update the version string in **all** of these
locations (find with `grep -r "3\." --include="*.py" --include="*.html"
--include="*.json" --include="*.md"`):

```
system.py          __version__ = "X.Y.Z"
server.py          __version__ = "X.Y.Z"
client/index.html  PhoneKey vX.Y.Z  (footer × 4 tabs)
config.json        "version": { "default": "X.Y.Z" }
CHANGELOG.md       new entry at top
```

### Tagging and Release

```bash
# 1. Commit everything
git add -A
git commit -m "chore: bump version to v3.2.0"

# 2. Tag
git tag v3.2.0
git push origin main --tags
```

GitHub Actions (`release.yml`) automatically:
1. Builds Windows `.exe`, macOS binary, and Linux binary in parallel
2. Downloads all three artifacts
3. Creates a GitHub Release named `PhoneKey v3.2.0`
4. Attaches all three binaries
5. Uses `CHANGELOG.md` as the release body

### Versioning Convention

Follows [Semantic Versioning](https://semver.org/):

| Increment | When |
|---|---|
| **MAJOR** (X) | Breaking change to CLI flags, WebSocket protocol, or file layout |
| **MINOR** (Y) | New feature, backward-compatible |
| **PATCH** (Z) | Bug fix, documentation, dependency update |

---

## 🔍 Code Quality

```bash
# Lint
ruff check .

# Format check
ruff format --check .

# Auto-fix
ruff check --fix .
ruff format .
```

Ruff is configured in `requirements-dev.txt`. No separate `pyproject.toml`
or `setup.cfg` is needed for a project of this scope.

---

## 🛠️ Common Development Tasks

### Change the WebSocket port default
Edit `_DEFAULT_WS_PORT` in `system.py` and `DEFAULT_WS_PORT` in `server.py`.
Also update `config.json` → `network.websocket.port.default`.

### Add a new keyboard shortcut button to the phone UI
Edit `client/index.html`. Add a `<button>` in the Quick Keys grid with
`onclick="sendSpecial('KeyName')"`. Add the key mapping in `server.py`
→ `SPECIAL_KEY_MAP` inside `_build_key_maps()` if it's not already there.

### Add a new WebSocket message type
1. Handle it in `ws_handler()` in `server.py`
2. Send it from `client/index.html` via `sendPayload({ action: "my_action", ... })`
3. Add a test case in `test_server.py`

### Add a new `/api/*` endpoint
Add a new `elif self.path == "/api/something":` block in
`PhoneKeyHTTPHandler.do_GET()` in `server.py`.

### Change logging verbosity at runtime
```bash
python system.py --log-level DEBUG
```

---

## ⚠️ Common Pitfalls

| Pitfall | Consequence | Fix |
|---|---|---|
| Putting logic in `server.py` at module level | Crashes test imports, breaks `from server import main` | Move inside `main(args)` |
| Adding a 7th logging file | Drift, complexity, unused imports | Everything goes in `logging_setup.py` |
| Injecting variables into `index.html` via string replace | Brittle — breaks on any rename | Use `/api/config` endpoint |
| Hardcoding `SESSION_PIN` in client JS | Security risk, caching bugs | Let client fetch from `/api/config` |
| Using `ARGS` global in `server.py` | Module-level side effect, untestable | Access through `main(args)` parameter |
| Adding back `logging_context.py` | Re-introduces unused complexity | PhoneKey is single-process; context managers add nothing |