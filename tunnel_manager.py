"""
PhoneKey — Tunnel Manager  (tunnel_manager.py)
Downloads cloudflared ONCE into a persistent OS cache directory.
On Windows: subprocess runs with CREATE_NO_WINDOW so no extra terminal appears.
"""

import hashlib
import os
import platform
import re
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path
from typing import Optional

from logging_setup import get_logger

logger = get_logger("phonekey")

# ── Cloudflared binary URLs ───────────────────────────────────────────────────
_CF_URLS = {
    ("Windows", "AMD64"):  "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe",
    ("Windows", "x86"):    "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-386.exe",
    ("Linux",   "x86_64"): "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64",
    ("Linux",   "aarch64"):"https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-arm64",
    ("Darwin",  "x86_64"): "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-darwin-amd64.tgz",
    ("Darwin",  "arm64"):  "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-darwin-arm64.tgz",
}


def _cache_dir() -> Path:
    """
    Return a persistent OS-appropriate cache directory for cloudflared.
    The binary lives here permanently — never re-downloaded unless missing.

    Windows : %LOCALAPPDATA%\\PhoneKey\\cache
    macOS   : ~/Library/Caches/PhoneKey
    Linux   : ~/.cache/PhoneKey
    """
    system = platform.system()
    if system == "Windows":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    elif system == "Darwin":
        base = Path.home() / "Library" / "Caches"
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))

    cache = base / "PhoneKey"
    cache.mkdir(parents=True, exist_ok=True)
    return cache


def _binary_path() -> Path:
    system   = platform.system()
    machine  = platform.machine()
    ext      = ".exe" if system == "Windows" else ""
    return _cache_dir() / f"cloudflared{ext}"


def _subprocess_flags() -> dict:
    """
    On Windows, CREATE_NO_WINDOW prevents a black console window
    from appearing when the cloudflared child process starts.
    """
    flags = {}
    if sys.platform == "win32":
        CREATE_NO_WINDOW = 0x08000000
        flags["creationflags"] = CREATE_NO_WINDOW
    return flags


def _download_cloudflared(dest: Path) -> bool:
    """Download cloudflared to dest. Returns True on success."""
    system  = platform.system()
    machine = platform.machine()
    url     = _CF_URLS.get((system, machine))

    if not url:
        logger.error("❌ No cloudflared binary available for %s/%s", system, machine)
        return False

    logger.info("⬇️  Downloading cloudflared for %s/%s…", system, machine)
    logger.info("    URL: %s", url)

    try:
        tmp = dest.with_suffix(".tmp")
        with urllib.request.urlopen(url, timeout=60) as resp, \
             open(tmp, "wb") as f:
            total = int(resp.headers.get("Content-Length", 0))
            downloaded = 0
            while chunk := resp.read(65536):
                f.write(chunk)
                downloaded += len(chunk)
                if total:
                    pct = downloaded * 100 // total
                    logger.info("    %d%%…", pct) if pct % 20 == 0 else None

        # Handle macOS .tgz — extract the binary
        if url.endswith(".tgz"):
            import tarfile
            with tarfile.open(tmp) as tar:
                for member in tar.getmembers():
                    if "cloudflared" in member.name and not member.isdir():
                        f_in  = tar.extractfile(member)
                        dest.write_bytes(f_in.read())
                        break
            tmp.unlink(missing_ok=True)
        else:
            tmp.rename(dest)

        # Make executable on Unix
        if sys.platform != "win32":
            dest.chmod(0o755)

        logger.info("✅ cloudflared saved → %s", dest)
        return True

    except Exception as exc:
        logger.error("❌ Failed to download cloudflared: %s", exc)
        if dest.exists():
            dest.unlink(missing_ok=True)
        return False


def _ensure_cloudflared() -> Optional[Path]:
    """
    Return path to cloudflared binary.
    Downloads once; on subsequent runs uses the cached copy.
    """
    dest = _binary_path()
    if dest.exists() and dest.stat().st_size > 1_000_000:
        logger.info("✅ Using cached cloudflared → %s", dest)
        return dest

    logger.info("📦 cloudflared not found in cache — downloading…")
    if _download_cloudflared(dest):
        return dest
    return None


class TunnelManager:
    def __init__(self, local_port: int):
        self._port    = local_port
        self._proc: Optional[subprocess.Popen] = None
        self._url:  Optional[str] = None

    def start(self) -> Optional[str]:
        cf = _ensure_cloudflared()
        if cf is None:
            return None

        logger.info("🚀 Starting Cloudflare Quick Tunnel…")

        try:
            self._proc = subprocess.Popen(
                [str(cf), "tunnel", "--url", f"http://localhost:{self._port}"],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                **_subprocess_flags(),              # ← no console window
            )
        except Exception as exc:
            logger.error("❌ Failed to start cloudflared: %s", exc)
            return None

        # Parse tunnel URL from cloudflared output (timeout 30 s)
        url = self._read_tunnel_url(timeout=30)
        if url:
            self._url = url
            logger.info("✅ Tunnel ready: %s", url)
        else:
            logger.error("❌ Tunnel URL not found — cloudflared may have failed")
            self.stop()
        return url

    def _read_tunnel_url(self, timeout: int = 30) -> Optional[str]:
        deadline = time.monotonic() + timeout
        pattern  = re.compile(r"https://[a-z0-9\-]+\.trycloudflare\.com")
        while time.monotonic() < deadline:
            if self._proc is None or self._proc.poll() is not None:
                break
            line = self._proc.stdout.readline()
            if not line:
                time.sleep(0.1)
                continue
            match = pattern.search(line)
            if match:
                return match.group(0)
        return None

    def stop(self):
        if self._proc and self._proc.poll() is None:
            logger.info("🛑 Stopping Cloudflare tunnel…")
            self._proc.terminate()
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.kill()
            self._proc = None