"""Structured JSON logging configuration using loguru.

This module implements enterprise-grade logging with the following features:
- Structured JSON output for log aggregation systems (ELK, Splunk, etc.)
- Automatic log rotation and retention policies
- Fail-fast validation of log directory writability at startup
- Contextual logging with request/batch correlation IDs

Design Rationale:
    Loguru is preferred over stdlib logging for its zero-config setup,
    structured output capabilities, and superior exception formatting.
    The JSON format enables seamless integration with log aggregation
    infrastructure commonly used in enterprise environments.
"""

import sys
from pathlib import Path
from typing import Any

from loguru import logger

from config.settings import GlobalConfig, get_config
from src.exceptions import LoggingInitializationError


def _json_serializer(record: dict[str, Any]) -> str:
    """Custom JSON serializer for log records.

    Formats log records as single-line JSON for easy parsing by
    log aggregation systems. Includes all contextual metadata.

    Args:
        record: Loguru record dictionary containing log metadata.

    Returns:
        JSON-formatted string representation of the log record.
    """
    import json
    from datetime import datetime, timezone

    subset = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "level": record["level"].name,
        "message": record["message"],
        "module": record["name"],
        "function": record["function"],
        "line": record["line"],
    }

    # Include exception info if present
    if record["exception"] is not None:
        subset["exception"] = {
            "type": record["exception"].type.__name__ if record["exception"].type else None,
            "value": str(record["exception"].value) if record["exception"].value else None,
            "traceback": record["exception"].traceback is not None,
        }

    # Include any extra context bound to the logger
    if record["extra"]:
        subset["context"] = {k: v for k, v in record["extra"].items() if k != "serialized"}

    return json.dumps(subset, default=str) + "\n"


def _validate_log_directory(log_dir: Path) -> None:
    """Validate log directory exists and is writable.

    Implements fail-fast principle - if we cannot write logs,
    the application should not proceed to avoid silent failures.

    Args:
        log_dir: Path to the log directory.

    Raises:
        LoggingInitializationError: If directory creation or write test fails.
    """
    try:
        log_dir.mkdir(parents=True, exist_ok=True)

        # Perform write test with temporary file
        test_file = log_dir / ".write_test"
        test_file.write_text("write_test")
        test_file.unlink()

    except PermissionError as exc:
        raise LoggingInitializationError(
            log_dir=str(log_dir),
            reason=f"Permission denied: {exc}",
        ) from exc
    except OSError as exc:
        raise LoggingInitializationError(
            log_dir=str(log_dir),
            reason=f"OS error during directory validation: {exc}",
        ) from exc


def configure_logging(config: GlobalConfig | None = None) -> None:
    """Initialize the logging infrastructure.

    Configures loguru with:
    - Console output (colorized, human-readable for development)
    - File output (structured JSON for production/aggregation)
    - Automatic rotation and retention policies

    This function should be called once during application bootstrap,
    before any other modules perform logging operations.

    Args:
        config: Optional GlobalConfig instance. If None, uses singleton.

    Raises:
        LoggingInitializationError: If log directory validation fails.
    """
    if config is None:
        config = get_config()

    # Remove default handler to prevent duplicate logs
    logger.remove()

    # Validate log directory (fail-fast)
    _validate_log_directory(config.log_dir)

    # Console handler - human-readable format for development
    console_format = (
        "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
        "<level>{level: <8}</level> | "
        "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
        "<level>{message}</level>"
    )

    logger.add(
        sys.stderr,
        format=console_format,
        level=config.log_level,
        colorize=True,
        backtrace=config.debug,
        diagnose=config.debug,
    )

    # File handler - structured JSON for log aggregation
    log_file_path = config.log_dir / "marketpulse_{time:YYYY-MM-DD}.json"

    logger.add(
        str(log_file_path),
        format="{extra[serialized]}",
        level=config.log_level,
        rotation=config.log_rotation,
        retention=config.log_retention,
        compression="gz",
        serialize=False,  # We use custom serializer
        filter=lambda record: record["extra"].update(serialized=_json_serializer(record)) or True,
    )

    # Log initialization complete
    logger.info(
        "Logging infrastructure initialized",
        app_name=config.app_name,
        environment=config.environment,
        log_level=config.log_level,
        log_dir=str(config.log_dir),
    )


def get_logger(name: str) -> "logger":
    """Get a contextualized logger instance.

    Creates a logger bound with the module name for consistent
    log attribution across the application.

    Args:
        name: Module or component name for log attribution.

    Returns:
        Loguru logger instance bound with the provided name context.

    Example:
        >>> log = get_logger(__name__)
        >>> log.info("Processing started", batch_id="abc123")
    """
    return logger.bind(module=name)
