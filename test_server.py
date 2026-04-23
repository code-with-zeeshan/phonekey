"""
End-to-end and unit tests for PhoneKey server components.

Tests cover:
- Client directory structure and files
- Device registration and duplicate tab detection (when server module is available)
"""

import unittest
import os
import sys
from pathlib import Path

# Add current directory to path
sys.path.insert(0, os.path.dirname(__file__))

CLIENT_DIR = Path(__file__).parent / "client"


class TestClientFiles(unittest.TestCase):
    """Test client directory and files."""

    def test_client_dir_exists(self):
        """Test that client directory exists."""
        self.assertTrue(CLIENT_DIR.exists())
        self.assertTrue(CLIENT_DIR.is_dir())

    def test_index_html_exists(self):
        """Test that index.html exists."""
        index_path = CLIENT_DIR / "index.html"
        self.assertTrue(index_path.exists())
        self.assertGreater(index_path.stat().st_size, 1000)  # Should be substantial

    def test_phonekey_ico_exists(self):
        """Test that phonekey.ico exists and has proper size."""
        ico_path = CLIENT_DIR / "phonekey.ico"
        self.assertTrue(ico_path.exists())
        # Proper .ico file should be at least 1KB (with multiple sizes)
        self.assertGreater(ico_path.stat().st_size, 1000)

    def test_phonekey_ico_is_ico_format(self):
        """Test that phonekey.ico is a valid ICO file."""
        ico_path = CLIENT_DIR / "phonekey.ico"
        with open(ico_path, 'rb') as f:
            header = f.read(4)
            # ICO files start with 0x00 0x00 0x01 0x00
            self.assertEqual(header, b'\x00\x00\x01\x00')


class TestServerComponents(unittest.TestCase):
    """Test server components (skipped if server can't be imported)."""

    @classmethod
    def setUpClass(cls):
        """Try to import server components."""
        # Mock external modules before importing server
        cls.server_available = False
        cls.server_components = {}

        try:
            # Mock sys.argv to prevent argument parsing
            old_argv = sys.argv
            sys.argv = ['test']

            # Import server - this may fail if dependencies not installed
            import server
            cls.server_available = True

            # Store references to components we want to test
            cls.server_components = {
                'get_local_ip': getattr(server, 'get_local_ip', None),
                'ConnectedDevice': getattr(server, 'ConnectedDevice', None),
                '_register_device': getattr(server, '_register_device', None),
                '_unregister_device': getattr(server, '_unregister_device', None),
                '_try_register_device': getattr(server, '_try_register_device', None),
                '_device_registry': getattr(server, '_device_registry', None),
                '_tab_id_to_device': getattr(server, '_tab_id_to_device', None),
                '_registry_lock': getattr(server, '_registry_lock', None),
            }

            sys.argv = old_argv
        except (ImportError, AttributeError) as e:
            cls.server_available = False
            print(f"\nServer module not available: {e}")

    def setUp(self):
        """Clear registries before each test if available."""
        if self.server_available:
            with self.server_components['_registry_lock']:
                self.server_components['_device_registry'].clear()
                self.server_components['_tab_id_to_device'].clear()

    def tearDown(self):
        """Clear registries after each test if available."""
        if self.server_available:
            with self.server_components['_registry_lock']:
                self.server_components['_device_registry'].clear()
                self.server_components['_tab_id_to_device'].clear()

    def test_server_available(self):
        """Test if server module is available."""
        if not self.server_available:
            self.skipTest("Server module not available")

    def test_connected_device_creation(self):
        """Test ConnectedDevice can be created with tab_id."""
        if not self.server_available:
            self.skipTest("Server module not available")

        ConnectedDevice = self.server_components['ConnectedDevice']
        device = ConnectedDevice(
            device_id="test-123",
            name="Test Device",
            websocket=None,
            authed=True,
            tab_id="tab-123"
        )
        self.assertEqual(device.device_id, "test-123")
        self.assertEqual(device.tab_id, "tab-123")

    def test_register_device_with_tab_id(self):
        """Test device registration stores tab_id mapping."""
        if not self.server_available:
            self.skipTest("Server module not available")

        ConnectedDevice = self.server_components['ConnectedDevice']
        _register_device = self.server_components['_register_device']
        _device_registry = self.server_components['_device_registry']
        _tab_id_to_device = self.server_components['_tab_id_to_device']
        _registry_lock = self.server_components['_registry_lock']

        device = ConnectedDevice(
            device_id="dev-1",
            name="Device 1",
            websocket=None,
            tab_id="tab-1"
        )
        _register_device(device)

        with _registry_lock:
            self.assertIn("dev-1", _device_registry)
            self.assertIn("tab-1", _tab_id_to_device)

    def test_duplicate_tab_detection(self):
        """Test duplicate tab_id is rejected."""
        if not self.server_available:
            self.skipTest("Server module not available")

        ConnectedDevice = self.server_components['ConnectedDevice']
        _try_register_device = self.server_components['_try_register_device']
        _registry_lock = self.server_components['_registry_lock']

        # Register first device
        device1 = ConnectedDevice(
            device_id="dev-20",
            name="Device 20",
            websocket=None,
            tab_id="tab-20"
        )
        result1 = _try_register_device(device1, "tab-20", ("127.0.0.1", 12345))
        self.assertTrue(result1)

        # Try to register second device with same tab_id
        device2 = ConnectedDevice(
            device_id="dev-21",
            name="Device 21",
            websocket=None,
            tab_id="tab-20"  # Same tab_id!
        )
        result2 = _try_register_device(device2, "tab-20", ("127.0.0.1", 12346))
        self.assertFalse(result2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
