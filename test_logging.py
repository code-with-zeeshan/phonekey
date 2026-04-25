"""
Tests for PhoneKey Centralized Logging Architecture

Tests cover:
- Log schema validation
- Log record creation
- Structured formatters
- Context management
- Log handlers
- Configuration loading
"""

import json
import logging
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch

# Add current directory to path
sys.path.insert(0, str(Path(__file__).parent))

try:
    from logging_schema import (
        LogRecord, LogLevel, LogSchemaValidator,
        debug, info, warning, error, critical
    )
    from logging_config import LoggingConfig, setup_logging, get_logger
    from logging_formatter import (
        StructuredJSONFormatter, StructuredTextFormatter, ColoredFormatter
    )
    from logging_context import LogContext, log_context, trace_span, get_context
    from logging_handlers import (
        RotatingFileHandler, AsyncHandler, MemoryHandler,
        ContextEnrichingHandler
    )
    from logging_utils import (
        TimedLog, timed_log, ExceptionLogger, LogSampler,
        StructuredLogger, PiiRedactionFilter
    )
    LOGGING_AVAILABLE = True
except ImportError as e:
    LOGGING_AVAILABLE = False
    print(f"\nLogging module not available: {e}")


class TestLogLevel(unittest.TestCase):
    """Test LogLevel enum."""

    def test_level_values(self):
        """Test that log level values match Python logging."""
        self.assertEqual(LogLevel.DEBUG, 10)
        self.assertEqual(LogLevel.INFO, 20)
        self.assertEqual(LogLevel.WARNING, 30)
        self.assertEqual(LogLevel.ERROR, 40)
        self.assertEqual(LogLevel.CRITICAL, 50)

    def test_from_name(self):
        """Test converting level name to enum."""
        self.assertEqual(LogLevel.from_name('DEBUG'), LogLevel.DEBUG)
        self.assertEqual(LogLevel.from_name('INFO'), LogLevel.INFO)
        self.assertEqual(LogLevel.from_name('ERROR'), LogLevel.ERROR)

    def test_from_name_invalid(self):
        """Test invalid level name raises ValueError."""
        with self.assertRaises(ValueError):
            LogLevel.from_name('INVALID')


class TestLogRecord(unittest.TestCase):
    """Test LogRecord creation and validation."""

    def setUp(self):
        """Set up test fixtures."""
        self.record = LogRecord.create(
            level=LogLevel.INFO,
            service='test_service',
            module='test_module',
            message='Test message',
            trace_id='trace-123',
            span_id='span-456',
            user_id='user-789',
            device_id='device-abc',
            metadata={'key': 'value'},
        )

    def test_required_fields(self):
        """Test that all required fields are present."""
        self.assertIsNotNone(self.record.timestamp)
        self.assertEqual(self.record.level, 'INFO')
        self.assertEqual(self.record.level_value, 20)
        self.assertEqual(self.record.service, 'test_service')
        self.assertEqual(self.record.module, 'test_module')
        self.assertEqual(self.record.message, 'Test message')
        self.assertEqual(self.record.trace_id, 'trace-123')
        self.assertEqual(self.record.span_id, 'span-456')

    def test_optional_fields(self):
        """Test that optional fields are set correctly."""
        self.assertEqual(self.record.user_id, 'user-789')
        self.assertEqual(self.record.device_id, 'device-abc')
        self.assertEqual(self.record.metadata, {'key': 'value'})

    def test_to_dict(self):
        """Test conversion to dictionary."""
        data = self.record.to_dict()
        self.assertIn('timestamp', data)
        self.assertIn('level', data)
        self.assertIn('service', data)
        self.assertIn('message', data)
        self.assertIn('trace_id', data)
        self.assertIn('span_id', data)
        self.assertIn('user_id', data)
        self.assertIn('device_id', data)
        self.assertIn('metadata', data)

    def test_to_dict_excludes_none(self):
        """Test that None values are excluded from dict."""
        record = LogRecord.create(
            level=LogLevel.INFO,
            service='test',
            module='test',
            message='test',
            trace_id='t',
            span_id='s',
        )
        data = record.to_dict()
        self.assertNotIn('user_id', data)
        self.assertNotIn('device_id', data)
        self.assertNotIn('metadata', data)

    def test_to_json(self):
        """Test JSON serialization."""
        json_str = self.record.to_json()
        data = json.loads(json_str)
        self.assertEqual(data['level'], 'INFO')
        self.assertEqual(data['service'], 'test_service')

    def test_timestamp_is_iso_format(self):
        """Test that timestamp is in ISO 8601 format."""
        dt = datetime.fromisoformat(self.record.timestamp.replace('Z', '+00:00'))
        self.assertIsInstance(dt, datetime)

    def test_create_with_exception(self):
        """Test creating record with exception info."""
        try:
            raise ValueError("Test error")
        except ValueError:
            record = error('test', 'test', 'Test message', exc=ValueError("Test error"))
            self.assertEqual(record.error_type, 'ValueError')
            self.assertIsNotNone(record.stack_trace)


