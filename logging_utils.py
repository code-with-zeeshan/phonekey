"""
PhoneKey Logging Utilities

Provides utilities for structured exception logging, performance timing,
PII redaction, log sampling, and other logging enhancements.
"""

import functools
import logging
import re
import time
import traceback
from typing import Any, Callable, Dict, Optional, Type, TypeVar

from logging_schema import LogRecord, LogLevel


T = TypeVar('T')


# PII patterns for redaction
PII_PATTERNS = [
    (re.compile(r'\b\d{3}-\d{2}-\d{4}\b'), '***-**-****'),  # SSN
    (re.compile(r'\b\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}\b'), '****-****-****-****'),  # Credit card
    (re.compile(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}'), '***@***.***'),  # Email
    (re.compile(r'\b(?:\d{1,3}\.){3}\d{1,3}\b'), '***.***.***.***'),  # IP address
    (re.compile(r'(?i)(password|passwd|pwd|secret|token|api_key|apikey)\s*[:=]\s*[\'"][^\'"]+[\'"]'),
     r'\1 = "***REDACTED***"'),  # Passwords/tokens
    (re.compile(r'(?i)(authorization|bearer)\s+[\w-]+'), r'\1 ***REDACTED***'),  # Auth headers
]


class PiiRedactionFilter(logging.Filter):
    """
    Filter that redacts PII from log messages.
    
    Scans log messages and metadata for sensitive information
    and replaces it with redaction markers.
    """

    def __init__(self, patterns: Optional[list] = None):
        """
        Initialize the PII redaction filter.
        
        Args:
            patterns: Optional list of (regex, replacement) tuples
        """
        super().__init__()
        self.patterns = patterns or PII_PATTERNS

    def filter(self, record: logging.LogRecord) -> bool:
        """Redact PII from log record."""
        # Redact message
        if hasattr(record, 'msg') and isinstance(record.msg, str):
            record.msg = self.redact(record.msg)

        # Redact structured record
        if hasattr(record, 'structured_record') and isinstance(record.structured_record, LogRecord):
            self._redact_structured_record(record.structured_record)

        return True

    def _redact_structured_record(self, record: LogRecord) -> None:
        """Redact PII from structured log record."""
        if record.message:
            record.message = self.redact(record.message)
        if record.metadata:
            for key, value in record.metadata.items():
                if isinstance(value, str):
                    record.metadata[key] = self.redact(value)

    def redact(self, text: str) -> str:
        """
        Redact PII patterns in text.
        
        Args:
            text: Input text
        
        Returns:
            Text with PII redacted
        """
        result = text
        for pattern, replacement in self.patterns:
            result = pattern.sub(replacement, result)
        return result


class ExceptionLogger:
    """
    Utility for structured exception logging.
    
    Captures exceptions with full context and formats them
    for structured logging.
    """

    @staticmethod
    def log_exception(
        logger: logging.Logger,
        message: str,
        exc: Exception,
        level: LogLevel = LogLevel.ERROR,
        **context: Any,
    ) -> LogRecord:
        """
        Log an exception with full context.
        
        Args:
            logger: Logger instance
            message: Log message
            exc: Exception to log
            level: Log level
            **context: Additional context
        
        Returns:
            LogRecord that was created
        """
        import sys

        exc_type = type(exc).__name__
        stack_trace = traceback.format_exc()

        # Extract file and line from traceback
        tb = sys.exc_info()[2]
        frame = tb
        while frame and frame.tb_next:
            frame = frame.tb_next

        record = LogRecord.create(
            level=level,
            service=logger.name,
            module=logger.name.split('.')[-1] if '.' in logger.name else logger.name,
            message=message,
            error_type=exc_type,
            stack_trace=stack_trace,
            file=frame.tb_frame.f_code.co_filename if frame else None,
            line=frame.tb_lineno if frame else None,
            **context,
        )

        # Log using structured record
        logger.log(
            level,
            message,
            exc_info=True,
            extra={'structured_record': record},
        )

        return record

    @staticmethod
    def exception_to_dict(exc: Exception) -> Dict[str, Any]:
        """
        Convert exception to dictionary.
        
        Args:
            exc: Exception to convert
        
        Returns:
            Dictionary representation of exception
        """
        return {
            'type': type(exc).__name__,
            'message': str(exc),
            'traceback': traceback.format_exc(),
        }


class TimedLog:
    """
    Context manager for timing operations and logging duration.
    
    Usage:
        with TimedLog(logger, "Processing request"):
            # Do work
            pass
    """

    def __init__(
        self,
        logger: logging.Logger,
        message: str,
        level: LogLevel = LogLevel.INFO,
        **context: Any,
    ):
        """
        Initialize timed log context manager.
        
        Args:
            logger: Logger instance
            message: Log message
            level: Log level
            **context: Additional context
        """
        self.logger = logger
        self.message = message
        self.level = level
        self.context = context
        self.start_time: Optional[float] = None
        self.duration_ms: Optional[float] = None

    def __enter__(self) -> "TimedLog":
        """Start timing."""
        self.start_time = time.time()
        return self

    def __exit__(self, exc_type: Optional[Type[BaseException]], exc_val: Optional[BaseException], exc_tb: Any) -> bool:
        """Stop timing and log duration."""
        if self.start_time is not None:
            self.duration_ms = (time.time() - self.start_time) * 1000

        context = dict(self.context)
        # Remove duration_ms from context to avoid duplicate kwarg
        context.pop('duration_ms', None)

        if exc_type is not None:
            # Exception occurred
            self.logger.log(
                self.level,
                f"{self.message} (failed after {self.duration_ms:.2f}ms)",
                exc_info=True,
                extra={'structured_record': LogRecord.create(
                    level=LogLevel.ERROR,
                    service=self.logger.name,
                    module=self.logger.name.split('.')[-1] if '.' in self.logger.name else self.logger.name,
                    message=f"{self.message} (failed after {self.duration_ms:.2f}ms)",
                    error_type=exc_type.__name__ if exc_type else None,
                    stack_trace=traceback.format_exc() if exc_val else None,
                    duration_ms=self.duration_ms,
                    **context,
                )},
            )
        else:
            # Success
            self.logger.log(
                self.level,
                f"{self.message} (completed in {self.duration_ms:.2f}ms)",
                extra={'structured_record': LogRecord.create(
                    level=self.level,
                    service=self.logger.name,
                    module=self.logger.name.split('.')[-1] if '.' in self.logger.name else self.logger.name,
                    message=f"{self.message} (completed in {self.duration_ms:.2f}ms)",
                    duration_ms=self.duration_ms,
                    **context,
                )},
            )

        return False  # Don't suppress exception


def timed_log(
    logger: logging.Logger,
    message: str,
    level: LogLevel = LogLevel.INFO,
    **context: Any,
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """
    Decorator for timing function execution and logging duration.
    
    Usage:
        @timed_log(logger, "Processing request")
        def process_request():
            pass
    
    Args:
        logger: Logger instance
        message: Log message
        level: Log level
        **context: Additional context
    
    Returns:
        Decorator function
    """
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> T:
            start_time = time.time()
            try:
                result = func(*args, **kwargs)
                duration_ms = (time.time() - start_time) * 1000
                # Remove duration_ms from context to avoid duplicate kwarg
                ctx = dict(context)
                ctx.pop('duration_ms', None)
                logger.log(
                    level,
                    f"{message} (completed in {duration_ms:.2f}ms)",
                    extra={'structured_record': LogRecord.create(
                        level=level,
                        service=logger.name,
                        module=logger.name.split('.')[-1] if '.' in logger.name else logger.name,
                        message=f"{message} (completed in {duration_ms:.2f}ms)",
                        duration_ms=duration_ms,
                        **ctx,
                    )},
                )
                return result
            except Exception as exc:
                duration_ms = (time.time() - start_time) * 1000
                ctx = dict(context)
                ctx.pop('duration_ms', None)
                logger.log(
                    LogLevel.ERROR,
                    f"{message} (failed after {duration_ms:.2f}ms)",
                    exc_info=True,
                    extra={'structured_record': LogRecord.create(
                        level=LogLevel.ERROR,
                        service=logger.name,
                        module=logger.name.split('.')[-1] if '.' in logger.name else logger.name,
                        message=f"{message} (failed after {duration_ms:.2f}ms)",
                        error_type=type(exc).__name__,
                        stack_trace=traceback.format_exc(),
                        duration_ms=duration_ms,
                        **ctx,
                    )},
                )
                raise
        return wrapper
    return decorator


class LogSampler:
    """
    Samples high-frequency logs to prevent log flooding.
    
    Supports rate limiting and probabilistic sampling.
    """

    def __init__(self, rate_limit: Optional[int] = None, sample_rate: float = 1.0):
        """
        Initialize log sampler.
        
        Args:
            rate_limit: Maximum logs per second (None for no limit)
            sample_rate: Probability of logging (0.0 to 1.0)
        """
        self.rate_limit = rate_limit
        self.sample_rate = sample_rate
        self._count = 0
        self._last_reset = time.time()
        self._lock = threading.Lock()

    def should_log(self) -> bool:
        """
        Determine if a log should be emitted.
        
        Returns:
            True if log should be emitted
        """
        import random

        # Probabilistic sampling
        if self.sample_rate < 1.0 and random.random() > self.sample_rate:
            return False

        # Rate limiting
        if self.rate_limit is not None:
            with self._lock:
                now = time.time()
                if now - self._last_reset >= 1.0:
                    self._count = 0
                    self._last_reset = now

                if self._count >= self.rate_limit:
                    return False

                self._count += 1

        return True


class StructuredLogger:
    """
    Convenience wrapper for structured logging.
    
    Provides methods for creating structured log records.
    """

    def __init__(self, logger: logging.Logger, service: Optional[str] = None):
        """
        Initialize structured logger.
        
        Args:
            logger: Logger instance
            service: Service name (defaults to logger name)
        """
        self.logger = logger
        self.service = service or logger.name

    def debug(self, message: str, **kwargs: Any) -> None:
        """Log DEBUG level message."""
        self._log(LogLevel.DEBUG, message, **kwargs)

    def info(self, message: str, **kwargs: Any) -> None:
        """Log INFO level message."""
        self._log(LogLevel.INFO, message, **kwargs)

    def warning(self, message: str, **kwargs: Any) -> None:
        """Log WARNING level message."""
        self._log(LogLevel.WARNING, message, **kwargs)

    def error(self, message: str, exc: Optional[Exception] = None, **kwargs: Any) -> None:
        """Log ERROR level message."""
        if exc:
            kwargs['error_type'] = type(exc).__name__
            kwargs['stack_trace'] = traceback.format_exc()
        self._log(LogLevel.ERROR, message, **kwargs)

    def critical(self, message: str, exc: Optional[Exception] = None, **kwargs: Any) -> None:
        """Log CRITICAL level message."""
        if exc:
            kwargs['error_type'] = type(exc).__name__
            kwargs['stack_trace'] = traceback.format_exc()
        self._log(LogLevel.CRITICAL, message, **kwargs)

    def _log(self, level: LogLevel, message: str, **kwargs: Any) -> None:
        """Create and emit structured log record."""
        module = kwargs.pop('module', self.service.split('.')[-1] if '.' in self.service else self.service)

        record = LogRecord.create(
            level=level,
            service=self.service,
            module=module,
            message=message,
            **kwargs,
        )

        self.logger.log(
            int(level),
            message,
            extra={'structured_record': record},
        )


# Threading import for LogSampler
import threading
