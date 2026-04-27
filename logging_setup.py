"""
PhoneKey — Logging Setup
Contract : Configure application-wide logging in one call; return the root logger.
"""
import logging
import sys


def setup_logging(level: str = "INFO") -> logging.Logger:
    """
    Configure stdout logging for PhoneKey.

    Args:
        level: One of DEBUG / INFO / WARNING / ERROR  (case-insensitive).

    Returns:
        The "phonekey" root logger, ready to use.
    """
    numeric = getattr(logging, level.upper(), logging.INFO)

    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(numeric)
    handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s [%(levelname)s] %(message)s",
            datefmt="%H:%M:%S",
        )
    )

    root = logging.getLogger("phonekey")
    root.setLevel(numeric)
    # Guard against duplicate handlers when called more than once (tests, etc.)
    if not root.handlers:
        root.addHandler(handler)

    return root


def get_logger(name: str) -> logging.Logger:
    """
    Return a child logger scoped under the "phonekey" namespace.

    Args:
        name: Dot-namespaced name, e.g. "phonekey.http" or "phonekey.tunnel".

    Returns:
        logging.Logger instance that inherits the root handler automatically.
    """
    return logging.getLogger(name)