"""
PhoneKey Logging Context Utilities

Manages thread-local and async context for log enrichment.
Provides request-scoped metadata (trace_id, user_id, device_id, etc.)
and automatic correlation across async boundaries.
"""

import asyncio
import contextlib
import logging
import threading
import uuid
from contextvars import ContextVar
from typing import Any, Dict, Optional


# Thread-local storage for synchronous context
_thread_local = threading.local()

# ContextVar for async context (Python 3.7+)
_context_vars: Dict[str, ContextVar] = {}


def _get_or_create_var(name: str, default: Any = None) -> ContextVar:
    """Get or create a ContextVar."""
    if name not in _context_vars:
        _context_vars[name] = ContextVar(name, default=default)
    return _context_vars[name]


# Define context variables
TRACE_ID_VAR = _get_or_create_var('trace_id', '')
SPAN_ID_VAR = _get_or_create_var('span_id', '')
DEVICE_ID_VAR = _get_or_create_var('device_id', '')
USER_ID_VAR = _get_or_create_var('user_id', '')
SESSION_ID_VAR = _get_or_create_var('session_id', '')
REQUEST_ID_VAR = _get_or_create_var('request_id', '')


class LogContext:
    """Manages logging context for correlation and enrichment."""

    @staticmethod
    def get_context() -> Dict[str, Any]:
        """
        Get current logging context.
        
        Returns:
            Dictionary of context values
        """
        return {
            'trace_id': LogContext.get_trace_id(),
            'span_id': LogContext.get_span_id(),
            'device_id': LogContext.get_device_id(),
            'user_id': LogContext.get_user_id(),
            'session_id': LogContext.get_session_id(),
            'request_id': LogContext.get_request_id(),
        }

    @staticmethod
    def set_context(**kwargs: Any) -> None:
        """
        Set multiple context values.
        
        Args:
            **kwargs: Context key-value pairs
        """
        for key, value in kwargs.items():
            if value is not None:
                setattr(LogContext, key, value)

    @staticmethod
    def clear_context() -> None:
        """Clear all context values."""
        for var in _context_vars.values():
            try:
                var.set(None)
            except:
                pass
        _thread_local.__dict__.clear()

    # Trace ID
    @staticmethod
    def get_trace_id() -> str:
        """Get current trace ID."""
        # Try ContextVar first (async)
        trace_id = TRACE_ID_VAR.get()
        if trace_id:
            return trace_id
        # Fall back to thread-local (sync)
        return getattr(_thread_local, 'trace_id', '')

    @staticmethod
    def set_trace_id(trace_id: Optional[str] = None) -> str:
        """
        Set trace ID.
        
        Args:
            trace_id: Trace ID to set (auto-generated if None)
        
        Returns:
            The trace ID that was set
        """
        if trace_id is None:
            trace_id = str(uuid.uuid4())
        TRACE_ID_VAR.set(trace_id)
        _thread_local.trace_id = trace_id
        return trace_id

    @staticmethod
    def generate_trace_id() -> str:
        """Generate a new trace ID."""
        return str(uuid.uuid4())

    # Span ID
    @staticmethod
    def get_span_id() -> str:
        """Get current span ID."""
        span_id = SPAN_ID_VAR.get()
        if span_id:
            return span_id
        return getattr(_thread_local, 'span_id', '')

    @staticmethod
    def set_span_id(span_id: Optional[str] = None) -> str:
        """
        Set span ID.
        
        Args:
            span_id: Span ID to set (auto-generated if None)
        
        Returns:
            The span ID that was set
        """
        if span_id is None:
            span_id = str(uuid.uuid4())
        SPAN_ID_VAR.set(span_id)
        _thread_local.span_id = span_id
        return span_id

    @staticmethod
    def generate_span_id() -> str:
        """Generate a new span ID."""
        return str(uuid.uuid4())

    # Device ID
    @staticmethod
    def get_device_id() -> str:
        """Get current device ID."""
        device_id = DEVICE_ID_VAR.get()
        if device_id:
            return device_id
        return getattr(_thread_local, 'device_id', '')

    @staticmethod
    def set_device_id(device_id: Optional[str]) -> None:
        """Set device ID."""
        if device_id:
            DEVICE_ID_VAR.set(device_id)
            _thread_local.device_id = device_id

    # User ID
    @staticmethod
    def get_user_id() -> str:
        """Get current user ID."""
        user_id = USER_ID_VAR.get()
        if user_id:
            return user_id
        return getattr(_thread_local, 'user_id', '')

    @staticmethod
    def set_user_id(user_id: Optional[str]) -> None:
        """Set user ID."""
        if user_id:
            USER_ID_VAR.set(user_id)
            _thread_local.user_id = user_id

    # Session ID
    @staticmethod
    def get_session_id() -> str:
        """Get current session ID."""
        session_id = SESSION_ID_VAR.get()
        if session_id:
            return session_id
        return getattr(_thread_local, 'session_id', '')

    @staticmethod
    def set_session_id(session_id: Optional[str]) -> None:
        """Set session ID."""
        if session_id:
            SESSION_ID_VAR.set(session_id)
            _thread_local.session_id = session_id

    # Request ID
    @staticmethod
    def get_request_id() -> str:
        """Get current request ID."""
        request_id = REQUEST_ID_VAR.get()
        if request_id:
            return request_id
        return getattr(_thread_local, 'request_id', '')

    @staticmethod
    def set_request_id(request_id: Optional[str] = None) -> str:
        """
        Set request ID.
        
        Args:
            request_id: Request ID to set (auto-generated if None)
        
        Returns:
            The request ID that was set
        """
        if request_id is None:
            request_id = str(uuid.uuid4())
        REQUEST_ID_VAR.set(request_id)
        _thread_local.request_id = request_id
        return request_id


