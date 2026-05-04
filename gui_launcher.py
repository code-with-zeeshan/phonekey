"""
PhoneKey — GUI Launcher  (gui_launcher.py)
Scrollable dark-theme GUI with embedded QR code, live log panel,
and all server configuration options.
"""

import argparse
import queue
import sys
import threading
import tkinter as tk
from tkinter import font as tkfont
from argparse import Namespace
from typing import Optional

__version__ = "3.2.1"

_DEFAULT_WS_PORT   = 8765
_DEFAULT_HTTP_PORT = 8080

# ── Palette ───────────────────────────────────────────────────────────────────
_BG       = "#0f1117"
_SURFACE  = "#1c1f2e"
_SURFACE2 = "#252840"
_ACCENT   = "#6c63ff"
_SUCCESS  = "#2ecc71"
_WARNING  = "#f39c12"
_DANGER   = "#e74c3c"
_TEXT     = "#e8eaf6"
_TEXT_DIM = "#9e9eb8"
_BORDER   = "#2e3153"

# ── Log queue shared between server thread and GUI ────────────────────────────
log_queue: queue.Queue = queue.Queue()


# ─────────────────────────────────────────────────────────────────────────────
#  Scrollable Frame helper
# ─────────────────────────────────────────────────────────────────────────────

class ScrollableFrame(tk.Frame):
    """A frame that scrolls with mouse-wheel; scrollbar is hidden."""

    def __init__(self, parent, bg=_BG, **kw):
        super().__init__(parent, bg=bg, **kw)

        self._canvas = tk.Canvas(self, bg=bg, highlightthickness=0,
                                 bd=0, relief="flat")
        self._canvas.pack(fill="both", expand=True)

        self.inner = tk.Frame(self._canvas, bg=bg)
        self._win_id = self._canvas.create_window(
            (0, 0), window=self.inner, anchor="nw"
        )

        self.inner.bind("<Configure>", self._on_inner_configure)
        self._canvas.bind("<Configure>", self._on_canvas_configure)
        self._canvas.bind_all("<MouseWheel>",       self._on_mousewheel)   # Windows
        self._canvas.bind_all("<Button-4>",         self._on_mousewheel)   # Linux scroll up
        self._canvas.bind_all("<Button-5>",         self._on_mousewheel)   # Linux scroll down

    def _on_inner_configure(self, _event):
        self._canvas.configure(scrollregion=self._canvas.bbox("all"))

    def _on_canvas_configure(self, event):
        self._canvas.itemconfig(self._win_id, width=event.width)

    def _on_mousewheel(self, event):
        if event.num == 4:
            self._canvas.yview_scroll(-1, "units")
        elif event.num == 5:
            self._canvas.yview_scroll(1, "units")
        else:
            self._canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")


# ─────────────────────────────────────────────────────────────────────────────
#  Launcher Window
# ─────────────────────────────────────────────────────────────────────────────

