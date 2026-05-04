"""
PhoneKey — Logging Setup
Contract : Configure application-wide logging in one call; return the root logger.
"""
import logging
import sys

def get_logger(name: str) -> logging.Logger:
    """
    Return a child logger scoped under the "phonekey" namespace.

    Args:
        name: Dot-namespaced name, e.g. "phonekey.http" or "phonekey.tunnel".

    Returns:
        logging.Logger instance that inherits the root handler automatically.
    """
    return logging.getLogger(name)


class _GuiSink(logging.Handler):
    """Forwards log records to gui_launcher.log_to_gui()."""
    def emit(self, record):
        try:
            from gui_launcher import log_to_gui
            log_to_gui(self.format(record))
        except Exception:
            pass


def setup_logging(level: str = "INFO") -> logging.Logger:
    """
    Configure stdout logging for PhoneKey.

    Args:
        level: One of DEBUG / INFO / WARNING / ERROR  (case-insensitive).

    Returns:
        The "phonekey" root logger, ready to use.
    """
    root = logging.getLogger("phonekey")
    if root.handlers:
        return root                 # already configured

    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s",
                            datefmt="%H:%M:%S")

    # Console handler
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    root.addHandler(sh)

    # GUI handler (no-op if GUI is not running)
    gh = _GuiSink()
    gh.setFormatter(fmt)
    root.addHandler(gh)

    return root    