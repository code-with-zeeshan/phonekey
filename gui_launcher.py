"""
PhoneKey — GUI Launcher  (gui_launcher.py)
GUI with embedded QR code, live log panel,and all server configuration options.
Phase 1 : Config window  → user picks settings, clicks Start → window stays open
Phase 2 : Server running → same window switches to "running" view with logs + QR
The server runs in a background daemon thread so the GUI stays responsive.
"""

import argparse
import queue
import sys
import threading
import tkinter as tk
from argparse import Namespace
from typing import Optional, Callable

__version__ = "3.2.1"

_DEFAULT_WS_PORT   = 8765
_DEFAULT_HTTP_PORT = 8080

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

log_queue: queue.Queue = queue.Queue()

# Pending notifications queued before running view is built
_pending_qr:  Optional[str] = None
_pending_pin: Optional[str] = None

_app_ref: Optional["PhoneKeyApp"] = None


# ── Scrollable Frame ──────────────────────────────────────────────────────────

class _ScrollFrame(tk.Frame):
    def __init__(self, parent, bg=_BG, **kw):
        super().__init__(parent, bg=bg, **kw)
        self._cv = tk.Canvas(self, bg=bg, highlightthickness=0, bd=0)
        self._cv.pack(fill="both", expand=True)
        self.inner = tk.Frame(self._cv, bg=bg)
        self._win  = self._cv.create_window((0, 0), window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", lambda e: self._cv.configure(
            scrollregion=self._cv.bbox("all")))
        self._cv.bind("<Configure>", lambda e: self._cv.itemconfig(
            self._win, width=e.width))
        for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            self._cv.bind_all(seq, self._on_wheel)

    def _on_wheel(self, event):
        if   event.num == 4: self._cv.yview_scroll(-1, "units")
        elif event.num == 5: self._cv.yview_scroll( 1, "units")
        else: self._cv.yview_scroll(int(-1 * event.delta / 120), "units")


# ── Main Application Window ───────────────────────────────────────────────────

