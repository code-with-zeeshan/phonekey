"""
PhoneKey — Cloudflare Quick Tunnel Manager  (tunnel_manager.py)
Contract : Manage the cloudflared process lifecycle (find, download, start, stop).
           Returns the public tunnel URL; knows nothing about WebSocket or HTTP serving.
"""

import os
import platform
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional
from urllib.request import urlopen

from logging_setup import get_logger

logger = get_logger("phonekey.tunnel")

# ─────────────────────────────────────────────
#  Constants
# ─────────────────────────────────────────────
CLOUDFLARED_BIN_NAME    = "cloudflared"
TUNNEL_URL_REGEX        = re.compile(r"https://[a-zA-Z0-9\-]+\.trycloudflare\.com")
CLOUDFLARED_REPO        = "cloudflare/cloudflared"
CLOUDFLARED_API_URL     = f"https://api.github.com/repos/{CLOUDFLARED_REPO}/releases/latest"
CLOUDFLARED_DOWNLOAD_BASE = (
    "https://github.com/cloudflare/cloudflared/releases/latest/download"
)


class TunnelManager:
    """Manages the Cloudflare Quick Tunnel process lifecycle."""

    def __init__(self, local_port: int) -> None:
        self.local_port:  int                      = local_port
        self.process:     Optional[subprocess.Popen] = None
        self.tunnel_url:  Optional[str]              = None
        self.binary_path: Optional[Path]             = None

    def _get_bin_dir() -> Path:
        """
        Return the persistent bin/ directory next to the .exe or script.
        
        PyInstaller sets sys.frozen=True and sys.executable to the actual
        .exe path. Using sys.executable.parent means the bin/ folder sits
        permanently next to phonekey.exe — never wiped between runs.
        
        In script mode, Path(__file__).parent is correct as before.
        """
        if getattr(sys, "frozen", False):
            # Running as .exe — use folder containing the .exe itself
            exe_dir = Path(sys.executable).parent
        else:
            # Running as script — use folder containing tunnel_manager.py
            exe_dir = Path(__file__).parent
        
        bin_dir = exe_dir / "bin"
        bin_dir.mkdir(parents=True, exist_ok=True)
        return bin_dir    

    # ── Binary discovery ──────────────────────────────────────────────────

    def find_binary(self) -> Optional[Path]:
        """Search CWD → bin/ → PATH for cloudflared (any platform variant)."""
        bin_dir = _get_bin_dir()

        # Build list of names to search: bare name + platform-specific name
        platform_name = self.get_platform_specific_name()  # e.g. cloudflared-windows-amd64.exe
        search_names  = list(dict.fromkeys([           # preserve order, deduplicate
            CLOUDFLARED_BIN_NAME,                      # "cloudflared"
            platform_name,                             # "cloudflared-windows-amd64.exe"
            CLOUDFLARED_BIN_NAME + ".exe",             # "cloudflared.exe" (Windows fallback)
        ]))

        # Search CWD and bin/ for each candidate name
        for name in search_names:
            for directory in (Path.cwd(), bin_dir):
                candidate = directory / name
                if candidate.is_file() and os.access(candidate, os.X_OK):
                    logger.debug("Found cloudflared: %s", candidate)
                    return candidate

        # Search PATH
        found = shutil.which(CLOUDFLARED_BIN_NAME)
        if found:
            p = Path(found)
            if p.is_file() and os.access(p, os.X_OK):
                logger.debug("Found cloudflared in PATH: %s", p)
                return p

        logger.warning("cloudflared binary not found")
        return None

    def get_os_arch_suffix(self) -> Optional[str]:
        """Map this system to cloudflared's release asset naming convention."""
        system  = platform.system().lower()
        machine = platform.machine().lower()
        arch_map = {
            "x86_64": "amd64", "amd64": "amd64",
            "arm64": "arm64", "aarch64": "arm64",
            "i386": "386",
        }
        arch = arch_map.get(machine, machine)
        if system == "windows": return f"windows-{arch}.exe"
        if system == "darwin":  return f"darwin-{arch}.tgz"
        if system == "linux":   return f"linux-{arch}"
        return None

    def get_platform_specific_name(self) -> str:
        """Return the local binary filename including platform suffix."""
        suffix = self.get_os_arch_suffix() or "unknown"
        return f"{CLOUDFLARED_BIN_NAME}-{suffix}"

    def download_binary(self, binary_name: str) -> Optional[Path]:
        """Download cloudflared from GitHub releases using the API to find the right asset."""
        suffix = self.get_os_arch_suffix()
        if not suffix:
            logger.error("Unsupported OS/architecture for cloudflared download")
            return None

        bin_dir = _get_bin_dir()

        # Check for already-downloaded binary before hitting GitHub
        for existing_name in (binary_name, "cloudflared", "cloudflared.exe"):
            existing = bin_dir / existing_name
            if existing.exists() and os.access(existing, os.X_OK):
                logger.info("Using existing cloudflared: %s", existing)
                return existing

        logger.info("Checking GitHub for latest cloudflared release…")
        binary_path = None
        try:
            req  = urlopen(CLOUDFLARED_API_URL, timeout=15)
            data = json.loads(req.read().decode())
            assets = data.get("assets", [])

            download_url = None
            for asset in assets:
                name = asset.get("name", "")
                if name.endswith(suffix):
                    download_url = asset.get("browser_download_url")
                    binary_name  = name
                    break

            if not download_url:
                logger.error("No matching binary for %s in latest release", suffix)
                return None

            binary_path = bin_dir / binary_name
            logger.info("Downloading %s…", binary_name)

            req = urlopen(download_url, timeout=120)
            with open(binary_path, "wb") as f:
                total      = int(req.headers.get("Content-Length", 0))
                downloaded = 0
                while chunk := req.read(8192):
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total and downloaded % (total // 5 + 1) < 8192:
                        pct = downloaded * 100 // total
                        logger.info("    Downloading… %d%%", pct)
            binary_path.chmod(0o755)
            logger.info("✅ Downloaded cloudflared → %s", binary_path)

            # Extract .tgz (macOS)
            if binary_name.endswith(".tgz"):
                import tarfile
                with tarfile.open(binary_path, "r:gz") as tar:
                    tar.extractall(bin_dir)
                extracted = bin_dir / "cloudflared"
                if extracted.exists():
                    extracted.chmod(0o755)
                    return extracted

            return binary_path

        except Exception as exc:
            logger.error("Failed to download cloudflared: %s", exc)
            logger.error(
                "Download manually from https://github.com/cloudflare/cloudflared/releases"
            )
            if "binary_path" and binary_path.exists():
                try:
                    binary_path.unlink()
                except Exception:
                    pass
            return None

    # ── Lifecycle ─────────────────────────────────────────────────────────

    def start(self) -> Optional[str]:
        """Start the tunnel; return the public HTTPS URL or None on failure."""
        self.binary_path = self.find_binary()
        if not self.binary_path:
            logger.info("cloudflared not found — attempting auto-download…")
            self.binary_path = self.download_binary(self.get_platform_specific_name())
        if not self.binary_path:
            logger.error(
                "cloudflared unavailable. "
                "Download from https://github.com/cloudflare/cloudflared/releases"
            )
            return None

        cmd = [str(self.binary_path), "tunnel", "--url",
               f"http://localhost:{self.local_port}"]
        logger.info("🚀 Starting Cloudflare Quick Tunnel…")

        # ── Windows: prevent a black console window for the child process ─────
        extra_kwargs = {}
        if sys.platform == "win32":
            extra_kwargs["creationflags"] = 0x08000000  # CREATE_NO_WINDOW

        try:
            self.process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL, text=True, bufsize=1,
                **extra_kwargs,
            )
            deadline = time.time() + 30
            while time.time() < deadline:
                if self.process.poll() is not None:
                    out = self.process.stdout.read() if self.process.stdout else ""
                    logger.error("cloudflared exited early: %s", out)
                    self._cleanup()
                    return None
                line = self.process.stdout.readline() if self.process.stdout else ""
                if line:
                    logger.debug("cloudflared: %s", line.strip())
                    m = TUNNEL_URL_REGEX.search(line)
                    if m:
                        self.tunnel_url = m.group(0)
                        logger.info("✅ Tunnel ready: %s", self.tunnel_url)
                        return self.tunnel_url
                    if "error" in line.lower() or "failed" in line.lower():
                        logger.error("cloudflared error: %s", line.strip())
                time.sleep(0.1)

            logger.error("Timeout waiting for tunnel URL")
            self._cleanup()
            return None

        except FileNotFoundError:
            logger.error("cloudflared binary not found or not executable")
            return None
        except Exception as exc:
            logger.error("Failed to start tunnel: %s", exc)
            self._cleanup()
            return None

    def stop(self) -> None:
        logger.info("🛑 Stopping Cloudflare tunnel…")
        self._cleanup()

    def _cleanup(self) -> None:
        if self.process:
            try:
                self.process.terminate()
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                logger.warning("Force-killing cloudflared")
                self.process.kill()
                self.process.wait()
            except Exception as exc:
                logger.debug("Error terminating process: %s", exc)
            finally:
                self.process = None
        self.tunnel_url = None

    def is_running(self) -> bool:
        return self.process is not None and self.process.poll() is None


# ── Module-level convenience ──────────────────────────────────────────────────

def create_tunnel(local_port: int) -> Optional[TunnelManager]:
    """Create, start, and return a TunnelManager, or None on failure."""
    mgr = TunnelManager(local_port)
    return mgr if mgr.start() else None


# ── CLI self-test ──────────────────────────────────────────────────────────────
import json   # needed here for download_binary; also fine at top-level

if __name__ == "__main__":
    from logging_setup import setup_logging
    setup_logging("DEBUG")
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8080
    print(f"Starting tunnel to localhost:{port}…")
    mgr = create_tunnel(port)
    if mgr and mgr.tunnel_url:
        print(f"\nTunnel URL: {mgr.tunnel_url}")
        try:
            while mgr.is_running():
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        finally:
            mgr.stop()
    else:
        print("Failed to start tunnel")
        sys.exit(1)