# Centralized Logging Architecture for PhoneKey

## Overview
Production-grade centralized logging architecture that ingests structured logs from all services, enforces consistent schema and severity levels, and provides flexible log routing and storage.

## Architecture Components

### 1. Log Schema (`logging_schema.py`)
- Defines structured log format (JSON-based)
- Standardized severity levels (DEBUG, INFO, WARNING, ERROR, CRITICAL)
- Required fields: timestamp, level, service, message, trace_id, span_id, context
- Optional fields: user_id, device_id, session_id, error_type, stack_trace, metadata

### 2. Log Configuration (`logging_config.py`)
- Centralized logging configuration
- Environment-specific settings (development, production, testing)
- Handler configuration (console, file, rotating file, network/syslog)
- Log level policies per service/module
- Output format selection (JSON, structured text)

### 3. Log Formatter (`logging_formatter.py`)
- JSON formatter for structured logging
- Consistent field ordering and naming
- ISO 8601 timestamps
- Automatic context enrichment

### 4. Log Handlers (`logging_handlers.py`)
- ConsoleHandler: Colored output for development
- RotatingFileHandler: Size-based log rotation with compression
- NetworkHandler: Syslog/remote log aggregation (UDP/TCP)
- AsyncLogHandler: Non-blocking log delivery

### 5. Log Ingestion Service (`log_ingestion.py`)
- Central log collector for multi-service architectures
- Accepts logs via HTTP, TCP, or Unix socket
- Buffering and batching for performance
- Dead letter queue for failed deliveries

### 6. Context Utilities (`logging_context.py`)
- Thread-local context storage
- Request-scoped metadata (trace_id, user_id, device_id)
- Automatic correlation ID generation
- Context propagation across async boundaries

### 7. Logging Utilities (`logging_utils.py`)
- Structured exception logging
- Performance timing decorators
- Log sanitization (PII redaction)
- Log sampling for high-volume events

## Log Schema Specification

### Required Fields
```json
{
  "timestamp": "2026-04-25T04:23:29.999Z",
  "level": "INFO",
  "service": "phonekey",
  "module": "server",
  "message": "Phone connecting from 192.168.1.100:54321",
  "trace_id": "550e8400-e29b-41d4-a716-446655440000",
  "span_id": "550e8400-e29b-41d4-a716-446655440001"
}
```

### Optional Fields
- `user_id`: User identifier
- `device_id`: Device identifier
- `session_id`: Session identifier
- `error_type`: Exception class name
- `stack_trace`: Exception traceback
- `duration_ms`: Operation duration
- `metadata`: Additional structured data

## Severity Levels

| Level | Value | Use Case |
|-------|-------|----------|
| DEBUG | 10 | Detailed debugging information |
| INFO | 20 | Normal operational messages |
| WARNING | 30 | Potential issues, recoverable |
| ERROR | 40 | Errors affecting functionality |
| CRITICAL | 50 | Critical failures, service down |

## Environment Configurations

### Development
- Console output with colors
- DEBUG level for all modules
- No log rotation
- Local file output optional

### Production
- JSON format to stdout/stderr
- INFO level default, WARNING for noisy modules
- Rotating file handler (100MB, 10 files)
- Network syslog forwarding
- Structured error tracking

### Testing
- Captured to memory buffer
- WARNING level and above
- Deterministic output for assertions

## Integration Points

### server.py
- Replace `logging.basicConfig` with centralized config
- Add context enrichment for device_id, tab_id, client_addr
- Structured logging for all WebSocket events
- Error logging with full context

### tunnel_manager.py
- Use centralized logger with "phonekey.tunnel" namespace
- Structured logging for tunnel lifecycle events
- Error logging with binary download failures

## Performance Considerations

- Async log handlers to avoid blocking I/O
- Batching for network log delivery
- Size-based rotation to prevent disk exhaustion
- Sampling for high-frequency debug logs
- Thread-local context to avoid contention

## Security Considerations

- PII redaction in logs (passwords, tokens, personal data)
- Log file permissions (0600 for sensitive data)
- Encrypted log transmission over network
- Rate limiting to prevent log flooding attacks
- Audit trail for log access

## Monitoring & Alerting

- Log volume metrics
- Error rate alerts
- Latency percentiles
- Dead letter queue monitoring
- Disk space alerts for log storage