@contextlib.contextmanager
def log_context(**kwargs: Any):
    """
    Context manager for temporarily setting log context.
    
    Usage:
        with log_context(device_id='123', user_id='456'):
            logger.info("This log has device_id and user_id set")
    
    Args:
        **kwargs: Context key-value pairs
    """
    # Save current values
    old_values = {}
    for key in kwargs:
        if hasattr(LogContext, f'get_{key}'):
            old_values[key] = getattr(LogContext, f'get_{key}')()

    # Set new values
    for key, value in kwargs.items():
        if hasattr(LogContext, f'set_{key}'):
            getattr(LogContext, f'set_{key}')(value)

    try:
        yield
    finally:
        # Restore old values
        for key, old_value in old_values.items():
            if hasattr(LogContext, f'set_{key}'):
                getattr(LogContext, f'set_{key}')(old_value)


@contextlib.contextmanager
def trace_span(name: str, **kwargs: Any):
    """
    Context manager for creating a trace span.
    
    Creates a new span ID while maintaining the trace ID.
    Optionally sets additional context.
    
    Args:
        name: Span name
        **kwargs: Additional context
    
    Usage:
        with trace_span("process_request", device_id="123"):
            logger.info("Processing request")
    """
    # Generate new span ID, keep trace ID
    old_span_id = LogContext.get_span_id()
    span_id = LogContext.generate_span_id()
    LogContext.set_span_id(span_id)

    # Set additional context
    old_values = {}
    for key in kwargs:
        if hasattr(LogContext, f'get_{key}'):
            old_values[key] = getattr(LogContext, f'get_{key}')()
    for key, value in kwargs.items():
        if hasattr(LogContext, f'set_{key}'):
            getattr(LogContext, f'set_{key}')(value)

    try:
        yield span_id
    finally:
        # Restore span ID
        LogContext.set_span_id(old_span_id)
        # Restore other context
        for key, old_value in old_values.items():
            if hasattr(LogContext, f'set_{key}'):
                getattr(LogContext, f'set_{key}')(old_value)


class ContextFilter(logging.Filter):
    """
    Logging filter that injects context into log records.
    
    Adds trace_id, span_id, device_id, user_id, session_id to log records.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        """Inject context into log record."""
        context = LogContext.get_context()
        for key, value in context.items():
            if value and not hasattr(record, key):
                setattr(record, key, value)
        return True


# Convenience functions
def get_context() -> Dict[str, Any]:
    """Get current logging context."""
    return LogContext.get_context()


def set_context(**kwargs: Any) -> None:
    """Set logging context."""
    LogContext.set_context(**kwargs)


def clear_context() -> None:
    """Clear logging context."""
    LogContext.clear_context()
