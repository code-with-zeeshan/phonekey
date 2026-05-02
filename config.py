"""
PhoneKey Configuration Loader

Loads configuration with environment-specific overrides.
Provides backward compatibility with hardcoded constants.
"""

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional


DEFAULT_CONFIG = {
    "application": {"version": "3.2.0", "name": "PhoneKey"},
    "network": {
        "websocket": {"port": 8765, "ping_interval": 30, "ping_timeout": 60},
        "http": {"port": 8080, "host": "0.0.0.0"},
        "connection_timeout": 30
    },
    "input": {
        "keyboard": {"key_inject_delay": 0.012},
        "mouse": {"speed_multiplier": 1.0}
    },
    "security": {
        "authentication": {
            "pin": {"enabled": True, "length": 4},
            "tls": {"enabled": False, "certificate_validity_days": 365}
        },
        "tunnel": {
            "enabled": False,
            "provider": "cloudflare",
            "api_url": "https://api.github.com/repos/cloudflare/cloudflared/releases/latest",
            "download_base_url": "https://github.com/cloudflare/cloudflared/releases/latest/download",
            "binary_name": "cloudflared"
        }
    },
    "filesystem": {
        "directories": {"client": "client", "bin": "bin", "certificates": "."},
        "files": {"certificate": "phonekey-cert.pem", "private_key": "phonekey-key.pem"}
    },
    "platform": {
        "windows": {
            "ctrl_events": {"ctrl_c": 0, "ctrl_close": 2},
            "ignored_errors": [10053, 10054, 10058]
        },
        "architectures": {"x86_64": "amd64", "arm64": "arm64", "arm": "arm"}
    },
    "cloud": {
        "github": {
            "api_url": "https://api.github.com/repos/cloudflare/cloudflared/releases/latest",
            "download_timeout": 120,
            "api_timeout": 15
        }
    },
    "environments": {
        "development": {
            "network.websocket.port": 8765,
            "network.http.port": 8080,
            "security.authentication.pin.enabled": False,
            "security.tunnel.enabled": False
        },
        "production": {
            "network.websocket.port": 8765,
            "network.http.port": 8080,
            "security.authentication.pin.enabled": True,
            "security.tunnel.enabled": True
        },
        "testing": {
            "network.websocket.port": 9999,
            "network.http.port": 9998,
            "security.authentication.pin.enabled": False,
            "security.tunnel.enabled": False,
            "input.keyboard.key_inject_delay": 0.001
        }
    },
    "validation": {
        "port_range": {"min": 1024, "max": 65535},
        "speed_multiplier_range": {"min": 0.1, "max": 5.0},
        "key_delay_range": {"min": 0.001, "max": 1.0}
    }
}


