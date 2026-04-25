"""
PhoneKey Logging Configuration

Centralized logging configuration with environment-specific settings.
Provides flexible handler configuration and log level policies.
"""

import json
import logging
import logging.config
import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional

from logging_schema import LogLevel


class LoggingConfig:
    """Centralized logging configuration manager."""

    # Default configuration
    DEFAULT_CONFIG: Dict[str, Any] = {
        'version': 1,
        'disable_existing_loggers': False,
        'formatters': {
            'structured_json': {
                '()': 'logging_formatter.StructuredJSONFormatter',
            },
            'structured_text': {
                '()': 'logging_formatter.StructuredTextFormatter',
            },
            'colored': {
                '()': 'logging_formatter.ColoredFormatter',
            },
        },
        'filters': {
            'context_filter': {
                '()': 'logging_context.ContextFilter',
            },
            'pii_filter': {
                '()': 'logging_utils.PiiRedactionFilter',
            },
        },
        'handlers': {},
        'loggers': {},
        'root': {
            'level': 'INFO',
            'handlers': [],
        },
    }

    # Environment-specific configurations
    ENV_CONFIGS = {
        'development': {
            'root_level': 'DEBUG',
            'handlers': ['console'],
            'console': {
                'formatter': 'colored',
                'level': 'DEBUG',
            },
            'loggers': {
                'phonekey': {'level': 'DEBUG'},
                'phonekey.tunnel': {'level': 'DEBUG'},
            },
        },
        'production': {
            'root_level': 'INFO',
            'handlers': ['console', 'rotating_file', 'network'],
            'console': {
                'formatter': 'structured_json',
                'level': 'INFO',
            },
            'rotating_file': {
                'formatter': 'structured_json',
                'level': 'INFO',
                'max_bytes': 100 * 1024 * 1024,  # 100 MB
                'backup_count': 10,
                'compress': True,
            },
            'network': {
                'formatter': 'structured_json',
                'level': 'WARNING',
                'host': 'localhost',
                'port': 514,
                'protocol': 'udp',
            },
            'loggers': {
                'phonekey': {'level': 'INFO'},
                'phonekey.tunnel': {'level': 'WARNING'},
            },
        },
        'testing': {
            'root_level': 'WARNING',
            'handlers': ['console', 'memory'],
            'console': {
                'formatter': 'structured_text',
                'level': 'WARNING',
            },
            'memory': {
                'formatter': 'structured_json',
                'level': 'DEBUG',
            },
            'loggers': {
                'phonekey': {'level': 'WARNING'},
                'phonekey.tunnel': {'level': 'WARNING'},
            },
        },
    }

    def __init__(self, environment: str = 'development', log_dir: Optional[Path] = None):
        """
        Initialize logging configuration.
        
        Args:
            environment: Environment name (development/production/testing)
            log_dir: Directory for log files (defaults to ./logs)
        """
        self.environment = environment
        self.log_dir = log_dir or Path.cwd() / 'logs'
        self.config = self._build_config()

    def _build_config(self) -> Dict[str, Any]:
        """Build complete logging configuration for the environment."""
        config = self._deep_copy(self.DEFAULT_CONFIG)
        env_config = self.ENV_CONFIGS.get(self.environment, self.ENV_CONFIGS['development'])

        # Set root level
        config['root']['level'] = env_config['root_level']

        # Build handlers
        config['handlers'] = self._build_handlers(env_config)
        config['root']['handlers'] = list(config['handlers'].keys())

        # Build loggers
        config['loggers'] = self._build_loggers(env_config)

        return config

    def _build_handlers(self, env_config: Dict[str, Any]) -> Dict[str, Any]:
        """Build handler configurations."""
        handlers = {}

        for handler_name in env_config.get('handlers', []):
            handler_config = env_config.get(handler_name, {})

            if handler_name == 'console':
                handlers['console'] = {
                    'class': 'logging.StreamHandler',
                    'level': handler_config.get('level', 'INFO'),
                    'formatter': handler_config.get('formatter', 'structured_text'),
                    'filters': ['context_filter', 'pii_filter'],
                    'stream': 'ext://sys.stderr',
                }

            elif handler_name == 'rotating_file':
                self.log_dir.mkdir(parents=True, exist_ok=True)
                log_file = self.log_dir / f'phonekey_{self.environment}.log'
                handlers['rotating_file'] = {
                    'class': 'logging_handlers.RotatingFileHandler',
                    'level': handler_config.get('level', 'INFO'),
                    'formatter': handler_config.get('formatter', 'structured_json'),
                    'filters': ['context_filter', 'pii_filter'],
                    'filename': str(log_file),
                    'maxBytes': handler_config.get('max_bytes', 100 * 1024 * 1024),
                    'backupCount': handler_config.get('backup_count', 10),
                    'compress': handler_config.get('compress', False),
                    'encoding': 'utf-8',
                }

            elif handler_name == 'network':
                protocol = handler_config.get('protocol', 'udp')
                if protocol == 'udp':
                    handler_class = 'logging_handlers.DatagramHandler'
                else:
                    handler_class = 'logging_handlers.SocketHandler'
                handlers['network'] = {
                    'class': handler_class,
                    'level': handler_config.get('level', 'WARNING'),
                    'formatter': handler_config.get('formatter', 'structured_json'),
                    'filters': ['context_filter', 'pii_filter'],
                    'host': handler_config.get('host', 'localhost'),
                    'port': handler_config.get('port', 514),
                }

            elif handler_name == 'memory':
                handlers['memory'] = {
                    'class': 'logging_handlers.MemoryHandler',
                    'level': handler_config.get('level', 'DEBUG'),
                    'formatter': handler_config.get('formatter', 'structured_json'),
                    'filters': ['context_filter', 'pii_filter'],
                    'capacity': handler_config.get('capacity', 1000),
                }

        return handlers

    def _build_loggers(self, env_config: Dict[str, Any]) -> Dict[str, Any]:
        """Build logger configurations."""
        loggers = {}
        for logger_name, logger_config in env_config.get('loggers', {}).items():
            loggers[logger_name] = {
                'level': logger_config.get('level', 'INFO'),
                'propagate': False,
                'handlers': env_config.get('handlers', []),
            }
        return loggers

    def apply(self) -> None:
        """Apply the logging configuration."""
        logging.config.dictConfig(self.config)

    def get_logger(self, name: str) -> logging.Logger:
        """Get a configured logger instance."""
        return logging.getLogger(name)

    @staticmethod
    def _deep_copy(data: Dict[str, Any]) -> Dict[str, Any]:
        """Deep copy a dictionary."""
        return json.loads(json.dumps(data))


# Global configuration instance
_config: Optional[LoggingConfig] = None


def setup_logging(
    environment: str = 'development',
    log_dir: Optional[Path] = None,
    config_file: Optional[Path] = None,
) -> LoggingConfig:
    """
    Setup centralized logging.
    
    Args:
        environment: Environment name
        log_dir: Directory for log files
        config_file: Optional JSON configuration file
    
    Returns:
        LoggingConfig instance
    """
    global _config

    if config_file and config_file.exists():
        with open(config_file) as f:
            custom_config = json.load(f)
        logging.config.dictConfig(custom_config)
        _config = LoggingConfig(environment, log_dir)
        _config.config = custom_config
    else:
        _config = LoggingConfig(environment, log_dir)
        _config.apply()

    return _config


def get_logger(name: str) -> logging.Logger:
    """Get a logger instance."""
    if _config is None:
        setup_logging()
    return _config.get_logger(name)


def get_config() -> LoggingConfig:
    """Get the current logging configuration."""
    if _config is None:
        setup_logging()
    return _config
