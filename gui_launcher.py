"""
PhoneKey — GUI Launcher  (gui_launcher.py)
A tkinter-based graphical launcher that replaces the terminal TUI
for non-technical users running the .exe.
All server logic still runs in server.py via system.py.
"""

import argparse
import asyncio
import sys
import threading
import tkinter as tk
from tkinter import ttk, messagebox
from argparse import Namespace

# ── Constants ────────────────────────────────────────────────────────────────
_DEFAULT_WS_PORT   = 8765
_DEFAULT_HTTP_PORT = 8080
_BG        = "#0f1117"
_SURFACE   = "#1c1f2e"
_SURFACE2  = "#252840"
_ACCENT    = "#6c63ff"
_SUCCESS   = "#2ecc71"
_WARNING   = "#f39c12"
_DANGER    = "#e74c3c"
_TEXT      = "#e8eaf6"
_TEXT_DIM  = "#9e9eb8"
_BORDER    = "#2e3153"
_FONT      = ("Segoe UI", 10)
_FONT_B    = ("Segoe UI", 10, "bold")
_FONT_H    = ("Segoe UI", 13, "bold")
_FONT_SM   = ("Segoe UI", 8)


# ─────────────────────────────────────────────────────────────────────────────
#  GUI Launcher Window
# ─────────────────────────────────────────────────────────────────────────────