class PhoneKeyLauncher(tk.Tk):
    def __init__(self):
        super().__init__()

        self.title(f"PhoneKey  v{__version__}")
        self.configure(bg=_BG)
        self.resizable(True, True)
        self.minsize(500, 400)

        # Window size + center
        w, h = 520, 680
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.geometry(f"{w}x{h}+{(sw-w)//2}+{(sh-h)//2}")

        self.result: Optional[Namespace] = None

        # QR image reference (prevents GC)
        self._qr_photo = None

        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self._on_cancel)

    # ── Top-level layout ──────────────────────────────────────────────────
    def _build_ui(self):
        # ── Fixed header ─────────────────────────────────────────────────
        self._build_header()

        # ── Scrollable body ───────────────────────────────────────────────
        self._scroll = ScrollableFrame(self, bg=_BG)
        self._scroll.pack(fill="both", expand=True)
        body = self._scroll.inner

        self._build_mode_section(body)
        self._divider(body)
        self._build_pin_section(body)
        self._divider(body)
        self._build_speed_section(body)
        self._divider(body)
        self._build_clipboard_section(body)
        self._divider(body)
        self._build_qr_section(body)
        self._divider(body)
        self._build_log_section(body)

        # ── Fixed footer with buttons ─────────────────────────────────────
        self._build_footer()

    # ── Header ────────────────────────────────────────────────────────────
    def _build_header(self):
        hdr = tk.Frame(self, bg=_SURFACE, pady=14)
        hdr.pack(fill="x")

        inner = tk.Frame(hdr, bg=_SURFACE)
        inner.pack()

        tk.Label(inner, text="🔐", bg=_SURFACE,
                 font=("Segoe UI Emoji", 28)).pack(side="left", padx=(0, 10))

        text_col = tk.Frame(inner, bg=_SURFACE)
        text_col.pack(side="left")

        tk.Label(text_col, text="PhoneKey",
                 bg=_SURFACE, fg=_TEXT,
                 font=("Segoe UI", 18, "bold")).pack(anchor="w")

        sub = tk.Frame(text_col, bg=_SURFACE)
        sub.pack(anchor="w")
        tk.Label(sub, text=f"v{__version__}",
                 bg=_SURFACE, fg=_ACCENT,
                 font=("Segoe UI", 9, "bold")).pack(side="left")
        tk.Label(sub, text="  —  Wireless keyboard & mouse",
                 bg=_SURFACE, fg=_TEXT_DIM,
                 font=("Segoe UI", 9)).pack(side="left")

        # Thin accent line at bottom of header
        tk.Frame(self, bg=_ACCENT, height=2).pack(fill="x")

    # ── Section helpers ────────────────────────────────────────────────────
    def _divider(self, parent):
        tk.Frame(parent, bg=_BORDER, height=1).pack(fill="x", padx=20, pady=10)

    def _section_label(self, parent, text: str):
        tk.Label(parent, text=text.upper(),
                 bg=_BG, fg=_TEXT_DIM,
                 font=("Segoe UI", 7, "bold")).pack(
                     anchor="w", padx=20, pady=(0, 6))

    def _card(self, parent, color=_SURFACE):
        f = tk.Frame(parent, bg=color, bd=0)
        f.pack(fill="x", padx=20, pady=2)
        return f

    # ── Mode section ──────────────────────────────────────────────────────
    def _build_mode_section(self, parent):
        self._section_label(parent, "Connection Mode")
        self._mode_var = tk.StringVar(value="wifi")

        modes = [
            ("wifi",   "📶  Local WiFi",
             "HTTP · same network · simplest setup · no cert warning"),
            ("https",  "🔒  Local HTTPS",
             "Encrypted · same WiFi · one-time cert warning on phone"),
            ("tunnel", "🌐  Cloudflare Tunnel",
             "Any network · no cert warning · requires internet"),
        ]
        for val, label, sub in modes:
            self._radio_card(parent, val, label, sub)

    def _radio_card(self, parent, value, label, subtitle):
        card = tk.Frame(parent, bg=_SURFACE, cursor="hand2")
        card.pack(fill="x", padx=20, pady=2)

        inner = tk.Frame(card, bg=_SURFACE)
        inner.pack(fill="x", padx=12, pady=10)

        rb = tk.Radiobutton(inner, text=label,
                            variable=self._mode_var, value=value,
                            bg=_SURFACE, fg=_TEXT,
                            selectcolor=_ACCENT,
                            activebackground=_SURFACE2,
                            activeforeground=_TEXT,
                            font=("Segoe UI", 10, "bold"),
                            anchor="w", relief="flat",
                            borderwidth=0, cursor="hand2")
        rb.pack(anchor="w")

        tk.Label(inner, text=f"  {subtitle}",
                 bg=_SURFACE, fg=_TEXT_DIM,
                 font=("Segoe UI", 8)).pack(anchor="w")

        # Click anywhere on card selects radio
        for widget in (card, inner):
            widget.bind("<Button-1>", lambda e, v=value: self._mode_var.set(v))

    # ── PIN section ────────────────────────────────────────────────────────
    def _build_pin_section(self, parent):
        self._section_label(parent, "Security")
        self._pin_var = tk.BooleanVar(value=True)

        card = self._card(parent)
        inner = tk.Frame(card, bg=_SURFACE)
        inner.pack(fill="x", padx=12, pady=10)

        cb = tk.Checkbutton(inner,
                            text="🔑  Enable 4-digit connection PIN",
                            variable=self._pin_var,
                            bg=_SURFACE, fg=_TEXT,
                            selectcolor=_SURFACE2,
                            activebackground=_SURFACE,
                            activeforeground=_TEXT,
                            font=("Segoe UI", 10, "bold"),
                            cursor="hand2", relief="flat")
        cb.pack(side="left")

        tk.Label(inner, text="   Recommended",
                 bg=_SURFACE, fg=_SUCCESS,
                 font=("Segoe UI", 8)).pack(side="left")

    # ── Speed section ──────────────────────────────────────────────────────
    def _build_speed_section(self, parent):
        self._section_label(parent, "Mouse Speed")
        self._speed_var = tk.DoubleVar(value=1.0)

        card = self._card(parent)
        inner = tk.Frame(card, bg=_SURFACE)
        inner.pack(fill="x", padx=12, pady=10)

        tk.Label(inner, text="🐢", bg=_SURFACE,
                 font=("Segoe UI Emoji", 14)).pack(side="left")

        self._speed_scale = tk.Scale(
            inner, from_=0.1, to=5.0, resolution=0.1,
            orient="horizontal", variable=self._speed_var,
            bg=_SURFACE, fg=_TEXT,
            troughcolor=_SURFACE2,
            activebackground=_ACCENT,
            highlightthickness=0,
            sliderlength=18, sliderrelief="flat",
            showvalue=False, length=300,
            command=self._on_speed_change,
        )
        self._speed_scale.pack(side="left", padx=8)

        tk.Label(inner, text="🚀", bg=_SURFACE,
                 font=("Segoe UI Emoji", 14)).pack(side="left")

        self._speed_label = tk.Label(inner, text="1.0×",
                                     bg=_SURFACE, fg=_ACCENT,
                                     font=("Segoe UI", 10, "bold"), width=5)
        self._speed_label.pack(side="left", padx=(8, 0))

    def _on_speed_change(self, val):
        self._speed_label.config(text=f"{float(val):.1f}×")

    # ── Clipboard section ──────────────────────────────────────────────────
    def _build_clipboard_section(self, parent):
        self._section_label(parent, "Clipboard Sync Direction")
        self._clip_var = tk.StringVar(value="phone_to_laptop")

        options = [
            ("phone_to_laptop", "📱→💻  Phone → Laptop",
             "Phone copies text to laptop clipboard"),
            ("laptop_to_phone", "💻→📱  Laptop → Phone",
             "Laptop clipboard changes appear on phone"),
            ("bidirectional",   "↔  Bidirectional",
             "Both directions — fully synced"),
        ]
        card = self._card(parent)
        for val, label, sub in options:
            row = tk.Frame(card, bg=_SURFACE)
            row.pack(fill="x", padx=12, pady=4)

            rb = tk.Radiobutton(row, text=label,
                                variable=self._clip_var, value=val,
                                bg=_SURFACE, fg=_TEXT,
                                selectcolor=_ACCENT,
                                activebackground=_SURFACE2,
                                activeforeground=_TEXT,
                                font=("Segoe UI", 10, "bold"),
                                anchor="w", relief="flat",
                                cursor="hand2")
            rb.pack(side="left")

            tk.Label(row, text=f"   {sub}",
                     bg=_SURFACE, fg=_TEXT_DIM,
                     font=("Segoe UI", 8)).pack(side="left")

    # ── QR section ─────────────────────────────────────────────────────────
    def _build_qr_section(self, parent):
        self._section_label(parent, "QR Code")

        card = self._card(parent)
        inner = tk.Frame(card, bg=_SURFACE)
        inner.pack(fill="x", padx=12, pady=10)

        self._qr_label = tk.Label(
            inner,
            text="🔲  QR code will appear here after server starts",
            bg=_SURFACE, fg=_TEXT_DIM,
            font=("Segoe UI", 9), wraplength=400, justify="center",
        )
        self._qr_label.pack(pady=8)

        self._url_label = tk.Label(
            inner, text="",
            bg=_SURFACE, fg=_ACCENT,
            font=("Segoe UI", 9, "bold"),
            cursor="hand2",
        )
        self._url_label.pack()

    def show_qr(self, url: str):
        """Called from server thread via after() — renders QR into GUI."""
        try:
            import qrcode
            from PIL import Image, ImageTk

            qr = qrcode.QRCode(
                version=None,
                error_correction=qrcode.constants.ERROR_CORRECT_L,
                box_size=5, border=2,
            )
            qr.add_data(url)
            qr.make(fit=True)
            img = qr.make_image(fill_color=_TEXT, back_color=_SURFACE)
            img = img.resize((220, 220), Image.NEAREST)
            self._qr_photo = ImageTk.PhotoImage(img)
            self._qr_label.config(image=self._qr_photo, text="")
            self._url_label.config(text=url)
        except ImportError:
            # Pillow not available — show text QR fallback
            self._qr_label.config(
                text=f"📱 Scan URL in your browser:\n{url}",
                fg=_TEXT,
            )
            self._url_label.config(text=url)

    # ── Log section ────────────────────────────────────────────────────────
    def _build_log_section(self, parent):
        # Collapsible header
        self._log_expanded = tk.BooleanVar(value=False)
        self._log_frame_outer = tk.Frame(parent, bg=_BG)
        self._log_frame_outer.pack(fill="x", padx=20, pady=(0, 10))

        toggle_btn = tk.Button(
            self._log_frame_outer,
            text="▶  Server Logs",
            bg=_SURFACE2, fg=_TEXT_DIM,
            activebackground=_BORDER,
            activeforeground=_TEXT,
            font=("Segoe UI", 9, "bold"),
            relief="flat", anchor="w",
            padx=12, pady=6, cursor="hand2",
            command=self._toggle_log,
        )
        toggle_btn.pack(fill="x")
        self._log_toggle_btn = toggle_btn

        # Log text widget (hidden by default)
        self._log_container = tk.Frame(self._log_frame_outer, bg=_SURFACE)
        self._log_text = tk.Text(
            self._log_container,
            bg="#0a0c14", fg=_TEXT_DIM,
            font=("Consolas", 8),
            height=10, wrap="word",
            relief="flat", bd=0,
            state="disabled",
        )
        self._log_text.pack(fill="both", expand=True, padx=1, pady=1)

        # Color tags for log levels
        self._log_text.tag_config("INFO",    foreground=_TEXT_DIM)
        self._log_text.tag_config("WARNING", foreground=_WARNING)
        self._log_text.tag_config("ERROR",   foreground=_DANGER)
        self._log_text.tag_config("SUCCESS", foreground=_SUCCESS)

        # Poll log queue every 200 ms
        self._poll_log()

    def _toggle_log(self):
        if self._log_expanded.get():
            self._log_expanded.set(False)
            self._log_container.pack_forget()
            self._log_toggle_btn.config(text="▶  Server Logs")
        else:
            self._log_expanded.set(True)
            self._log_container.pack(fill="x")
            self._log_toggle_btn.config(text="▼  Server Logs")

    def _poll_log(self):
        try:
            while True:
                record = log_queue.get_nowait()
                self._append_log(record)
        except queue.Empty:
            pass
        self.after(200, self._poll_log)

    def _append_log(self, text: str):
        self._log_text.config(state="normal")
        tag = "INFO"
        if "WARNING" in text or "⚠" in text:
            tag = "WARNING"
        elif "ERROR" in text or "❌" in text:
            tag = "ERROR"
        elif "✅" in text or "ready" in text.lower():
            tag = "SUCCESS"
        self._log_text.insert("end", text + "\n", tag)
        self._log_text.see("end")
        self._log_text.config(state="disabled")

    # ── Footer buttons ─────────────────────────────────────────────────────
    def _build_footer(self):
        tk.Frame(self, bg=_BORDER, height=1).pack(fill="x")

        foot = tk.Frame(self, bg=_BG, pady=14)
        foot.pack(fill="x", padx=20)

        tk.Button(foot, text="✕  Cancel",
                  bg=_SURFACE2, fg=_TEXT_DIM,
                  activebackground=_BORDER,
                  activeforeground=_TEXT,
                  font=("Segoe UI", 9),
                  relief="flat", padx=18, pady=8,
                  cursor="hand2",
                  command=self._on_cancel).pack(side="left")

        self._start_btn = tk.Button(
            foot, text="🚀  Start PhoneKey",
            bg=_ACCENT, fg="#ffffff",
            activebackground="#5b52e0",
            activeforeground="#ffffff",
            font=("Segoe UI", 11, "bold"),
            relief="flat", padx=30, pady=10,
            cursor="hand2",
            command=self._on_start,
        )
        self._start_btn.pack(side="right")

    # ── Actions ────────────────────────────────────────────────────────────
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
        # Disable controls — server is starting
        self._start_btn.config(
            text="⏳  Starting…",
            state="disabled",
            bg=_SURFACE2, fg=_TEXT_DIM,
        )
        # Expand log automatically so user sees progress
        if not self._log_expanded.get():
            self._toggle_log()

        # Signal the waiting thread
        self._start_event.set()

    def set_start_event(self, event: threading.Event):
        self._start_event = event


# ─────────────────────────────────────────────────────────────────────────────
#  Public API
# ─────────────────────────────────────────────────────────────────────────────

_launcher_instance: Optional[PhoneKeyLauncher] = None


def run_gui() -> Optional[Namespace]:
    """Show launcher, block until Start or Cancel, return Namespace or None."""
    global _launcher_instance
    start_event = threading.Event()
    app = PhoneKeyLauncher()
    app.set_start_event(start_event)
    _launcher_instance = app
    app.mainloop()
    return app.result


def notify_qr(url: str):
    """Called from server thread to display QR code in the running GUI."""
    if _launcher_instance and _launcher_instance.winfo_exists():
        _launcher_instance.after(0, lambda: _launcher_instance.show_qr(url))


def log_to_gui(text: str):
    """Thread-safe: push a log line to the GUI log panel."""
    log_queue.put(text)