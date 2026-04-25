"""
PhoneKey Log Ingestion Service

Centralized log collector for aggregating logs from multiple services.
Supports HTTP, TCP, and Unix socket ingestion endpoints.
"""

import asyncio
import json
import logging
import socket
import threading
import time
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from queue import Queue
from typing import Any, Dict, Optional
from urllib.parse import urlparse

from logging_schema import LogRecord, LogLevel


class LogIngestionHandler(BaseHTTPRequestHandler):
    """HTTP handler for receiving log entries."""

    server_instance: Optional["LogIngestionServer"] = None

    def do_POST(self) -> None:
        """Handle POST requests with log entries."""
        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length)

        try:
            data = json.loads(body.decode('utf-8'))
            self.server_instance.ingest_log(data)  # type: ignore

            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'status': 'ok'}).encode())
        except json.JSONDecodeError:
            self.send_response(400)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'error': 'invalid json'}).encode())
        except Exception as e:
            self.send_response(500)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'error': str(e)}).encode())

    def do_GET(self) -> None:
        """Handle GET requests for health checks."""
        if self.path == '/health':
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            stats = self.server_instance.get_stats()  # type: ignore
            self.wfile.write(json.dumps(stats).encode())
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format: str, *args: Any) -> None:
        """Suppress default HTTP server logging."""
        pass