class TestLogSchemaValidator(unittest.TestCase):
    """Test log record validation."""

    def test_valid_record(self):
        """Test that valid record passes validation."""
        record = LogRecord.create(
            level=LogLevel.INFO,
            service='test',
            module='test',
            message='test',
            trace_id='t',
            span_id='s',
        )
        is_valid, errors = LogSchemaValidator.validate(record)
        self.assertTrue(is_valid)
        self.assertEqual(len(errors), 0)

    def test_invalid_level(self):
        """Test that invalid level fails validation."""
        record = LogRecord.create(
            level=LogLevel.INFO,
            service='test',
            module='test',
            message='test',
            trace_id='t',
            span_id='s',
        )
        # Manually corrupt the level
        record.level = 'INVALID'
        is_valid, errors = LogSchemaValidator.validate(record)
        self.assertFalse(is_valid)
        self.assertTrue(any('Invalid level' in e for e in errors))

    def test_missing_required_field(self):
        """Test that missing required field fails validation."""
        record = LogRecord.create(
            level=LogLevel.INFO,
            service='test',
            module='test',
            message='test',
            trace_id='t',
            span_id='s',
        )
        # Manually remove a required field
        record.timestamp = None
        is_valid, errors = LogSchemaValidator.validate(record)
        self.assertFalse(is_valid)
        self.assertTrue(any('Missing required field' in e for e in errors))


class TestLogRecordCreation(unittest.TestCase):
    """Test convenience functions for creating log records."""

    def test_debug(self):
        """Test debug() function."""
        record = debug('service', 'module', 'message')
        self.assertEqual(record.level, 'DEBUG')
        self.assertEqual(record.level_value, 10)

    def test_info(self):
        """Test info() function."""
        record = info('service', 'module', 'message')
        self.assertEqual(record.level, 'INFO')
        self.assertEqual(record.level_value, 20)

    def test_warning(self):
        """Test warning() function."""
        record = warning('service', 'module', 'message')
        self.assertEqual(record.level, 'WARNING')
        self.assertEqual(record.level_value, 30)

    def test_error(self):
        """Test error() function."""
        record = error('service', 'module', 'message')
        self.assertEqual(record.level, 'ERROR')
        self.assertEqual(record.level_value, 40)

    def test_critical(self):
        """Test critical() function."""
        record = critical('service', 'module', 'message')
        self.assertEqual(record.level, 'CRITICAL')
        self.assertEqual(record.level_value, 50)

    def test_auto_trace_id(self):
        """Test that trace_id is auto-generated."""
        record = info('service', 'module', 'message')
        self.assertIsNotNone(record.trace_id)
        self.assertNotEqual(record.trace_id, '')

    def test_auto_span_id(self):
        """Test that span_id is auto-generated."""
        record = info('service', 'module', 'message')
        self.assertIsNotNone(record.span_id)
        self.assertNotEqual(record.span_id, '')


