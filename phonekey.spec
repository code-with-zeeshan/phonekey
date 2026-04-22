# phonekey.spec
# PyInstaller build specification for PhoneKey
# Run with: pyinstaller phonekey.spec

import sys
from pathlib import Path

block_cipher = None

a = Analysis(
    ["server.py"],                           # Entry point
    pathex=[],
    binaries=[],
    datas=[
        # Bundle client/index.html into the executable
        # Format: (source_path, destination_folder_inside_bundle)
        ("client/index.html", "client"),
    ],
    hiddenimports=[
        # pynput backend modules — PyInstaller can't detect these
        # automatically because pynput loads them dynamically
        "pynput.keyboard._win32",    # Windows backend
        "pynput.keyboard._darwin",   # macOS backend
        "pynput.keyboard._xorg",     # Linux X11 backend
        "pynput.mouse._win32",
        "pynput.mouse._darwin",
        "pynput.mouse._xorg",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Exclude heavy stdlib modules we don't need
        "tkinter",
        "unittest",
        "xml",
        "xmlrpc",
        "pydoc",
        "doctest",
        "difflib",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="phonekey",                        # Output binary name
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,                              # Compress binary (smaller file) — disabled if objdump missing
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,                           # Keep terminal window (shows server logs)
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # Windows: show a nice name in Task Manager
    version_file=None,
    icon=None,                              # Add icon path here if you have one
)