# PhoneKey Centralized Logging Documentation

## Overview

PhoneKey implements a production-grade centralized logging architecture that ingests structured logs from all services, enforces consistent schema and severity levels, and provides flexible log routing and storage.

## Architecture

The logging system consists of the following components:

- **logging_schema.py**: Defines structured log format and severity levels
- **logging_config.py**: Centralized logging configuration with environment-specific settings
- **logging_formatter.py**: Formatters for JSON, text, and colored console output
- **logging_handlers.py**: Custom handlers (rotating file, async, memory, context-enriching)
- **logging_context.py**: Thread-local and async context management
- **logging_utils.py**: Utilities for exception logging, timing, PII redaction, and sampling
- **log_ingestion.py**: Central log collector for multi-service architectures

## Quick Start

### Basic Usage

```python
from logging_config import get_logger

logger = get_logger("my_module")
logger.info("Application started")
logger.error("Something went wrong", exc_info=True)
```

### With Context

```python
from logging_context import log_context

with log_context(device_id="123", user_id="456"):
    logger.info("Processing request")
    # Logs will include device_id and user_id
```

### Timing Operations

```python
from logging_utils import timed_log

@timed_log(logger, "Processing request")
def process_request():
    # Do work
    pass
```

## Log Schema

### Required Fields

Every log entry includes these fields:

| Field | Type | Description |
|-------|------|-------------|
| `timestamp` | string | ISO 8601 UTC timestamp |
| `level` | string | Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL) |
| `level_value` | integer | Numeric level value |
| `service` | string | Service name |
| `module` | string | Module name |
| `message` | string | Human-readable message |
| `trace_id` | string | Correlation ID for distributed tracing |
| `span_id` | string | Span ID for request tracing |

### Optional Fields

| Field | Type | Description |
|-------|------|-------------|
| `user_id` | string | User identifier |
| `device_id` | string | Device identifier |
| `session_id` | string | Session identifier |
| `error_type` | string | Exception class name |
| `stack_trace` | string | Exception traceback |
| `duration_ms` | float | Operation duration in milliseconds |
| `metadata` | object | Additional structured data |
| `file` | string | Source file path |
| `line` | integer | Source line number |
| `function` | string | Function name |

### Example Log Entry

```json
{
  "timestamp": "2026-04-25T04:23:29.999Z",
  "level": "INFO",
  "level_value": 20,
  "service": "phonekey",
  "module": "server",
  "message": "Phone connecting from 192.168.1.100:54321",
  "trace_id": "550e8400-e29b-41d4-a716-446655440000",
  "span_id": "550e8400-e29b-41d4-a716-446655440001",
  "device_id": "device-123",
  "user_id": "user-456",
  "metadata": {
    "ip": "192.168.1.100",
    "port": 54321
  }
}
```

## Severity Levels

| Level | Value | Use Case |
|-------|-------|----------|
| `DEBUG` | 10 | Detailed debugging information |
| `INFO` | 20 | Normal operational messages |
| `WARNING` | 30 | Potential issues, recoverable |
| `ERROR` | 40 | Errors affecting functionality |
| `CRITICAL` | 50 | Critical failures, service down |

## Environment Configurations

### Development

- **Console output** with colors
- **DEBUG level** for all modules
- No log rotation
- Local file output optional

```python
setup_logging(environment='development')
```

### Production

- **JSON format** to stdout/stderr
- **INFO level** default, WARNING for noisy modules
- **Rotating file handler** (100MB, 10 files)
- **Network syslog forwarding**
- Structured error tracking

```python
setup_logging(environment='production')
```

### Testing

- **Captured to memory buffer**
- **WARNING level** and above
- Deterministic output for assertions

```python
setup_logging(environment='testing')
```

## API Reference

### Logging Configuration

#### `setup_logging(environment='development', log_dir=None, config_file=None)`

Setup centralized logging.

**Parameters:**
- `environment` (str): Environment name (development/production/testing)
- `log_dir` (Path): Directory for log files
- `config_file` (Path): Optional JSON configuration file

**Returns:** `LoggingConfig` instance

#### `get_logger(name)`

Get a logger instance.

**Parameters:**
- `name` (str): Logger name

**Returns:** `logging.Logger`

### Log Context

#### `log_context(**kwargs)`

Context manager for temporarily setting log context.

```python
with log_context(device_id='123', user_id='456'):
    logger.info("This log has device_id and user_id set")
```

#### `trace_span(name, **kwargs)`

Context manager for creating a trace span.

```python
with trace_span("process_request", device_id="123"):
    logger.info("Processing request")
```

