"""
PhoneKey Logging Schema

Defines structured log format, severity levels, and log record structure.
Enforces consistent schema across all services and modules.
"""

import json
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import IntEnum
from typing import Any, Dict, Optional
import uuid
import traceback


class LogLevel(IntEnum):
    """Standardized severity levels matching Python logging module."""
    DEBUG = 10
    INFO = 20
    WARNING = 30
    ERROR = 40
    CRITICAL = 50

    @classmethod
    def from_name(cls, name: str) -> "LogLevel":
        """Convert level name to LogLevel enum."""
        name_upper = name.upper()
        if name_upper in cls.__members__:
            return cls[name_upper]
        raise ValueError(f"Invalid log level name: {name}")

    @classmethod
    def to_python_level(cls, level: "LogLevel") -> int:
        """Convert to Python logging level integer."""
        return int(level)


@dataclass
class LogRecord:
    """
    Structured log record schema.
    
    All fields are included in every log entry to ensure consistency.
    Optional fields may be None when not applicable.
    """
    # Required fields
    timestamp: str  # ISO 8601 UTC format
    level: str      # Log level name (DEBUG, INFO, WARNING, ERROR, CRITICAL)
    level_value: int  # Numeric level value
    service: str    # Service name (e.g., "phonekey", "phonekey.tunnel")
    module: str     # Module name (e.g., "server", "tunnel_manager")
    message: str    # Human-readable log message
    trace_id: str   # Correlation ID for distributed tracing
    span_id: str    # Span ID for request tracing

    # Optional fields
    user_id: Optional[str] = None
    device_id: Optional[str] = None
    session_id: Optional[str] = None
    error_type: Optional[str] = None
    stack_trace: Optional[str] = None
    duration_ms: Optional[float] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    file: Optional[str] = None
    line: Optional[int] = None
    function: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary, excluding None values for optional fields."""
        data = asdict(self)
        # Remove None values for optional fields to keep output clean
        optional_fields = [
            'user_id', 'device_id', 'session_id', 'error_type',
            'stack_trace', 'duration_ms', 'file', 'line', 'function'
        ]
        for field_name in optional_fields:
            if data.get(field_name) is None:
                data.pop(field_name, None)
        # Remove empty metadata
        if not data.get('metadata'):
            data.pop('metadata', None)
        return data

    def to_json(self) -> str:
        """Convert to JSON string."""
        return json.dumps(self.to_dict(), default=str)

    @classmethod
    def create(
        cls,
        level: LogLevel,
        service: str,
        module: str,
        message: str,
        trace_id: Optional[str] = None,
        span_id: Optional[str] = None,
        **kwargs: Any,
    ) -> "LogRecord":
        """
        Create a new log record with current timestamp.
        
        Args:
            level: Log severity level
            service: Service name
            module: Module name
            message: Log message
            trace_id: Correlation ID (auto-generated if not provided)
            span_id: Span ID (auto-generated if not provided)
            **kwargs: Additional optional fields
        """
        return cls(
            timestamp=datetime.now(timezone.utc).isoformat(),
            level=level.name,
            level_value=int(level),
            service=service,
            module=module,
            message=message,
            trace_id=trace_id or str(uuid.uuid4()),
            span_id=span_id or str(uuid.uuid4()),
            **kwargs,
        )


class LogSchemaValidator:
    """Validates log records against the schema."""

    REQUIRED_FIELDS = {
        'timestamp', 'level', 'level_value', 'service',
        'module', 'message', 'trace_id', 'span_id'
    }

    VALID_LEVELS = {level.name for level in LogLevel}

    @classmethod
    def validate(cls, record: LogRecord) -> tuple[bool, list[str]]:
        """
        Validate a log record.
        
        Returns:
            Tuple of (is_valid, list_of_errors)
        """
        errors = []

        # Check required fields (check actual fields, not dict)
        for field in cls.REQUIRED_FIELDS:
            value = getattr(record, field, None)
            if value is None:
                errors.append(f"Missing required field: {field}")

        # Validate level
        if record.level not in cls.VALID_LEVELS:
            errors.append(f"Invalid level: {record.level}")

        # Validate timestamp format
        if record.timestamp is not None:
            try:
                datetime.fromisoformat(record.timestamp.replace('Z', '+00:00'))
            except (ValueError, AttributeError):
                errors.append(f"Invalid timestamp format: {record.timestamp}")
        else:
            errors.append(f"Missing required field: timestamp")

        # Validate types
        if not isinstance(record.message, str):
            errors.append(f"Message must be string, got {type(record.message)}")

        if not isinstance(record.metadata, dict):
            errors.append(f"Metadata must be dict, got {type(record.metadata)}")

        return len(errors) == 0, errors


class StructuredLogEncoder(json.JSONEncoder):
    """Custom JSON encoder for log records."""

    def default(self, obj: Any) -> Any:
        if isinstance(obj, LogRecord):
            return obj.to_dict()
        if isinstance(obj, datetime):
            return obj.isoformat()
        if isinstance(obj, uuid.UUID):
            return str(obj)
        if isinstance(obj, LogLevel):
            return obj.name
        return super().default(obj)


# Convenience functions for creating log records
def debug(service: str, module: str, message: str, **kwargs) -> LogRecord:
    """Create DEBUG level log record."""
    return LogRecord.create(LogLevel.DEBUG, service, module, message, **kwargs)


def info(service: str, module: str, message: str, **kwargs) -> LogRecord:
    """Create INFO level log record."""
    return LogRecord.create(LogLevel.INFO, service, module, message, **kwargs)


def warning(service: str, module: str, message: str, **kwargs) -> LogRecord:
    """Create WARNING level log record."""
    return LogRecord.create(LogLevel.WARNING, service, module, message, **kwargs)


def error(service: str, module: str, message: str, exc: Optional[Exception] = None, **kwargs) -> LogRecord:
    """Create ERROR level log record with optional exception."""
    if exc:
        kwargs['error_type'] = type(exc).__name__
        kwargs['stack_trace'] = traceback.format_exc()
    return LogRecord.create(LogLevel.ERROR, service, module, message, **kwargs)


def critical(service: str, module: str, message: str, exc: Optional[Exception] = None, **kwargs) -> LogRecord:
    """Create CRITICAL level log record with optional exception."""
    if exc:
        kwargs['error_type'] = type(exc).__name__
        kwargs['stack_trace'] = traceback.format_exc()
    return LogRecord.create(LogLevel.CRITICAL, service, module, message, **kwargs)
