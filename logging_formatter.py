"""
PhoneKey Logging Formatters

Provides structured formatters for consistent log output.
Supports JSON, text, and colored console output.
"""

import json
import logging
import os
import sys
from typing import Any, Dict, Optional

from logging_schema import LogRecord


COLORAMA_AVAILABLE = False
try:
    from colorama import Fore, Style, init
    init(autoreset=True)
    COLORAMA_AVAILABLE = True
    FORE = Fore
    STYLE = Style
except ImportError:
    # Define dummy color constants
    class DummyColors:
        def __getattr__(self, name):
            return ''
    FORE = DummyColors()
    STYLE = DummyColors()


class StructuredJSONFormatter(logging.Formatter):
    """
    Formats log records as structured JSON.
    
    If the log record has a 'structured_record' attribute (LogRecord instance),
    it uses that directly. Otherwise, it creates a LogRecord from the
    standard logging record.
    """

    def format(self, record: logging.LogRecord) -> str:
        """Format the log record as JSON."""
        if hasattr(record, 'structured_record') and isinstance(record.structured_record, LogRecord):
            log_data = record.structured_record.to_dict()
        else:
            log_data = self._create_log_record(record).to_dict()

        # Add any extra attributes
        for key, value in record.__dict__.items():
            if key not in logging.LogRecord('', 0, '', 0, '', (), None).__dict__:
                if key not in log_data:
                    log_data[key] = self._safe_serialize(value)

        return json.dumps(log_data, default=str, ensure_ascii=False)

    def _create_log_record(self, record: logging.LogRecord) -> LogRecord:
        """Create a LogRecord from a standard logging record."""
        level_map = {
            logging.DEBUG: 'DEBUG',
            logging.INFO: 'INFO',
            logging.WARNING: 'WARNING',
            logging.ERROR: 'ERROR',
            logging.CRITICAL: 'CRITICAL',
        }

        # Extract context from record
        trace_id = getattr(record, 'trace_id', None)
        span_id = getattr(record, 'span_id', None)
        device_id = getattr(record, 'device_id', None)
        user_id = getattr(record, 'user_id', None)
        session_id = getattr(record, 'session_id', None)

        # Extract exception info
        error_type = None
        stack_trace = None
        if record.exc_info:
            error_type = record.exc_info[0].__name__ if record.exc_info[0] else None
            stack_trace = self.formatException(record.exc_info)

        return LogRecord(
            timestamp=self._format_time(record),
            level=level_map.get(record.levelno, 'INFO'),
            level_value=record.levelno,
            service=record.name,
            module=record.module,
            message=record.getMessage(),
            trace_id=trace_id or self._generate_trace_id(),
            span_id=span_id or self._generate_span_id(),
            user_id=user_id,
            device_id=device_id,
            session_id=session_id,
            error_type=error_type,
            stack_trace=stack_trace,
            file=record.pathname,
            line=record.lineno,
            function=record.funcName,
            metadata={},
        )

    def _format_time(self, record: logging.LogRecord) -> str:
        """Format timestamp as ISO 8601 UTC."""
        from datetime import datetime, timezone
        dt = datetime.fromtimestamp(record.created, tz=timezone.utc)
        return dt.isoformat()

    def _generate_trace_id(self) -> str:
        """Generate a trace ID."""
        import uuid
        return str(uuid.uuid4())

    def _generate_span_id(self) -> str:
        """Generate a span ID."""
        import uuid
        return str(uuid.uuid4())

    def _safe_serialize(self, value: Any) -> Any:
        """Safely serialize a value for JSON."""
        try:
            json.dumps(value)
            return value
        except (TypeError, ValueError):
            return str(value)


class StructuredTextFormatter(logging.Formatter):
    """
    Formats log records as structured text.
    Human-readable but still structured.
    """

    FORMAT = '%(timestamp)s [%(levelname)-8s] %(service)s/%(module)s: %(message)s'

    def format(self, record: logging.LogRecord) -> str:
        """Format the log record as structured text."""
        if hasattr(record, 'structured_record') and isinstance(record.structured_record, LogRecord):
            log_data = record.structured_record.to_dict()
            parts = [
                log_data.get('timestamp', ''),
                f"[{log_data.get('level', 'INFO'):<8}]",
                f"{log_data.get('service', '')}/{log_data.get('module', '')}:",
                log_data.get('message', ''),
            ]
            # Add context if present
            context_parts = []
            if log_data.get('device_id'):
                context_parts.append(f"device={log_data['device_id'][:8]}")
            if log_data.get('user_id'):
                context_parts.append(f"user={log_data['user_id']}")
            if log_data.get('trace_id'):
                context_parts.append(f"trace={log_data['trace_id'][:8]}")
            if context_parts:
                parts.append(f"({', '.join(context_parts)})")
            return ' '.join(parts)

        # Add custom attributes to record
        record.timestamp = self._format_time(record)
        record.service = record.name
        record.module = record.module

        return super().format(record)

    def _format_time(self, record: logging.LogRecord) -> str:
        """Format timestamp."""
        from datetime import datetime, timezone
        dt = datetime.fromtimestamp(record.created, tz=timezone.utc)
        return dt.strftime('%H:%M:%S.%f')[:-3] + 'Z'


class ColoredFormatter(logging.Formatter):
    """
    Formats log records with colors for console output.
    Only works if colorama is available.
    """

    LEVEL_COLORS = {
        'DEBUG': FORE.CYAN,
        'INFO': FORE.GREEN,
        'WARNING': FORE.YELLOW,
        'ERROR': FORE.RED,
        'CRITICAL': FORE.RED + STYLE.BRIGHT,
    }

    FORMAT = '%(asctime)s [%(levelname)-8s] %(name)s: %(message)s'

    def __init__(self, *args: Any, **kwargs: Any):
        """Initialize the colored formatter."""
        super().__init__(*args, **kwargs)
        self.use_color = COLORAMA_AVAILABLE and sys.stdout.isatty()

    def format(self, record: logging.LogRecord) -> str:
        """Format the log record with colors."""
        levelname = record.levelname
        if self.use_color:
            color = self.LEVEL_COLORS.get(levelname, '')
            reset = Style.RESET_ALL if self.use_color else ''
            record.levelname = f"{color}{levelname}{reset}"

        # Color the message for errors
        if record.levelno >= logging.ERROR and self.use_color:
            record.msg = f"{Fore.RED}{record.msg}{Style.RESET_ALL}"

        return super().format(record)
