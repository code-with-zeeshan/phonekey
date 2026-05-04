# phonekey.spec
# PyInstaller build specification for PhoneKey
# Run with: pyinstaller phonekey.spec

import sys
from pathlib import Path

block_cipher = None

a = Analysis(
    ["system.py"],                           # Entry point
    pathex=[],
    binaries=[],
    datas=[
        # Bundle client/index.html and icon into the executable
        ("client/index.html", "client"),
        ("client/phonekey.ico", "client"),
        # GUI launcher is a pure-Python module — included automatically,
        # but listing it here makes the dependency explicit.
        ("gui_launcher.py", "."),
        ("logging_setup.py",    "."),
        ("server.py",           "."),
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
        # tkinter and its sub-modules must be listed explicitly
        # because PyInstaller does not auto-detect dynamic imports
        "tkinter",
        "tkinter.ttk",
        "tkinter.messagebox",
        "_tkinter",
        "PIL",
        "PIL.Image",
        "PIL.ImageTk",
        "qrcode",
        "qrcode.image.base",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Exclude heavy stdlib modules we don't need
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
    # ── IMPORTANT: console=False: no black terminal flash on double-click ─────────────
    # Logs are shown inside the GUI log panel instead.
    # Set to True temporarily if you need to debug a crash before GUI opens.
    console=False,                           
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # Windows: show a nice name in Task Manager
    version_file=None,
    icon="client/phonekey.ico",                    # Icon for executable
)