class Config:
    """Configuration manager for PhoneKey."""

    def __init__(self, config_file: Optional[Path] = None, environment: str = "development"):
        """
        Initialize configuration.

        Args:
            config_file: Path to config.json file (unused, kept for compatibility)
            environment: Environment name (development/production/testing)
        """
        self.environment = environment
        self._config = self._load_config()

    def _load_config(self) -> Dict[str, Any]:
        """Load configuration from defaults."""
        config = DEFAULT_CONFIG.copy()
        # Apply environment-specific overrides
        self._apply_environment_overrides(config)
        return config

    def _apply_environment_overrides(self, config: Dict[str, Any]) -> None:
        """Apply environment-specific configuration overrides."""
        env = self.environment
        if "environments" in config and env in config["environments"]:
            env_config = config["environments"][env]
            # Apply dot-notation overrides (e.g., "network.websocket.port")
            for key, value in env_config.items():
                if isinstance(key, str) and "." in key and not key.startswith("/*"):
                    self._set_nested(config, key, value)

    def _set_nested(self, config: Dict[str, Any], path: str, value: Any) -> None:
        """Set nested dictionary value using dot notation."""
        keys = path.split(".")
        target = config
        for key in keys[:-1]:
            if key not in target:
                target[key] = {}
            target = target[key]
        target[keys[-1]] = value

    def get(self, path: str, default: Any = None) -> Any:
        """
        Get configuration value using dot notation.

        Args:
            path: Dot-separated path (e.g., "network.websocket.port")
            default: Default value if path not found

        Returns:
            Configuration value
        """
        keys = path.split(".")
        value = self._config
        for key in keys:
            if isinstance(value, dict) and key in value:
                value = value[key]
            else:
                return default
        return value

    # Backward compatibility properties
    @property
    def DEFAULT_WS_PORT(self) -> int:
        """WebSocket port (backward compatibility)."""
        return self.get("network.websocket.port", 8765)

    @property
    def DEFAULT_HTTP_PORT(self) -> int:
        """HTTP port (backward compatibility)."""
        return self.get("network.http.port", 8080)

    @property
    def KEY_INJECT_DELAY(self) -> float:
        """Keystroke delay (backward compatibility)."""
        return self.get("input.keyboard.key_inject_delay", 0.012)

    @property
    def WS_PING_INTERVAL(self) -> int:
        """WebSocket ping interval (backward compatibility)."""
        return self.get("network.websocket.ping_interval", 30)

    @property
    def WS_PING_TIMEOUT(self) -> int:
        """WebSocket ping timeout (backward compatibility)."""
        return self.get("network.websocket.ping_timeout", 60)

    @property
    def CLOUDFLARED_API_URL(self) -> str:
        """Cloudflared API URL (backward compatibility)."""
        return self.get("cloud.github.api_url",
                       "https://api.github.com/repos/cloudflare/cloudflared/releases/latest")

    @property
    def CLOUDFLARED_DOWNLOAD_BASE(self) -> str:
        """Cloudflared download base URL (backward compatibility)."""
        return self.get("security.tunnel.download_base_url",
                       "https://github.com/cloudflare/cloudflared/releases/latest/download")

    @property
    def CLOUDFLARED_REPO(self) -> str:
        """Cloudflared repository (backward compatibility)."""
        return "cloudflare/cloudflared"

    def __repr__(self) -> str:
        return f"Config(environment='{self.environment}')"


# Global configuration instance
_config_instance: Optional[Config] = None


def get_config(config_file: Optional[Path] = None, environment: str = "development") -> Config:
    """
    Get or create global configuration instance.

    Args:
        config_file: Path to config.json file (kept for compatibility)
        environment: Environment name

    Returns:
        Config instance
    """
    global _config_instance
    if _config_instance is None:
        _config_instance = Config(config_file, environment)
    return _config_instance


def validate_config(config: Config) -> list:
    """
    Validate configuration values.

    Args:
        config: Config instance to validate

    Returns:
        List of validation error messages (empty if valid)
    """
    errors = []

    # Validate port ranges
    ws_port = config.get("network.websocket.port")
    http_port = config.get("network.http.port")
    port_min = config.get("validation.port_range.min", 1024)
    port_max = config.get("validation.port_range.max", 65535)

    if not (port_min <= ws_port <= port_max):
        errors.append(f"WebSocket port {ws_port} out of range [{port_min}, {port_max}]")
    if not (port_min <= http_port <= port_max):
        errors.append(f"HTTP port {http_port} out of range [{port_min}, {port_max}]")

    # Validate speed multiplier
    speed = config.get("input.mouse.speed_multiplier")
    speed_min = config.get("validation.speed_multiplier_range.min", 0.1)
    speed_max = config.get("validation.speed_multiplier_range.max", 5.0)
    if not (speed_min <= speed <= speed_max):
        errors.append(f"Mouse speed {speed} out of range [{speed_min}, {speed_max}]")

    # Validate key delay
    delay = config.get("input.keyboard.key_inject_delay")
    delay_min = config.get("validation.key_delay_range.min", 0.001)
    delay_max = config.get("validation.key_delay_range.max", 1.0)
    if not (delay_min <= delay <= delay_max):
        errors.append(f"Key delay {delay} out of range [{delay_min}, {delay_max}]")

    return errors


if __name__ == "__main__":
    # Test configuration loading
    config = get_config()
    print(f"Configuration loaded: {config}")
    print(f"WebSocket port: {config.DEFAULT_WS_PORT}")
    print(f"HTTP port: {config.DEFAULT_HTTP_PORT}")
    print(f"Key delay: {config.KEY_INJECT_DELAY}")
    print(f"Tunnel enabled: {config.get('security.tunnel.enabled')}")

    # Validate
    errors = validate_config(config)
    if errors:
        print("Validation errors:", errors)
    else:
        print("Configuration is valid!")