class PhoneKeyLauncher(tk.Tk):
    def __init__(self):
        super().__init__()

        self.title("PhoneKey  v3.2.0")
        self.resizable(False, False)
        self.configure(bg=_BG)

        # Center window on screen
        w, h = 480, 560
        self.geometry(f"{w}x{h}+{(self.winfo_screenwidth()-w)//2}+{(self.winfo_screenheight()-h)//2}")

        # Result — filled when user clicks Start
        self.result: Namespace | None = None

        self._apply_styles()
        self._build_ui()

        # Close button = cancel
        self.protocol("WM_DELETE_WINDOW", self._on_cancel)

    # ── Styles ────────────────────────────────────────────────────────────
    def _apply_styles(self):
        style = ttk.Style(self)
        style.theme_use("clam")

        style.configure(".",
            background=_BG, foreground=_TEXT,
            font=_FONT, borderwidth=0)

        style.configure("TLabel",
            background=_BG, foreground=_TEXT, font=_FONT)

        style.configure("Dim.TLabel",
            background=_BG, foreground=_TEXT_DIM, font=_FONT_SM)

        style.configure("Header.TLabel",
            background=_BG, foreground=_TEXT, font=_FONT_H)

        style.configure("Accent.TLabel",
            background=_BG, foreground=_ACCENT, font=_FONT_B)

        style.configure("Surface.TFrame",
            background=_SURFACE, relief="flat")

        style.configure("TRadiobutton",
            background=_SURFACE, foreground=_TEXT,
            font=_FONT, indicatorcolor=_ACCENT,
            selectcolor=_ACCENT)

        style.map("TRadiobutton",
            background=[("active", _SURFACE2)],
            foreground=[("active", _TEXT)])

        style.configure("TCheckbutton",
            background=_SURFACE, foreground=_TEXT, font=_FONT,
            indicatorcolor=_ACCENT, selectcolor=_ACCENT)

        style.map("TCheckbutton",
            background=[("active", _SURFACE2)])

        style.configure("Start.TButton",
            background=_ACCENT, foreground="#ffffff",
            font=("Segoe UI", 11, "bold"),
            padding=(0, 12), borderwidth=0, relief="flat")

        style.map("Start.TButton",
            background=[("active", "#5b52e0"), ("pressed", "#4a44c0")])

        style.configure("Cancel.TButton",
            background=_SURFACE2, foreground=_TEXT_DIM,
            font=_FONT, padding=(0, 8), borderwidth=0, relief="flat")

        style.map("Cancel.TButton",
            background=[("active", _BORDER)])

        style.configure("TScale",
            background=_BG, troughcolor=_SURFACE2,
            slidercolor=_ACCENT)

        style.configure("TCombobox",
            fieldbackground=_SURFACE, background=_SURFACE,
            foreground=_TEXT, selectbackground=_ACCENT,
            arrowcolor=_TEXT_DIM, borderwidth=1)

    # ── UI Construction ───────────────────────────────────────────────────
    def _build_ui(self):
        root_pad = {"padx": 24, "pady": 0}

        # ── Logo / Title ──────────────────────────────────────────────────
        header = tk.Frame(self, bg=_BG)
        header.pack(fill="x", padx=24, pady=(20, 4))

        tk.Label(header, text="📱  PhoneKey", bg=_BG,
                 fg=_TEXT, font=("Segoe UI", 16, "bold")).pack(side="left")
        tk.Label(header, text="v3.2.0", bg=_BG,
                 fg=_TEXT_DIM, font=_FONT_SM).pack(side="left", padx=(6,0), pady=(6,0))

        tk.Label(self, text="Use your phone as a wireless keyboard & mouse",
                 bg=_BG, fg=_TEXT_DIM, font=_FONT_SM).pack(**root_pad)

        self._divider()

        # ── Connection Mode ───────────────────────────────────────────────
        self._section_label("Connection Mode")

        self._mode_var = tk.StringVar(value="wifi")

        modes = [
            ("wifi",   "📶  Local WiFi  (HTTP, simplest)",
                       "Phone & laptop on same network — no certificate warning"),
            ("https",  "🔒  Local HTTPS  (encrypted)",
                       "Same WiFi, encrypted — phone shows one-time cert warning"),
            ("tunnel", "🌐  Cloudflare Tunnel  (any network)",
                       "Phone & laptop on different networks — requires internet"),
        ]

        for value, label, sub in modes:
            self._radio_card(value, label, sub)

        self._divider()

        # ── PIN ───────────────────────────────────────────────────────────
        self._section_label("Security")
        self._pin_var = tk.BooleanVar(value=True)

        pin_frame = tk.Frame(self, bg=_SURFACE, bd=0)
        pin_frame.pack(fill="x", padx=24, pady=(0, 4))

        pin_inner = tk.Frame(pin_frame, bg=_SURFACE)
        pin_inner.pack(fill="x", padx=12, pady=10)

        ttk.Checkbutton(pin_inner, text="Enable 4-digit connection PIN",
                        variable=self._pin_var,
                        style="TCheckbutton").pack(side="left")

        tk.Label(pin_inner,
                 text="Recommended — prevents unauthorized access",
                 bg=_SURFACE, fg=_TEXT_DIM, font=_FONT_SM).pack(side="left", padx=(10,0))

        self._divider()

        # ── Mouse Speed ────────────────────────────────────────────────────
        self._section_label("Mouse Speed")
        self._speed_var = tk.DoubleVar(value=1.0)

        speed_frame = tk.Frame(self, bg=_SURFACE)
        speed_frame.pack(fill="x", padx=24, pady=(0, 4))

        inner = tk.Frame(speed_frame, bg=_SURFACE)
        inner.pack(fill="x", padx=12, pady=10)

        tk.Label(inner, text="Slow", bg=_SURFACE,
                 fg=_TEXT_DIM, font=_FONT_SM).pack(side="left")

        scale = tk.Scale(inner, from_=0.1, to=5.0,
                         resolution=0.1, orient="horizontal",
                         variable=self._speed_var,
                         bg=_SURFACE, fg=_TEXT,
                         troughcolor=_SURFACE2,
                         activebackground=_ACCENT,
                         highlightthickness=0,
                         sliderlength=18, sliderrelief="flat",
                         showvalue=False, length=260,
                         command=self._on_speed_change)
        scale.pack(side="left", padx=8)

        tk.Label(inner, text="Fast", bg=_SURFACE,
                 fg=_TEXT_DIM, font=_FONT_SM).pack(side="left")

        self._speed_label = tk.Label(inner, text="1.0×",
                                     bg=_SURFACE, fg=_ACCENT, font=_FONT_B, width=5)
        self._speed_label.pack(side="left", padx=(8,0))

        self._divider()

        # ── Clipboard Direction ────────────────────────────────────────────
        self._section_label("Clipboard Sync Direction")
        self._clip_var = tk.StringVar(value="phone_to_laptop")

        clip_options = [
            ("phone_to_laptop", "Phone → Laptop",  "Phone copies text to laptop clipboard"),
            ("laptop_to_phone", "Laptop → Phone",  "Laptop copies text to phone"),
            ("bidirectional",   "↔ Bidirectional", "Both directions — fully synced"),
        ]

        clip_frame = tk.Frame(self, bg=_SURFACE)
        clip_frame.pack(fill="x", padx=24, pady=(0, 4))

        for value, label, sub in clip_options:
            row = tk.Frame(clip_frame, bg=_SURFACE)
            row.pack(fill="x", padx=12, pady=3)

            rb = tk.Radiobutton(row, text=label,
                                variable=self._clip_var, value=value,
                                bg=_SURFACE, fg=_TEXT,
                                selectcolor=_ACCENT,
                                activebackground=_SURFACE2,
                                font=_FONT_B, anchor="w",
                                relief="flat", borderwidth=0,
                                cursor="hand2")
            rb.pack(side="left")

            tk.Label(row, text=f"  —  {sub}",
                     bg=_SURFACE, fg=_TEXT_DIM,
                     font=_FONT_SM).pack(side="left")

        self._divider()

        # ── Buttons ────────────────────────────────────────────────────────
        btn_row = tk.Frame(self, bg=_BG)
        btn_row.pack(fill="x", padx=24, pady=(12, 20))

        cancel_btn = tk.Button(btn_row, text="Cancel",
                               bg=_SURFACE2, fg=_TEXT_DIM,
                               activebackground=_BORDER,
                               activeforeground=_TEXT,
                               font=_FONT, relief="flat",
                               padx=20, pady=10,
                               cursor="hand2",
                               command=self._on_cancel)
        cancel_btn.pack(side="left")

        start_btn = tk.Button(btn_row, text="🚀  Start PhoneKey",
                              bg=_ACCENT, fg="#ffffff",
                              activebackground="#5b52e0",
                              activeforeground="#ffffff",
                              font=("Segoe UI", 11, "bold"),
                              relief="flat", padx=30, pady=10,
                              cursor="hand2",
                              command=self._on_start)
        start_btn.pack(side="right")

    # ── Helper Widgets ─────────────────────────────────────────────────────
    def _divider(self):
        tk.Frame(self, bg=_BORDER, height=1).pack(fill="x", padx=24, pady=8)

    def _section_label(self, text: str):
        tk.Label(self, text=text.upper(),
                 bg=_BG, fg=_TEXT_DIM,
                 font=("Segoe UI", 7, "bold")).pack(
                     anchor="w", padx=24, pady=(0, 4))

    def _radio_card(self, value: str, label: str, subtitle: str):
        card = tk.Frame(self, bg=_SURFACE, cursor="hand2")
        card.pack(fill="x", padx=24, pady=2)

        inner = tk.Frame(card, bg=_SURFACE)
        inner.pack(fill="x", padx=12, pady=8)

        rb = tk.Radiobutton(inner, text=label,
                            variable=self._mode_var, value=value,
                            bg=_SURFACE, fg=_TEXT,
                            selectcolor=_ACCENT,
                            activebackground=_SURFACE2,
                            font=_FONT_B, anchor="w",
                            relief="flat", borderwidth=0,
                            cursor="hand2")
        rb.grid(row=0, column=0, sticky="w")

        tk.Label(inner, text=f"  {subtitle}",
                 bg=_SURFACE, fg=_TEXT_DIM,
                 font=_FONT_SM).grid(row=1, column=0, sticky="w", padx=(22, 0))

        # Click anywhere on the card to select
        card.bind("<Button-1>",  lambda e: self._mode_var.set(value))
        inner.bind("<Button-1>", lambda e: self._mode_var.set(value))

    # ── Event Handlers ─────────────────────────────────────────────────────
    def _on_speed_change(self, val):
        self._speed_label.config(text=f"{float(val):.1f}×")

    def _on_cancel(self):
        self.result = None
        self.destroy()

    def _on_start(self):
        mode = self._mode_var.get()
        self.result = argparse.Namespace(
            https        = (mode == "https"),
            tunnel       = (mode == "tunnel"),
            no_pin       = not self._pin_var.get(),
            mouse_speed  = round(self._speed_var.get(), 1),
            clipboard_sync_direction = self._clip_var.get(),
            ws_port      = _DEFAULT_WS_PORT,
            http_port    = _DEFAULT_HTTP_PORT,
            log_level    = "INFO",
            yes          = True,
        )
        self.destroy()


def run_gui() -> Namespace | None:
    """Show the launcher GUI and return the chosen Namespace, or None if cancelled."""
    app = PhoneKeyLauncher()
    app.mainloop()
    return app.result