class TestStructuredJSONFormatter(unittest.TestCase):
    """Test JSON formatter."""

    def setUp(self):
        """Set up test fixtures."""
        self.formatter = StructuredJSONFormatter()

    def test_format_with_structured_record(self):
        """Test formatting with structured record."""
        log_record = LogRecord.create(
            level=LogLevel.INFO,
            service='test',
            module='test',
            message='test message',
            trace_id='trace-123',
            span_id='span-456',
        )
        record = logging.LogRecord(
            name='test',
            level=logging.INFO,
            pathname='',
            lineno=0,
            msg='',
            args=(),
            exc_info=None,
        )
        record.structured_record = log_record
        output = self.formatter.format(record)
        data = json.loads(output)
        self.assertEqual(data['level'], 'INFO')
        self.assertEqual(data['service'], 'test')
        self.assertEqual(data['message'], 'test message')

    def test_format_without_structured_record(self):
        """Test formatting without structured record."""
        record = logging.LogRecord(
            name='test_logger',
            level=logging.INFO,
            pathname='/test.py',
            lineno=10,
            msg='Test message',
            args=(),
            exc_info=None,
        )
        output = self.formatter.format(record)
        data = json.loads(output)
        self.assertEqual(data['level'], 'INFO')
        self.assertEqual(data['service'], 'test_logger')
        self.assertEqual(data['message'], 'Test message')


class TestStructuredTextFormatter(unittest.TestCase):
    """Test text formatter."""

    def setUp(self):
        """Set up test fixtures."""
        self.formatter = StructuredTextFormatter()

    def test_format_with_structured_record(self):
        """Test formatting with structured record."""
        log_record = LogRecord.create(
            level=LogLevel.INFO,
            service='test',
            module='test',
            message='test message',
            trace_id='trace-123',
            span_id='span-456',
            device_id='device-789',
        )
        record = logging.LogRecord(
            name='test',
            level=logging.INFO,
            pathname='',
            lineno=0,
            msg='',
            args=(),
            exc_info=None,
        )
        record.structured_record = log_record
        output = self.formatter.format(record)
        self.assertIn('test message', output)
        self.assertIn('test/test', output)


class TestLoggingContext(unittest.TestCase):
    """Test logging context management."""

    def setUp(self):
        """Set up test fixtures."""
        LogContext.clear_context()

    def tearDown(self):
        """Clean up after tests."""
        LogContext.clear_context()

    def test_set_and_get_trace_id(self):
        """Test setting and getting trace ID."""
        trace_id = LogContext.set_trace_id('trace-123')
        self.assertEqual(trace_id, 'trace-123')
        self.assertEqual(LogContext.get_trace_id(), 'trace-123')

    def test_set_and_get_device_id(self):
        """Test setting and getting device ID."""
        LogContext.set_device_id('device-123')
        self.assertEqual(LogContext.get_device_id(), 'device-123')

    def test_set_and_get_user_id(self):
        """Test setting and getting user ID."""
        LogContext.set_user_id('user-123')
        self.assertEqual(LogContext.get_user_id(), 'user-123')

    def test_get_context(self):
        """Test getting full context."""
        LogContext.set_trace_id('trace-123')
        LogContext.set_device_id('device-123')
        context = LogContext.get_context()
        self.assertEqual(context['trace_id'], 'trace-123')
        self.assertEqual(context['device_id'], 'device-123')

    def test_log_context_manager(self):
        """Test log_context context manager."""
        LogContext.set_device_id('old-device')
        with log_context(device_id='device-123', user_id='user-456'):
            self.assertEqual(LogContext.get_device_id(), 'device-123')
            self.assertEqual(LogContext.get_user_id(), 'user-456')
        # Context should be restored
        self.assertEqual(LogContext.get_device_id(), 'old-device')

    def test_trace_span_manager(self):
        """Test trace_span context manager."""
        old_span = LogContext.get_span_id()
        with trace_span('test_span', device_id='device-123') as span_id:
            self.assertNotEqual(span_id, old_span)
            self.assertEqual(LogContext.get_device_id(), 'device-123')
        # Span ID should be restored
        self.assertEqual(LogContext.get_span_id(), old_span)

    def test_context_filter(self):
        """Test ContextFilter injects context into log records."""
        from logging_context import ContextFilter
        with log_context(device_id='device-123', trace_id='trace-456'):
            record = logging.LogRecord(
                name='test',
                level=logging.INFO,
                pathname='',
                lineno=0,
                msg='test',
                args=(),
                exc_info=None,
            )
            filter_obj = ContextFilter()
            filter_obj.filter(record)
            self.assertEqual(record.device_id, 'device-123')
            self.assertEqual(record.trace_id, 'trace-456')


