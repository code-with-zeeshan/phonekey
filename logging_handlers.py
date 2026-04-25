"""
PhoneKey Custom Logging Handlers

Provides specialized handlers for log delivery:
- RotatingFileHandler with compression
- AsyncHandler for non-blocking I/O
- MemoryHandler for testing
- ContextEnrichingHandler for automatic metadata injection
"""

import gzip
import json
import logging
import logging.handlers
import os
import shutil
import threading
import time
from pathlib import Path
from queue import Queue, Empty
from typing import Any, Optional

from logging_schema import LogRecord


class RotatingFileHandler(logging.handlers.RotatingFileHandler):
    """
    Enhanced RotatingFileHandler with optional compression.
    
    Automatically compresses rotated log files to save disk space.
    """

    def __init__(
        self,
        filename: str,
        mode: str = 'a',
        maxBytes: int = 0,
        backupCount: int = 0,
        encoding: Optional[str] = None,
        delay: bool = False,
        compress: bool = False,
    ):
        """
        Initialize the rotating file handler.
        
        Args:
            filename: Path to the log file
            mode: File open mode
            maxBytes: Maximum file size before rotation
            backupCount: Number of backup files to keep
            encoding: File encoding
            delay: Delay file opening
            compress: Whether to compress rotated files
        """
        super().__init__(filename, mode, maxBytes, backupCount, encoding, delay)
        self.compress = compress
        self._lock = threading.Lock()

    def doRollover(self) -> None:
        """
        Perform rollover, optionally compressing the old log file.
        """
        super().doRollover()

        if self.compress and self.backupCount > 0:
            # Compress the previous backup file
            for i in range(self.backupCount - 1, 0, -1):
                sfn = self.rotation_filename(f"{self.baseFilename}.{i}")
                dfn = self.rotation_filename(f"{self.baseFilename}.{i + 1}.gz")
                if os.path.exists(sfn):
                    if os.path.exists(dfn):
                        os.remove(dfn)
                    self._compress_file(sfn, dfn)
                    os.remove(sfn)

            # Compress the first backup
            sfn = self.rotation_filename(f"{self.baseFilename}.1")
            if os.path.exists(sfn):
                dfn = self.rotation_filename(f"{self.baseFilename}.1.gz")
                if os.path.exists(dfn):
                    os.remove(dfn)
                self._compress_file(sfn, dfn)
                os.remove(sfn)

    def _compress_file(self, source: str, dest: str) -> None:
        """
        Compress a file using gzip.
        
        Args:
            source: Source file path
            dest: Destination file path
        """
        try:
            with open(source, 'rb') as f_in:
                with gzip.open(dest, 'wb') as f_out:
                    shutil.copyfileobj(f_in, f_out)
        except Exception as e:
            # Log error but don't crash
            import sys
            print(f"Failed to compress log file {source}: {e}", file=sys.stderr)


class AsyncHandler(logging.Handler):
    """
    Asynchronous log handler that processes logs in a background thread.
    
    Prevents blocking I/O operations from slowing down the application.
    """

    def __init__(self, target_handler: logging.Handler, queue_size: int = 10000):
        """
        Initialize the async handler.
        
        Args:
            target_handler: The actual handler to process logs
            queue_size: Maximum queue size before dropping logs
        """
        super().__init__()
        self.target_handler = target_handler
        self.queue: Queue = Queue(maxsize=queue_size)
        self._thread = threading.Thread(target=self._process_queue, daemon=True)
        self._running = True
        self._thread.start()
        self._dropped_count = 0
        self._lock = threading.Lock()

    def emit(self, record: logging.LogRecord) -> None:
        """
        Queue the log record for async processing.
        """
        try:
            self.queue.put_nowait(record)
        except:
            with self._lock:
                self._dropped_count += 1

    def _process_queue(self) -> None:
        """
        Process log records from the queue.
        """
        while self._running:
            try:
                record = self.queue.get(timeout=1)
                try:
                    self.target_handler.emit(record)
                except Exception:
                    self.handleError(record)
                finally:
                    self.queue.task_done()
            except Empty:
                continue

    def flush(self) -> None:
        """Wait for all queued logs to be processed."""
        self.queue.join()
        if self.target_handler:
            self.target_handler.flush()

    def close(self) -> None:
        """Stop the async handler and clean up."""
        self._running = False
        self.flush()
        if self.target_handler:
            self.target_handler.close()
        super().close()

    @property
    def dropped_count(self) -> int:
        """Get the number of dropped log records."""
        with self._lock:
            return self._dropped_count


class MemoryHandler(logging.Handler):
    """
    In-memory log handler for testing.
    
    Stores log records in a list for later inspection.
    """

    def __init__(self, capacity: int = 1000):
        """
        Initialize the memory handler.
        
        Args:
            capacity: Maximum number of records to store
        """
        super().__init__()
        self.records: list[logging.LogRecord] = []
        self.capacity = capacity
        self._lock = threading.Lock()

    def emit(self, record: logging.LogRecord) -> None:
        """
        Store the log record in memory.
        """
        with self._lock:
            self.records.append(record)
            if len(self.records) > self.capacity:
                self.records.pop(0)

    def get_records(self) -> list[logging.LogRecord]:
        """Get all stored log records."""
        with self._lock:
            return list(self.records)

    def get_records_by_level(self, level: int) -> list[logging.LogRecord]:
        """Get log records filtered by level."""
        with self._lock:
            return [r for r in self.records if r.levelno == level]

    def get_records_by_name(self, name: str) -> list[logging.LogRecord]:
        """Get log records filtered by logger name."""
        with self._lock:
            return [r for r in self.records if r.name == name]

    def clear(self) -> None:
        """Clear all stored records."""
        with self._lock:
            self.records.clear()

    def __len__(self) -> int:
        """Get the number of stored records."""
        with self._lock:
            return len(self.records)


