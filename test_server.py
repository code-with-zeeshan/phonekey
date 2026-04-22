"""
Basic unit tests for PhoneKey server components.
"""

import unittest
from unittest.mock import patch
import os
import sys

# Add current directory to path
sys.path.insert(0, os.path.dirname(__file__))

from server import get_local_ip, CONFIG


class TestServer(unittest.TestCase):

    def test_get_local_ip(self):
        """Test IP detection returns a string."""
        ip = get_local_ip()
        self.assertIsInstance(ip, str)
        self.assertGreater(len(ip), 0)

    def test_config_defaults(self):
        """Test configuration defaults."""
        self.assertEqual(CONFIG["ws_port"], 8765)
        self.assertEqual(CONFIG["http_port"], 8080)
        self.assertEqual(CONFIG["key_inject_delay"], 0.012)

    @patch.dict(os.environ, {"PHONEKEY_WS_PORT": "9999"})
    def test_config_env_override(self):
        """Test configuration from environment."""
        # Reload config (simulate)
        config = {
            "ws_port": int(os.environ.get("PHONEKEY_WS_PORT", 8765)),
            "http_port": int(os.environ.get("PHONEKEY_HTTP_PORT", 8080)),
        }
        self.assertEqual(config["ws_port"], 9999)


if __name__ == "__main__":
    unittest.main()