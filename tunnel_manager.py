"""
Cloudflare Quick Tunnels Manager for PhoneKey

Provides secure public HTTPS URLs via Cloudflare Quick Tunnels without requiring
domain names or SSL certificates. Creates a temporary tunnel that forwards traffic
to the local PhoneKey server.

Automatically downloads cloudflared binary if not found.
"""

# Centralized logging
from logging_config import get_logger

logger = get_logger("phonekey.tunnel")


# Cloudflare Quick Tunnels binary name
CLOUDFLARED_BIN_NAME = "cloudflared"

# Regex to extract the tunnel URL from cloudflared output
TUNNEL_URL_REGEX = re.compile(r"https://[a-zA-Z0-9\-]+\.trycloudflare\.com")

# Cloudflare releases
CLOUDFLARED_REPO = "cloudflare/cloudflared"
CLOUDFLARED_API_URL = f"https://api.github.com/repos/{CLOUDFLARED_REPO}/releases/latest"
CLOUDFLARED_DOWNLOAD_BASE = "https://github.com/cloudflare/cloudflared/releases/latest/download"


class TunnelManager:
    """Manages Cloudflare Quick Tunnel lifecycle."""

    def __init__(self, local_port: int):
        """
        Initialize the tunnel manager.

        Args:
            local_port: The local HTTP port to tunnel (e.g., 8080)
        """
        self.local_port = local_port
        self.process: Optional[subprocess.Popen] = None
        self.tunnel_url: Optional[str] = None
        self.binary_path: Optional[Path] = None

    def find_binary(self) -> Optional[Path]:
        """
        Locate the cloudflared binary.

        Search order:
        1. Current directory
        2. Binary directory next to server.py
        3. System PATH

        Returns:
            Path to the binary if found, None otherwise.
        """
        # Check current working directory
        cwd_path = Path.cwd() / CLOUDFLARED_BIN_NAME
        if cwd_path.is_file() and os.access(cwd_path, os.X_OK):
            logger.debug("Found cloudflared in CWD: %s", cwd_path)
            return cwd_path

        # Check binary directory (next to server.py)
        base_dir = Path(__file__).parent
        bin_dir = base_dir / "bin"
        if bin_dir.exists():
            bin_path = bin_dir / CLOUDFLARED_BIN_NAME
            if bin_path.is_file() and os.access(bin_path, os.X_OK):
                logger.debug("Found cloudflared in bin/: %s", bin_path)
                return bin_path

        # Check system PATH
        system_path = shutil.which(CLOUDFLARED_BIN_NAME)
        if system_path:
            path_obj = Path(system_path)
            if path_obj.is_file() and os.access(path_obj, os.X_OK):
                logger.debug("Found cloudflared in PATH: %s", path_obj)
                return path_obj

        logger.warning("cloudflared binary not found")
        return None

    def get_platform_specific_name(self) -> str:
        """
        Get the platform-specific binary name.

        Returns:
            Binary name with platform suffix (e.g., cloudflared-linux-amd64)
        """
        system = platform.system().lower()
        machine = platform.machine().lower()

        # Normalize architecture names
        if machine in ("x86_64", "amd64", "x64"):
            arch = "amd64"
        elif machine in ("aarch64", "arm64"):
            arch = "arm64"
        elif machine in ("armv7l", "armv8l"):
            arch = "arm"
        else:
            arch = machine

        if system == "windows":
            return f"{CLOUDFLARED_BIN_NAME}-windows-{arch}.exe"
        elif system == "darwin":
            return f"{CLOUDFLARED_BIN_NAME}-darwin-{arch}"
        elif system == "linux":
            return f"{CLOUDFLARED_BIN_NAME}-linux-{arch}"
        else:
            return f"{CLOUDFLARED_BIN_NAME}-{system}-{arch}"

    def get_os_arch_suffix(self) -> Optional[str]:
        """
        Maps local system info to cloudflared's release naming convention.
        
        Returns:
            Suffix string (e.g., "linux-amd64", "windows-amd64.exe", "darwin-arm64.tgz")
            or None if unsupported.
        """
        system = platform.system().lower()
        machine = platform.machine().lower()
        
        # Map CPU architectures
        arch_map = {
            "x86_64": "amd64",
            "amd64": "amd64",
            "arm64": "arm64",
            "aarch64": "arm64",
            "i386": "386",
        }
        arch = arch_map.get(machine, machine)
        
        # Map Operating Systems to binary suffixes
        if system == "windows":
            return f"windows-{arch}.exe"
        elif system == "darwin":  # macOS
            return f"darwin-{arch}.tgz"
        elif system == "linux":
            return f"linux-{arch}"
        return None

    def download_binary(self, binary_name: str) -> Optional[Path]:
        """
        Download cloudflared binary from GitHub releases.
        Uses GitHub API to find the correct asset for this platform.

        Args:
            binary_name: Platform-specific binary name (unused, derived from system)

        Returns:
            Path to downloaded binary if successful, None otherwise.
        """
        suffix = self.get_os_arch_suffix()
        if not suffix:
            logger.error("Unsupported OS or architecture for cloudflared download")
            return None
        
        bin_dir = Path(__file__).parent / "bin"
        bin_dir.mkdir(exist_ok=True)
        
        # Check for existing binary first
        existing = bin_dir / binary_name
        if existing.exists() and os.access(existing, os.X_OK):
            logger.info("Using existing cloudflared binary: %s", existing)
            return existing
        
        # Also check for extracted binary (macOS/Linux)
        extracted_name = binary_name.replace(".exe", "").replace(".tgz", "")
        extracted_path = bin_dir / extracted_name
        if extracted_path.exists() and os.access(extracted_path, os.X_OK):
            logger.info("Using existing extracted cloudflared: %s", extracted_path)
            return extracted_path

        # Query GitHub API for latest release assets
        logger.info("Checking GitHub for latest cloudflared release...")
        try:
            import json
            req = urlopen(CLOUDFLARED_API_URL, timeout=15)
            data = json.loads(req.read().decode())
            assets = data.get("assets", [])
            
            # Find the asset that matches our system suffix
            download_url = None
            for asset in assets:
                name = asset.get("name", "")
                if name.endswith(suffix):
                    download_url = asset.get("browser_download_url")
                    binary_name = name
                    break
            
            if not download_url:
                logger.error("Could not find matching binary for %s in latest release", suffix)
                logger.error("Available assets: %s", [a.get("name") for a in assets])
                return None
            
            binary_path = bin_dir / binary_name
            logger.info("Downloading %s...", binary_name)
            
            # Download with streaming to handle large files
            req = urlopen(download_url, timeout=120)
            with open(binary_path, 'wb') as f:
                while True:
                    chunk = req.read(8192)
                    if not chunk:
                        break
                    f.write(chunk)
            
            # Make executable
            binary_path.chmod(0o755)
            logger.info("✅ Downloaded cloudflared to %s", binary_path)
            
            # Extract if it's an archive (macOS .tgz)
            if binary_name.endswith(".tgz"):
                logger.info("Extracting archive...")
                import tarfile
                with tarfile.open(binary_path, 'r:gz') as tar:
                    tar.extractall(bin_dir)
                # The extracted binary is usually named "cloudflared"
                extracted_path = bin_dir / "cloudflared"
                if extracted_path.exists():
                    extracted_path.chmod(0o755)
                    logger.info("✅ Extracted to %s", extracted_path)
                    return extracted_path
            
            return binary_path
            
        except Exception as e:
            logger.error("Failed to download cloudflared: %s", e)
            logger.error("Please download manually from https://github.com/cloudflare/cloudflared/releases")
            # Cleanup partial download
            if 'binary_path' in locals() and binary_path.exists():
                try:
                    binary_path.unlink()
                except:
                    pass
            return None

    def start(self) -> Optional[str]:
        """
        Start the Cloudflare Quick Tunnel.

        Returns:
            The tunnel URL if successful, None otherwise.
        """
        # Find the binary
        self.binary_path = self.find_binary()
        
        # Auto-download if not found
        if not self.binary_path:
            logger.info("cloudflared not found, attempting to download...")
            binary_name = self.get_platform_specific_name()
            self.binary_path = self.download_binary(binary_name)
            
        if not self.binary_path:
            logger.error(
                "cloudflared not available. "
                "Download from https://github.com/cloudflare/cloudflared/releases "
                "or it will be auto-downloaded on next attempt."
            )
            return None

        # Build the command
        cmd = [
            str(self.binary_path),
            "tunnel",
            "--url",
            f"http://localhost:{self.local_port}",
        ]

        logger.info("🚀 Starting Cloudflare Quick Tunnel...")
        logger.debug("Command: %s", " ".join(cmd))

        try:
            self.process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                text=True,
                bufsize=1,
            )

            # Wait for tunnel URL in output
            start_time = time.time()
            timeout = 30  # seconds

            while time.time() - start_time < timeout:
                if self.process.poll() is not None:
                    # Process exited
                    output = self.process.stdout.read() if self.process.stdout else ""
                    logger.error("cloudflared exited early: %s", output)
                    self._cleanup()
                    return None

                line = self.process.stdout.readline() if self.process.stdout else ""
                if line:
                    logger.debug("cloudflared: %s", line.strip())

                    # Check for tunnel URL
                    match = TUNNEL_URL_REGEX.search(line)
                    if match:
                        self.tunnel_url = match.group(0)
                        logger.info("✅ Tunnel ready: %s", self.tunnel_url)
                        return self.tunnel_url

                    # Check for errors
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
        """Stop the tunnel and clean up."""
        logger.info("🛑 Stopping Cloudflare tunnel...")
        self._cleanup()

    def _cleanup(self) -> None:
        """Clean up the tunnel process."""
        if self.process:
            try:
                self.process.terminate()
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                logger.warning("Force killing cloudflared process")
                self.process.kill()
                self.process.wait()
            except Exception as exc:
                logger.debug("Error terminating process: %s", exc)
            finally:
                self.process = None

        self.tunnel_url = None

    def is_running(self) -> bool:
        """Check if the tunnel is running."""
        return self.process is not None and self.process.poll() is None


def create_tunnel(local_port: int) -> Optional[TunnelManager]:
    """
    Create and start a Cloudflare Quick Tunnel.

    Args:
        local_port: The local HTTP port to tunnel

    Returns:
        TunnelManager instance if successful, None otherwise.
    """
    manager = TunnelManager(local_port)
    url = manager.start()
    if url:
        return manager
    return None


if __name__ == "__main__":
    # Simple CLI test
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8080
    print(f"Starting tunnel to localhost:{port}...")

    manager = create_tunnel(port)
    if manager and manager.tunnel_url:
        print(f"\nTunnel URL: {manager.tunnel_url}")
        print("Press Ctrl+C to stop...")
        try:
            while manager.is_running():
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        finally:
            manager.stop()
    else:
        print("Failed to start tunnel")
        sys.exit(1)