class ContextEnrichingHandler(logging.Handler):
    """
    Handler that enriches log records with contextual metadata.
    
    Automatically adds device_id, user_id, session_id, and trace_id
    from thread-local context storage.
    """

    def __init__(self, target_handler: logging.Handler):
        """
        Initialize the context enriching handler.
        
        Args:
            target_handler: The handler to enrich and forward to
        """
        super().__init__()
        self.target_handler = target_handler

    def emit(self, record: logging.LogRecord) -> None:
        """
        Enrich the record with context and forward it.
        """
        from logging_context import get_context

        context = get_context()

        # Add context to record
        if not hasattr(record, 'device_id'):
            record.device_id = context.get('device_id')
        if not hasattr(record, 'user_id'):
            record.user_id = context.get('user_id')
        if not hasattr(record, 'session_id'):
            record.session_id = context.get('session_id')
        if not hasattr(record, 'trace_id'):
            record.trace_id = context.get('trace_id')
        if not hasattr(record, 'span_id'):
            record.span_id = context.get('span_id')

        # Add any additional context as metadata
        extra_context = {
            k: v for k, v in context.items()
            if k not in ('device_id', 'user_id', 'session_id', 'trace_id', 'span_id')
        }
        if extra_context and not hasattr(record, 'metadata'):
            record.metadata = extra_context

        self.target_handler.emit(record)

    def flush(self) -> None:
        """Flush the target handler."""
        if self.target_handler:
            self.target_handler.flush()

    def close(self) -> None:
        """Close the target handler."""
        if self.target_handler:
            self.target_handler.close()
        super().close()


class SysLogHandler(logging.Handler):
    """
    Syslog handler for sending logs to a remote syslog server.
    
    Supports both UDP and TCP protocols.
    """

    # Syslog severity levels
    SYSLOG_LEVELS = {
        logging.DEBUG: 7,    # LOG_DEBUG
        logging.INFO: 6,     # LOG_INFO
        logging.WARNING: 4,  # LOG_WARNING
        logging.ERROR: 3,    # LOG_ERR
        logging.CRITICAL: 2, # LOG_CRIT
    }

    def __init__(
        self,
        host: str = 'localhost',
        port: int = 514,
        protocol: str = 'udp',
        facility: int = 1,  # LOG_USER
        app_name: str = 'phonekey',
    ):
        """
        Initialize the syslog handler.
        
        Args:
            host: Syslog server hostname
            port: Syslog server port
            protocol: 'udp' or 'tcp'
            facility: Syslog facility
            app_name: Application name for syslog tag
        """
        super().__init__()
        self.host = host
        self.port = port
        self.protocol = protocol
        self.facility = facility
        self.app_name = app_name
        self._socket: Optional[Any] = None
        self._lock = threading.Lock()
        self._connect()

    def _connect(self) -> None:
        """Establish connection to syslog server."""
        import socket

        with self._lock:
            if self._socket:
                try:
                    self._socket.close()
                except:
                    pass

            try:
                if self.protocol == 'udp':
                    self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                else:
                    self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    self._socket.connect((self.host, self.port))
            except Exception as e:
                import sys
                print(f"Failed to connect to syslog {self.host}:{self.port}: {e}", file=sys.stderr)
                self._socket = None

    def emit(self, record: logging.LogRecord) -> None:
        """
        Send log record to syslog server.
        """
        try:
            message = self.format(record)
            self._send(message, record.levelno)
        except Exception:
            self.handleError(record)

    def _send(self, message: str, level: int) -> None:
        """
        Send message to syslog.
        
        Args:
            message: Formatted log message
            level: Log level
        """
        import socket

        with self._lock:
            if not self._socket:
                self._connect()

            if not self._socket:
                return

            try:
                # Build syslog message
                severity = self.SYSLOG_LEVELS.get(level, 7)
                priority = (self.facility * 8) + severity
                timestamp = time.strftime('%b %d %H:%M:%S')
                hostname = socket.gethostname().split('.')[0]
                
                syslog_msg = f"<{priority}>{timestamp} {hostname} {self.app_name}: {message}"

                if self.protocol == 'udp':
                    self._socket.sendto(syslog_msg.encode('utf-8'), (self.host, self.port))
                else:
                    self._socket.sendall((syslog_msg + '\n').encode('utf-8'))
            except Exception:
                # Try to reconnect on next emit
                try:
                    self._socket.close()
                except:
                    pass
                self._socket = None

    def close(self) -> None:
        """Close the syslog connection."""
        with self._lock:
            if self._socket:
                try:
                    self._socket.close()
                except:
                    pass
                self._socket = None
        super().close()