class PhoneKeyApp(tk.Tk):
    def __init__(self, server_runner: Callable[[Namespace], None]):
        super().__init__()
        self._server_runner  = server_runner
        self._server_thread: Optional[threading.Thread] = None
        self._stop_event     = threading.Event()
        # Flags so we never double-apply pending notifications
        self._running_view_ready = False

        self.title(f"PhoneKey  v{__version__}")
        self.configure(bg=_BG)
        self.resizable(True, True)
        self.minsize(500, 420)
        w, h = 520, 680
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.geometry(f"{w}x{h}+{(sw-w)//2}+{(sh-h)//2}")

        self._build_config_view()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._poll_log()

    # ── Shared header ─────────────────────────────────────────────────────

    def _make_header(self, parent):
        hdr = tk.Frame(parent, bg=_SURFACE, pady=14)
        hdr.pack(fill="x")
        inner = tk.Frame(hdr, bg=_SURFACE)
        inner.pack()
        tk.Label(inner, text="🔐", bg=_SURFACE,
                 font=("Segoe UI Emoji", 26)).pack(side="left", padx=(0, 10))
        col = tk.Frame(inner, bg=_SURFACE)
        col.pack(side="left")
        tk.Label(col, text="PhoneKey",
                 bg=_SURFACE, fg=_TEXT,
                 font=("Segoe UI", 17, "bold")).pack(anchor="w")
        sub = tk.Frame(col, bg=_SURFACE)
        sub.pack(anchor="w")
        tk.Label(sub, text=f"v{__version__}",
                 bg=_SURFACE, fg=_ACCENT,
                 font=("Segoe UI", 9, "bold")).pack(side="left")
        tk.Label(sub, text="  —  Wireless keyboard & mouse",
                 bg=_SURFACE, fg=_TEXT_DIM,
                 font=("Segoe UI", 9)).pack(side="left")
        tk.Frame(parent, bg=_ACCENT, height=2).pack(fill="x")

    # ── Section helpers ───────────────────────────────────────────────────

    def _hr(self, parent):
        tk.Frame(parent, bg=_BORDER, height=1).pack(
            fill="x", padx=20, pady=8)

    def _section(self, parent, text):
        tk.Label(parent, text=text.upper(),
                 bg=_BG, fg=_TEXT_DIM,
                 font=("Segoe UI", 7, "bold")).pack(
                     anchor="w", padx=20, pady=(0, 5))

    # ── Phase 1 — Config view ─────────────────────────────────────────────

    def _build_config_view(self):
        self._config_root = tk.Frame(self, bg=_BG)
        self._config_root.pack(fill="both", expand=True)
        self._make_header(self._config_root)
        sf   = _ScrollFrame(self._config_root, bg=_BG)
        sf.pack(fill="both", expand=True)
        body = sf.inner
        self._build_mode(body)
        self._hr(body)
        self._build_pin(body)
        self._hr(body)
        self._build_speed(body)
        self._hr(body)
        self._build_clipboard(body)
        self._hr(body)
        self._build_config_footer(body)

    def _build_mode(self, parent):
        self._section(parent, "Connection Mode")
        self._mode_var = tk.StringVar(value="wifi")
        for val, lbl, sub in [
            ("wifi",   "📶  Local WiFi",
             "HTTP · same network · no cert warning"),
            ("https",  "🔒  Local HTTPS",
             "Encrypted · same WiFi · one-time cert warning"),
            ("tunnel", "🌐  Cloudflare Tunnel",
             "Any network · no cert warning · needs internet"),
        ]:
            card  = tk.Frame(parent, bg=_SURFACE, cursor="hand2")
            card.pack(fill="x", padx=20, pady=2)
            inner = tk.Frame(card, bg=_SURFACE)
            inner.pack(fill="x", padx=12, pady=9)
            tk.Radiobutton(inner, text=lbl,
                           variable=self._mode_var, value=val,
                           bg=_SURFACE, fg=_TEXT,
                           selectcolor=_ACCENT,
                           activebackground=_SURFACE2,
                           activeforeground=_TEXT,
                           font=("Segoe UI", 10, "bold"),
                           anchor="w", relief="flat",
                           cursor="hand2").pack(anchor="w")
            tk.Label(inner, text=f"  {sub}",
                     bg=_SURFACE, fg=_TEXT_DIM,
                     font=("Segoe UI", 8)).pack(anchor="w")
            for w in (card, inner):
                w.bind("<Button-1>", lambda e, v=val: self._mode_var.set(v))

    def _build_pin(self, parent):
        self._section(parent, "Security")
        self._pin_var = tk.BooleanVar(value=True)
        card  = tk.Frame(parent, bg=_SURFACE)
        card.pack(fill="x", padx=20, pady=2)
        inner = tk.Frame(card, bg=_SURFACE)
        inner.pack(fill="x", padx=12, pady=10)
        tk.Checkbutton(inner,
                       text="🔑  Enable 4-digit connection PIN",
                       variable=self._pin_var,
                       bg=_SURFACE, fg=_TEXT,
                       selectcolor=_SURFACE2,
                       activebackground=_SURFACE,
                       font=("Segoe UI", 10, "bold"),
                       cursor="hand2", relief="flat").pack(side="left")
        tk.Label(inner, text="   Recommended",
                 bg=_SURFACE, fg=_SUCCESS,
                 font=("Segoe UI", 8)).pack(side="left")

    def _build_speed(self, parent):
        self._section(parent, "Mouse Speed")
        self._speed_var = tk.DoubleVar(value=1.0)
        card  = tk.Frame(parent, bg=_SURFACE)
        card.pack(fill="x", padx=20, pady=2)
        inner = tk.Frame(card, bg=_SURFACE)
        inner.pack(fill="x", padx=12, pady=10)
        tk.Label(inner, text="🐢", bg=_SURFACE,
                 font=("Segoe UI Emoji", 13)).pack(side="left")
        self._spd_lbl = tk.Label(inner, text="1.0×",
                                 bg=_SURFACE, fg=_ACCENT,
                                 font=("Segoe UI", 10, "bold"), width=5)
        sc = tk.Scale(inner, from_=0.1, to=5.0, resolution=0.1,
                      orient="horizontal", variable=self._speed_var,
                      bg=_SURFACE, fg=_TEXT,
                      troughcolor=_SURFACE2,
                      activebackground=_ACCENT,
                      highlightthickness=0,
                      sliderlength=16, sliderrelief="flat",
                      showvalue=False, length=280,
                      command=lambda v: self._spd_lbl.config(
                          text=f"{float(v):.1f}×"))
        sc.pack(side="left", padx=8)
        tk.Label(inner, text="🚀", bg=_SURFACE,
                 font=("Segoe UI Emoji", 13)).pack(side="left")
        self._spd_lbl.pack(side="left", padx=(8, 0))

    def _build_clipboard(self, parent):
        self._section(parent, "Clipboard Sync Direction")
        self._clip_var = tk.StringVar(value="phone_to_laptop")
        card = tk.Frame(parent, bg=_SURFACE)
        card.pack(fill="x", padx=20, pady=2)
        for val, lbl, sub in [
            ("phone_to_laptop", "📱→💻  Phone → Laptop",
             "Phone sends text to laptop clipboard"),
            ("laptop_to_phone", "💻→📱  Laptop → Phone",
             "Laptop clipboard changes appear on phone"),
            ("bidirectional",   "↔  Bidirectional",
             "Both directions — fully synced"),
        ]:
            row = tk.Frame(card, bg=_SURFACE)
            row.pack(fill="x", padx=12, pady=4)
            tk.Radiobutton(row, text=lbl,
                           variable=self._clip_var, value=val,
                           bg=_SURFACE, fg=_TEXT,
                           selectcolor=_ACCENT,
                           activebackground=_SURFACE2,
                           font=("Segoe UI", 10, "bold"),
                           anchor="w", relief="flat",
                           cursor="hand2").pack(side="left")
            tk.Label(row, text=f"   {sub}",
                     bg=_SURFACE, fg=_TEXT_DIM,
                     font=("Segoe UI", 8)).pack(side="left")

    def _build_config_footer(self, parent):
        foot = tk.Frame(parent, bg=_BG, pady=16)
        foot.pack(fill="x", padx=20)
        tk.Button(foot, text="✕  Cancel",
                  bg=_SURFACE2, fg=_TEXT_DIM,
                  activebackground=_BORDER,
                  font=("Segoe UI", 9),
                  relief="flat", padx=16, pady=8,
                  cursor="hand2",
                  command=self._on_close).pack(side="left")
        self._start_btn = tk.Button(
            foot, text="🚀  Start PhoneKey",
            bg=_ACCENT, fg="#ffffff",
            activebackground="#5b52e0",
            font=("Segoe UI", 11, "bold"),
            relief="flat", padx=28, pady=10,
            cursor="hand2",
            command=self._on_start)
        self._start_btn.pack(side="right")

    # ── Phase 2 — Running dashboard ───────────────────────────────────────

    def _show_running_view(self):
        """Switch from config view to running dashboard. Called via after()."""
        self._config_root.destroy()

        self._run_root = tk.Frame(self, bg=_BG)
        self._run_root.pack(fill="both", expand=True)
        self._make_header(self._run_root)

        # Status bar
        self._status_bar = tk.Label(
            self._run_root,
            text="⏳  Server starting…",
            bg=_SURFACE2, fg=_WARNING,
            font=("Segoe UI", 9, "bold"), pady=6)
        self._status_bar.pack(fill="x")

        sf   = _ScrollFrame(self._run_root, bg=_BG)
        sf.pack(fill="both", expand=True)
        body = sf.inner

        # ── PIN display ───────────────────────────────────────────────────
        self._section(body, "Connection PIN")
        pin_card = tk.Frame(body, bg=_SURFACE)
        pin_card.pack(fill="x", padx=20, pady=(0, 4))
        pin_inner = tk.Frame(pin_card, bg=_SURFACE)
        pin_inner.pack(fill="x", padx=16, pady=12)

        tk.Label(pin_inner,
                 text="Enter this PIN on your phone:",
                 bg=_SURFACE, fg=_TEXT_DIM,
                 font=("Segoe UI", 9)).pack(anchor="w")

        self._pin_display = tk.Label(
            pin_inner,
            text="──────",
            bg=_SURFACE, fg=_ACCENT,
            font=("Segoe UI", 32, "bold"),
            pady=4)
        self._pin_display.pack(anchor="w")

        self._pin_sub = tk.Label(
            pin_inner,
            text="Waiting for server to generate PIN…",
            bg=_SURFACE, fg=_TEXT_DIM,
            font=("Segoe UI", 8))
        self._pin_sub.pack(anchor="w")

        self._hr(body)

        # ── QR code ───────────────────────────────────────────────────────
        self._section(body, "Scan QR Code with Phone Camera")
        qr_card = tk.Frame(body, bg=_SURFACE)
        qr_card.pack(fill="x", padx=20, pady=(0, 4))
        qr_inner = tk.Frame(qr_card, bg=_SURFACE)
        qr_inner.pack(padx=16, pady=12)

        self._qr_label = tk.Label(
            qr_inner,
            text="🔲  QR code appears once server is ready…",
            bg=_SURFACE, fg=_TEXT_DIM,
            font=("Segoe UI", 9))
        self._qr_label.pack()

        self._url_label = tk.Label(
            qr_inner, text="",
            bg=_SURFACE, fg=_ACCENT,
            font=("Segoe UI", 9, "bold"))
        self._url_label.pack(pady=(4, 0))

        self._hr(body)

        # ── Logs ──────────────────────────────────────────────────────────
        self._section(body, "Server Logs")
        log_card = tk.Frame(body, bg=_SURFACE)
        log_card.pack(fill="x", padx=20, pady=(0, 4))

        self._log_text = tk.Text(
            log_card,
            bg="#0a0c14", fg=_TEXT_DIM,
            font=("Consolas", 8),
            height=12, wrap="word",
            relief="flat", bd=0,
            state="disabled")
        self._log_text.pack(fill="both", expand=True, padx=2, pady=2)
        self._log_text.tag_config("INFO",    foreground=_TEXT_DIM)
        self._log_text.tag_config("WARNING", foreground=_WARNING)
        self._log_text.tag_config("ERROR",   foreground=_DANGER)
        self._log_text.tag_config("SUCCESS", foreground=_SUCCESS)

        self._hr(body)

        # ── Stop button ───────────────────────────────────────────────────
        foot = tk.Frame(body, bg=_BG, pady=14)
        foot.pack(fill="x", padx=20)
        tk.Button(foot, text="⏹  Stop Server",
                  bg=_DANGER, fg="#ffffff",
                  activebackground="#c0392b",
                  font=("Segoe UI", 10, "bold"),
                  relief="flat", padx=20, pady=9,
                  cursor="hand2",
                  command=self._on_stop).pack(side="right")

        # Mark running view as ready BEFORE applying pending notifications
        self._running_view_ready = True

        # Apply anything that arrived before the view was built
        global _pending_pin, _pending_qr
        if _pending_pin is not None:
            self._apply_pin(_pending_pin)
            _pending_pin = None
        if _pending_qr is not None:
            self._apply_qr(_pending_qr)
            _pending_qr = None

    # ── PIN display ───────────────────────────────────────────────────────

    def show_pin(self, pin: str):
        """Thread-safe entry point called from server thread."""
        self.after(0, lambda: self._apply_pin(pin))

    def _apply_pin(self, pin: str):
        """Must be called on the main thread."""
        if not self._running_view_ready:
            global _pending_pin
            _pending_pin = pin
            return
        if pin:
            self._pin_display.config(text=pin, fg=_ACCENT)
            self._pin_sub.config(
                text="Show this number on your phone to connect",
                fg=_SUCCESS)
        else:
            self._pin_display.config(text="Disabled", fg=_TEXT_DIM)
            self._pin_sub.config(
                text="PIN authentication is off",
                fg=_TEXT_DIM)

    # ── QR display ────────────────────────────────────────────────────────

    def show_qr(self, app_url: str):
        """Thread-safe entry point called from server thread."""
        self.after(0, lambda: self._apply_qr(app_url))

    def _apply_qr(self, app_url: str):
        """Must be called on the main thread."""
        if not self._running_view_ready:
            global _pending_qr
            _pending_qr = app_url
            return
        try:
            import qrcode
            from PIL import Image, ImageTk
            qr = qrcode.QRCode(
                version=None,
                error_correction=qrcode.constants.ERROR_CORRECT_L,
                box_size=5, border=2)
            qr.add_data(app_url)
            qr.make(fit=True)
            img = qr.make_image(fill_color=_TEXT, back_color=_SURFACE)
            img = img.resize((200, 200), Image.NEAREST)
            self._qr_photo = ImageTk.PhotoImage(img)
            self._qr_label.config(image=self._qr_photo, text="")
        except Exception:
            # PIL/qrcode not available — show text fallback
            self._qr_label.config(
                text=f"Open in phone browser:\n{app_url}",
                fg=_TEXT, font=("Segoe UI", 9))

        self._url_label.config(text=app_url)
        if hasattr(self, "_status_bar"):
            self._status_bar.config(
                text="✅  Server ready — scan QR or enter URL on phone",
                fg=_SUCCESS)

    # ── Log polling ───────────────────────────────────────────────────────

    def _poll_log(self):
        try:
            while True:
                line = log_queue.get_nowait()
                if hasattr(self, "_log_text"):
                    self._log_text.config(state="normal")
                    tag = ("WARNING" if any(x in line for x in ("WARNING", "⚠"))
                           else "ERROR"   if any(x in line for x in ("ERROR",   "❌"))
                           else "SUCCESS" if any(x in line for x in ("✅", "ready"))
                           else "INFO")
                    self._log_text.insert("end", line + "\n", tag)
                    self._log_text.see("end")
                    self._log_text.config(state="disabled")
        except queue.Empty:
            pass
        self.after(200, self._poll_log)

    # ── Actions ───────────────────────────────────────────────────────────

    def _on_start(self):
        mode = self._mode_var.get()
        args = argparse.Namespace(
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
        # Switch to running dashboard first, then start server thread
        self.after(0, self._show_running_view)
        self._server_thread = threading.Thread(
            target=self._server_runner,
            args=(args,),
            daemon=True,
            name="phonekey-server")
        self._server_thread.start()

    def _on_stop(self):
        """Stop the server but keep the window open showing a stopped state."""
        self._stop_event.set()
        if hasattr(self, "_status_bar"):
            self._status_bar.config(
                text="⏹  Server stopped. Close window to exit.",
                fg=_WARNING)
        # Signal server shutdown via the global stop mechanism
        try:
            from server import _stop_event_ref, _loop_ref
            if _stop_event_ref and _loop_ref:
                _loop_ref.call_soon_threadsafe(_stop_event_ref.set)
        except Exception:
            pass

    def _on_close(self):
        """Close window and exit."""
        self._on_stop()
        self.after(300, self.destroy)
        self.after(400, lambda: sys.exit(0))


# ── Public API ────────────────────────────────────────────────────────────────

def run_gui(server_runner: Callable[[Namespace], None]) -> None:
    global _app_ref
    app = PhoneKeyApp(server_runner)
    _app_ref = app
    app.mainloop()


def notify_qr(url: str):
    """Thread-safe: push QR URL to GUI. Safe to call before view is built."""
    if _app_ref and _app_ref.winfo_exists():
        _app_ref.show_qr(url)          # show_qr() uses after() internally


def notify_pin(pin: Optional[str]):
    """Thread-safe: push PIN to GUI. Safe to call before view is built."""
    if _app_ref and _app_ref.winfo_exists():
        _app_ref.show_pin(pin or "")


def log_to_gui(text: str):
    log_queue.put(text)