class TestLogHandlers(unittest.TestCase):
    """Test log handlers."""

    def setUp(self):
        """Set up test fixtures."""
        self.temp_dir = tempfile.mkdtemp()

    def tearDown(self):
        """Clean up after tests."""
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_rotating_file_handler(self):
        """Test rotating file handler creation."""
        log_file = Path(self.temp_dir) / 'test.log'
        handler = RotatingFileHandler(
            filename=str(log_file),
            maxBytes=1024,
            backupCount=3,
            compress=False,
        )
        self.assertIsNotNone(handler)
        handler.close()

    def test_memory_handler(self):
        """Test memory handler."""
        handler = MemoryHandler(capacity=100)
        record = logging.LogRecord(
            name='test',
            level=logging.INFO,
            pathname='',
            lineno=0,
            msg='test',
            args=(),
            exc_info=None,
        )
        handler.emit(record)
        self.assertEqual(len(handler), 1)
        records = handler.get_records()
        self.assertEqual(len(records), 1)
        handler.clear()
        self.assertEqual(len(handler), 0)

    def test_context_enriching_handler(self):
        """Test context enriching handler."""
        target = MemoryHandler()
        handler = ContextEnrichingHandler(target)
        with log_context(device_id='device-123', trace_id='trace-456'):
            record = logging.LogRecord(
                name='test',
                level=logging.INFO,
                pathname='',
                lineno=0,
                msg='test',
                args=(),
                exc_info=None,
            )
            handler.emit(record)
        records = target.get_records()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].device_id, 'device-123')
        self.assertEqual(records[0].trace_id, 'trace-456')


class TestLoggingUtils(unittest.TestCase):
    """Test logging utilities."""

    def test_timed_log_context_manager(self):
        """Test TimedLog context manager."""
        logger = logging.getLogger('test')
        with TimedLog(logger, 'test operation'):
            time.sleep(0.01)

    def test_timed_log_decorator(self):
        """Test timed_log decorator."""
        logger = logging.getLogger('test')
        
        @timed_log(logger, 'test function')
        def test_func():
            return 42
        
        result = test_func()
        self.assertEqual(result, 42)

    def test_exception_logger(self):
        """Test ExceptionLogger."""
        logger = logging.getLogger('test')
        try:
            raise ValueError("Test error")
        except ValueError:
            record = ExceptionLogger.log_exception(
                logger, 'Test message', ValueError("Test error")
            )
            self.assertEqual(record.error_type, 'ValueError')
            self.assertIsNotNone(record.stack_trace)

    def test_log_sampler(self):
        """Test LogSampler."""
        sampler = LogSampler(rate_limit=10, sample_rate=1.0)
        # Should allow logging initially
        self.assertTrue(sampler.should_log())

    def test_structured_logger(self):
        """Test StructuredLogger."""
        logger = logging.getLogger('test')
        slogger = StructuredLogger(logger, 'test_service')
        # Just ensure methods don't raise
        slogger.debug('debug message')
        slogger.info('info message')
        slogger.warning('warning message')
        slogger.error('error message')
        slogger.critical('critical message')

    def test_pii_redaction_filter(self):
        """Test PII redaction."""
        filter_obj = PiiRedactionFilter()
        record = logging.LogRecord(
            name='test',
            level=logging.INFO,
            pathname='',
            lineno=0,
            msg='Email: email',
            args=(),
            exc_info=None,
        )
        filter_obj.filter(record)
        self.assertNotIn('email', record.msg)


class TestLoggingConfig(unittest.TestCase):
    """Test logging configuration."""

    def test_development_config(self):
        """Test development environment config."""
        config = LoggingConfig(environment='development')
        self.assertEqual(config.environment, 'development')
        self.assertIn('console', config.config['handlers'])

    def test_production_config(self):
        """Test production environment config."""
        config = LoggingConfig(environment='production')
        self.assertEqual(config.environment, 'production')
        self.assertIn('rotating_file', config.config['handlers'])

    def test_testing_config(self):
        """Test testing environment config."""
        config = LoggingConfig(environment='testing')
        self.assertEqual(config.environment, 'testing')
        self.assertIn('memory', config.config['handlers'])

    def test_setup_logging(self):
        """Test setup_logging function."""
        config = setup_logging(environment='testing')
        self.assertIsNotNone(config)
        logger = get_logger('test')
        self.assertIsNotNone(logger)


if __name__ == '__main__':
    unittest.main(verbosity=2)