class LogIngestionServer:
    """
    Centralized log ingestion server.
    
    Collects logs from multiple services via HTTP, TCP, or Unix sockets.
    Validates, enriches, and forwards logs to configured handlers.
    """

    def __init__(
        self,
        host: str = '0.0.0.0',
        port: int = 9999,
        protocol: str = 'http',
        log_dir: Optional[Path] = None,
        max_queue_size: int = 10000,
    ):
        """
        Initialize the log ingestion server.
        
        Args:
            host: Host to bind to
            port: Port to listen on
            protocol: 'http', 'tcp', or 'unix'
            log_dir: Directory for storing logs
            max_queue_size: Maximum queue size for buffering
        """
        self.host = host
        self.port = port
        self.protocol = protocol
        self.log_dir = log_dir or Path.cwd() / 'logs'
        self.log_dir.mkdir(parents=True, exist_ok=True)

        self.queue: Queue = Queue(maxsize=max_queue_size)
        self.running = False
        self.threads: list[threading.Thread] = []

        # Statistics
        self.stats = {
            'received': 0,
            'processed': 0,
            'dropped': 0,
            'errors': 0,
            'start_time': None,
        }
        self._stats_lock = threading.Lock()

        # Configure local logger
        self.logger = logging.getLogger('phonekey.ingestion')

        # Handlers for processed logs
        self.handlers: list[logging.Handler] = []

    def add_handler(self, handler: logging.Handler) -> None:
        """Add a handler for processed logs."""
        self.handlers.append(handler)

    def start(self) -> None:
        """Start the ingestion server."""
        self.running = True
        self.stats['start_time'] = datetime.now(timezone.utc).isoformat()

        # Start queue processor
        processor_thread = threading.Thread(target=self._process_queue, daemon=True)
        processor_thread.start()
        self.threads.append(processor_thread)

        # Start ingestion endpoint
        if self.protocol == 'http':
            self._start_http_server()
        elif self.protocol == 'tcp':
            self._start_tcp_server()
        elif self.protocol == 'unix':
            self._start_unix_server()
        else:
            raise ValueError(f"Unsupported protocol: {self.protocol}")

        self.logger.info(
            "Log ingestion server started on %s://%s:%s",
            self.protocol, self.host, self.port,
        )

    def _start_http_server(self) -> None:
        """Start HTTP ingestion server."""
        LogIngestionHandler.server_instance = self
        self.http_server = HTTPServer((self.host, self.port), LogIngestionHandler)

        server_thread = threading.Thread(target=self.http_server.serve_forever, daemon=True)
        server_thread.start()
        self.threads.append(server_thread)

    def _start_tcp_server(self) -> None:
        """Start TCP ingestion server."""
        server_thread = threading.Thread(target=self._tcp_server_loop, daemon=True)
        server_thread.start()
        self.threads.append(server_thread)

    def _tcp_server_loop(self) -> None:
        """TCP server main loop."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((self.host, self.port))
        sock.listen(5)
        sock.settimeout(1.0)

        while self.running:
            try:
                conn, addr = sock.accept()
                thread = threading.Thread(
                    target=self._handle_tcp_connection,
                    args=(conn, addr),
                    daemon=True,
                )
                thread.start()
            except socket.timeout:
                continue
            except Exception as e:
                if self.running:
                    self.logger.error("TCP server error: %s", e)

        sock.close()

    def _handle_tcp_connection(self, conn: socket.socket, addr: tuple) -> None:
        """Handle a TCP connection."""
        buffer = ""
        try:
            while self.running:
                data = conn.recv(4096).decode('utf-8')
                if not data:
                    break
                buffer += data

                # Process complete JSON lines
                while '\n' in buffer:
                    line, buffer = buffer.split('\n', 1)
                    line = line.strip()
                    if line:
                        try:
                            log_data = json.loads(line)
                            self.ingest_log(log_data)
                        except json.JSONDecodeError:
                            with self._stats_lock:
                                self.stats['errors'] += 1
        except Exception as e:
            self.logger.debug("TCP connection error from %s: %s", addr, e)
        finally:
            conn.close()

    def _start_unix_server(self) -> None:
        """Start Unix socket ingestion server."""
        socket_path = self.log_dir / 'phonekey.sock'
        if socket_path.exists():
            socket_path.unlink()

        server_thread = threading.Thread(
            target=self._unix_server_loop,
            args=(str(socket_path),),
            daemon=True,
        )
        server_thread.start()
        self.threads.append(server_thread)

    def _unix_server_loop(self, socket_path: str) -> None:
        """Unix socket server main loop."""
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.bind(socket_path)
        sock.listen(5)
        sock.settimeout(1.0)

        while self.running:
            try:
                conn, _ = sock.accept()
                thread = threading.Thread(
                    target=self._handle_unix_connection,
                    args=(conn,),
                    daemon=True,
                )
                thread.start()
            except socket.timeout:
                continue
            except Exception as e:
                if self.running:
                    self.logger.error("Unix socket error: %s", e)

        sock.close()
        Path(socket_path).unlink(missing_ok=True)

    def _handle_unix_connection(self, conn: socket.socket) -> None:
        """Handle a Unix socket connection."""
        buffer = ""
        try:
            while self.running:
                data = conn.recv(4096).decode('utf-8')
                if not data:
                    break
                buffer += data

                while '\n' in buffer:
                    line, buffer = buffer.split('\n', 1)
                    line = line.strip()
                    if line:
                        try:
                            log_data = json.loads(line)
                            self.ingest_log(log_data)
                        except json.JSONDecodeError:
                            with self._stats_lock:
                                self.stats['errors'] += 1
        except Exception:
            pass
        finally:
            conn.close()

    def ingest_log(self, log_data: Dict[str, Any]) -> None:
        """
        Ingest a log entry.
        
        Args:
            log_data: Log data as dictionary
        """
        with self._stats_lock:
            self.stats['received'] += 1

        try:
            # Convert to LogRecord if needed
            if isinstance(log_data, dict) and 'timestamp' in log_data:
                # Already structured
                record = self._dict_to_logrecord(log_data)
            else:
                # Create from raw data
                record = self._create_logrecord_from_raw(log_data)

            # Validate
            is_valid, errors = record.__class__.validate(record)
            if not is_valid:
                self.logger.warning("Invalid log record: %s", errors)
                with self._stats_lock:
                    self.stats['errors'] += 1
                return

            # Queue for processing
            try:
                self.queue.put_nowait(record)
            except:
                with self._stats_lock:
                    self.stats['dropped'] += 1

        except Exception as e:
            self.logger.error("Failed to ingest log: %s", e)
            with self._stats_lock:
                self.stats['errors'] += 1

    def _dict_to_logrecord(self, data: Dict[str, Any]) -> LogRecord:
        """Convert dictionary to LogRecord."""
        # Extract required fields
        return LogRecord(
            timestamp=data.get('timestamp', datetime.now(timezone.utc).isoformat()),
            level=data.get('level', 'INFO'),
            level_value=data.get('level_value', 20),
            service=data.get('service', 'unknown'),
            module=data.get('module', 'unknown'),
            message=data.get('message', ''),
            trace_id=data.get('trace_id', ''),
            span_id=data.get('span_id', ''),
            user_id=data.get('user_id'),
            device_id=data.get('device_id'),
            session_id=data.get('session_id'),
            error_type=data.get('error_type'),
            stack_trace=data.get('stack_trace'),
            duration_ms=data.get('duration_ms'),
            metadata=data.get('metadata', {}),
            file=data.get('file'),
            line=data.get('line'),
            function=data.get('function'),
        )

    def _create_logrecord_from_raw(self, raw_data: Any) -> LogRecord:
        """Create LogRecord from raw unstructured data."""
        message = str(raw_data)
        if isinstance(raw_data, dict):
            message = raw_data.get('message', str(raw_data))

        return LogRecord.create(
            level=LogLevel.INFO,
            service='ingestion',
            module='ingestor',
            message=message,
            metadata={'raw': raw_data} if not isinstance(raw_data, dict) else {},
        )

    def _process_queue(self) -> None:
        """Process queued log records."""
        while self.running:
            try:
                record = self.queue.get(timeout=1)
                self._process_record(record)
                with self._stats_lock:
                    self.stats['processed'] += 1
                self.queue.task_done()
            except:
                continue

    def _process_record(self, record: LogRecord) -> None:
        """Process a single log record."""
        # Write to file
        self._write_to_file(record)

        # Forward to handlers
        for handler in self.handlers:
            try:
                # Convert to logging.LogRecord
                log_record = self._to_logging_record(record)
                handler.emit(log_record)
            except Exception as e:
                self.logger.error("Handler error: %s", e)

    def _write_to_file(self, record: LogRecord) -> None:
        """Write log record to file."""
        try:
            log_file = self.log_dir / f"ingested_{datetime.now(timezone.utc).strftime('%Y%m%d')}.log"
            with open(log_file, 'a') as f:
                f.write(record.to_json() + '\n')
        except Exception as e:
            self.logger.error("Failed to write log to file: %s", e)

    def _to_logging_record(self, record: LogRecord) -> logging.LogRecord:
        """Convert LogRecord to logging.LogRecord."""
        level_map = {
            'DEBUG': logging.DEBUG,
            'INFO': logging.INFO,
            'WARNING': logging.WARNING,
            'ERROR': logging.ERROR,
            'CRITICAL': logging.CRITICAL,
        }

        lr = logging.LogRecord(
            name=record.service,
            level=level_map.get(record.level, logging.INFO),
            pathname=record.file or '',
            lineno=record.line or 0,
            msg=record.message,
            args=(),
            func=record.function or '',
        )
        lr.structured_record = record
        return lr

    def get_stats(self) -> Dict[str, Any]:
        """Get server statistics."""
        with self._stats_lock:
            return dict(self.stats)

    def stop(self) -> None:
        """Stop the ingestion server."""
        self.running = False

        # Wait for queue to drain
        self.queue.join()

        # Close handlers
        for handler in self.handlers:
            try:
                handler.close()
            except:
                pass

        # Stop HTTP server
        if hasattr(self, 'http_server'):
            self.http_server.shutdown()

        self.logger.info("Log ingestion server stopped")


# Global ingestion server instance
_ingestion_server: Optional[LogIngestionServer] = None


def get_ingestion_server() -> LogIngestionServer:
    """Get or create the global ingestion server."""
    global _ingestion_server
    if _ingestion_server is None:
        _ingestion_server = LogIngestionServer()
    return _ingestion_server


def start_ingestion_server(
    host: str = '0.0.0.0',
    port: int = 9999,
    protocol: str = 'http',
) -> LogIngestionServer:
    """Start the global ingestion server."""
    server = get_ingestion_server()
    if not server.running:
        server.host = host
        server.port = port
        server.protocol = protocol
        server.start()
    return server