#### `LogContext`

Manages logging context.

- `LogContext.set_trace_id(trace_id)` - Set trace ID
- `LogContext.set_device_id(device_id)` - Set device ID
- `LogContext.set_user_id(user_id)` - Set user ID
- `LogContext.get_context()` - Get current context
- `LogContext.clear_context()` - Clear all context

### Structured Logging

#### `StructuredLogger`

Convenience wrapper for structured logging.

```python
slogger = StructuredLogger(logger, 'my_service')
slogger.info('message', module='my_module', device_id='123')
```

#### `TimedLog(logger, message, level=LogLevel.INFO, **context)`

Context manager for timing operations.

```python
with TimedLog(logger, 'Processing request'):
    # Do work
    pass
```

#### `timed_log(logger, message, level=LogLevel.INFO, **context)`

Decorator for timing function execution.

```python
@timed_log(logger, 'Processing request')
def process():
    pass
```

#### `ExceptionLogger`

Utility for structured exception logging.

```python
try:
    raise ValueError("Error")
except ValueError as exc:
    ExceptionLogger.log_exception(logger, 'Failed', exc)
```

### Log Sampling

#### `LogSampler(rate_limit=None, sample_rate=1.0)`

Samples high-frequency logs.

```python
sampler = LogSampler(rate_limit=10, sample_rate=0.5)
if sampler.should_log():
    logger.debug('High frequency log')
```

### PII Redaction

#### `PiiRedactionFilter`

Filter that redacts PII from log messages.

Automatically redacts:
- Social Security Numbers
- Credit card numbers
- Email addresses
- IP addresses
- Passwords and tokens
- Authorization headers

## Integration Examples

### Server Integration

```python
from logging_config import setup_logging, get_logger
from logging_context import log_context

# Setup logging
setup_logging(environment='production')

# Get logger
logger = get_logger('phonekey')

# Use with context
async def handle_connection(client_addr, device_id):
    with log_context(device_id=device_id, client_addr=client_addr):
        logger.info('Client connected')
        try:
            # Process request
            pass
        except Exception as exc:
            logger.error('Connection failed', exc_info=True)
```

### Tunnel Manager Integration

```python
from logging_config import get_logger

logger = get_logger('phonekey.tunnel')

class TunnelManager:
    def start(self):
        logger.info('Starting tunnel')
        try:
            # Start tunnel
            pass
        except Exception as exc:
            logger.error('Tunnel failed', exc_info=True)
```

## Log Rotation

Logs are rotated based on size:

- **Max file size**: 100 MB
- **Backup count**: 10 files
- **Compression**: Enabled in production

Rotated files are named:
- `phonekey_development.log` (current)
- `phonekey_development.log.1.gz` (most recent backup)
- `phonekey_development.log.2.gz` (older backup)

## Network Logging

Logs can be forwarded to a remote syslog server:

```python
# In logging_config.py
'network': {
    'formatter': 'structured_json',
    'level': 'WARNING',
    'host': 'localhost',
    'port': 514,
    'protocol': 'udp',
}
```

## Testing

Run logging tests:

```bash
python -m pytest test_logging.py -v
```

## Best Practices

1. **Always use structured logging**: Use `StructuredLogger` or create `LogRecord` instances
2. **Include context**: Use `log_context` to add request-scoped metadata
3. **Use appropriate levels**: DEBUG for development, INFO for operations, WARNING for issues, ERROR for failures
4. **Log exceptions properly**: Use `exc_info=True` or `ExceptionLogger`
5. **Avoid PII**: Use `PiiRedactionFilter` or manually redact sensitive data
6. **Time operations**: Use `@timed_log` decorator for performance monitoring
7. **Use trace IDs**: Correlate logs across services with trace IDs

## Troubleshooting

### Logs not appearing

- Check environment: `print(os.environ.get('PHONEKEY_ENV'))`
- Verify logger name: `print(logger.name)`
- Check log level: `print(logger.level)`

### Context not propagating

- Ensure `log_context` is used as context manager
- Check for async boundaries (use ContextVar for async)
- Verify filters are configured

### Performance issues

- Use `AsyncHandler` for non-blocking I/O
- Enable log sampling for high-frequency logs
- Reduce log level in production

## See Also

- [logging_design.md](logging_design.md) - Architecture design document
- [logging_schema.py](logging_schema.py) - Schema implementation
- [logging_config.py](logging_config.py) - Configuration module
- [logging_context.py](logging_context.py) - Context management
- [logging_utils.py](logging_utils.py) - Utilities module
