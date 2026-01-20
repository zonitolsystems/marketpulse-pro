"""Custom exception hierarchy for MarketPulse-Pro.

This module defines domain-specific exceptions that provide semantic clarity
and enable targeted error handling throughout the application. Each exception
includes contextual information to aid debugging and observability.

Design Rationale:
    - Prefer specific exceptions over generic Exception catches
    - Include context (URL, timestamp, selector) in exception messages
    - Support the "fail-fast" philosophy - errors should surface immediately
"""

from datetime import UTC, datetime
from typing import Any


class MarketPulseError(Exception):
    """Base exception for all MarketPulse-Pro errors.

    All custom exceptions inherit from this base, enabling blanket catches
    for application-specific errors while distinguishing from system errors.

    Attributes:
        message: Human-readable error description.
        context: Optional dictionary with additional debugging information.
        timestamp: UTC timestamp when the exception was raised.
    """

    def __init__(self, message: str, context: dict[str, Any] | None = None) -> None:
        self.message = message
        self.context = context or {}
        self.timestamp = datetime.now(UTC)
        super().__init__(self._format_message())

    def _format_message(self) -> str:
        """Format exception message with context for logging."""
        base = f"[{self.timestamp.isoformat()}] {self.message}"
        if self.context:
            context_str = ", ".join(f"{k}={v}" for k, v in self.context.items())
            return f"{base} | Context: {context_str}"
        return base


class ConfigValidationError(MarketPulseError):
    """Raised when configuration validation fails.

    This exception indicates a critical startup failure - the application
    cannot proceed without valid configuration.
    """

    def __init__(self, field: str, value: Any, reason: str) -> None:
        super().__init__(
            message=f"Configuration validation failed for '{field}': {reason}",
            context={"field": field, "value": value, "reason": reason},
        )


class BrowserInitializationError(MarketPulseError):
    """Raised when browser instance fails to initialize.

    Common causes include missing Playwright browsers, resource constraints,
    or conflicting browser processes.
    """

    def __init__(self, reason: str, browser_type: str = "chromium") -> None:
        super().__init__(
            message=f"Failed to initialize {browser_type} browser: {reason}",
            context={"browser_type": browser_type, "reason": reason},
        )


class NavigationError(MarketPulseError):
    """Raised when page navigation fails.

    This may indicate network issues, invalid URLs, or blocked requests.
    Includes the target URL for debugging.
    """

    def __init__(self, url: str, reason: str, status_code: int | None = None) -> None:
        super().__init__(
            message=f"Navigation to '{url}' failed: {reason}",
            context={"url": url, "reason": reason, "status_code": status_code},
        )


class ExtractionError(MarketPulseError):
    """Raised when data extraction from a page element fails.

    This exception captures the problematic selector and page context
    to aid in diagnosing layout changes or selector drift.
    """

    def __init__(self, selector: str, url: str, reason: str) -> None:
        super().__init__(
            message=f"Extraction failed for selector '{selector}': {reason}",
            context={"selector": selector, "url": url, "reason": reason},
        )


class SelectorNotFoundError(ExtractionError):
    """Raised when a CSS/XPath selector matches no elements.

    A specialized ExtractionError indicating the DOM structure
    may have changed or the selector is incorrect.
    """

    def __init__(self, selector: str, url: str) -> None:
        super().__init__(
            selector=selector,
            url=url,
            reason="Selector matched zero elements - possible layout shift",
        )


class LayoutShiftError(MarketPulseError):
    """Raised when the Watchdog detects excessive extraction failures.

    This is a CRITICAL error indicating the target site's structure
    has likely changed. The scraper halts to prevent data pollution.

    Attributes:
        failure_ratio: The observed null/failure ratio that triggered the error.
        threshold: The configured threshold that was exceeded.
        batch_size: Number of items in the evaluated batch.
    """

    def __init__(self, failure_ratio: float, threshold: float, batch_size: int, url: str) -> None:
        super().__init__(
            message=(
                f"CRITICAL: Layout shift detected. "
                f"Failure ratio {failure_ratio:.1%} exceeds threshold {threshold:.1%}"
            ),
            context={
                "failure_ratio": failure_ratio,
                "threshold": threshold,
                "batch_size": batch_size,
                "url": url,
            },
        )
        self.failure_ratio = failure_ratio
        self.threshold = threshold
        self.batch_size = batch_size


class RateLimitError(MarketPulseError):
    """Raised when the target server returns rate-limiting responses.

    HTTP 429 or similar responses trigger this exception.
    The retry mechanism should implement exponential backoff.
    """

    def __init__(self, url: str, retry_after: int | None = None) -> None:
        super().__init__(
            message=f"Rate limited by server at '{url}'",
            context={"url": url, "retry_after_seconds": retry_after},
        )
        self.retry_after = retry_after


class SessionExpiredError(MarketPulseError):
    """Raised when the stored browser session is invalid or expired.

    The application should clear the session state and re-authenticate.
    """

    def __init__(self, state_path: str) -> None:
        super().__init__(
            message=f"Browser session at '{state_path}' is invalid or expired",
            context={"state_path": state_path},
        )


class ReportGenerationError(MarketPulseError):
    """Raised when report generation fails.

    Common causes include insufficient data, I/O errors, or
    template rendering failures.
    """

    def __init__(self, report_type: str, reason: str, output_path: str | None = None) -> None:
        super().__init__(
            message=f"Failed to generate {report_type} report: {reason}",
            context={"report_type": report_type, "reason": reason, "output_path": output_path},
        )


class LoggingInitializationError(MarketPulseError):
    """Raised when the logging system fails to initialize.

    This is a startup-blocking error - the application cannot proceed
    without a functioning logging infrastructure.
    """

    def __init__(self, log_dir: str, reason: str) -> None:
        super().__init__(
            message=f"Failed to initialize logging at '{log_dir}': {reason}",
            context={"log_dir": log_dir, "reason": reason},
        )
