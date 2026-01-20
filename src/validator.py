"""Data validation and quality monitoring module.

This module implements:
- Pydantic schemas for strict data validation
- QualityMonitor (Watchdog) for detecting layout shifts and data anomalies

Design Rationale:
    Data quality is critical in production scraping pipelines. Invalid or
    incomplete data can pollute downstream systems (databases, analytics).
    The Watchdog pattern provides an early warning system that halts
    execution when extraction quality degrades beyond acceptable thresholds.

    This "fail-fast" approach prevents silent data corruption and alerts
    operators to site structure changes that require selector updates.
"""

import re
from typing import Any

from pydantic import BaseModel, Field, HttpUrl, field_validator, model_validator

from config.settings import GlobalConfig, get_config
from src.exceptions import LayoutShiftError
from src.logger import get_logger

log = get_logger(__name__)


class ProductSchema(BaseModel):
    """Validated schema for extracted product data.

    All fields undergo strict validation to ensure data integrity.
    The schema is designed for books.toscrape.com but can be adapted
    for similar e-commerce product listings.

    Attributes:
        title: Product title (required, non-empty).
        price: Numeric price value (currency symbols stripped).
        stock: Availability status (True = in stock).
        rating: Star rating from 1-5.
        url: Fully qualified product URL.
    """

    title: str = Field(
        ...,
        min_length=1,
        max_length=500,
        description="Product title",
    )
    price: float = Field(
        ...,
        ge=0.0,
        description="Price in numeric format (currency stripped)",
    )
    stock: bool = Field(
        ...,
        description="Availability status",
    )
    rating: int = Field(
        ...,
        ge=1,
        le=5,
        description="Star rating (1-5)",
    )
    url: HttpUrl = Field(
        ...,
        description="Product detail page URL",
    )

    @field_validator("title", mode="before")
    @classmethod
    def clean_title(cls, value: Any) -> str:
        """Strip whitespace and normalize title text.

        Args:
            value: Raw title string from extraction.

        Returns:
            Cleaned and normalized title.

        Raises:
            ValueError: If title is empty after cleaning.
        """
        if not isinstance(value, str):
            raise ValueError(f"Title must be a string, got {type(value).__name__}")

        cleaned = " ".join(value.split())  # Normalize whitespace
        if not cleaned:
            raise ValueError("Title cannot be empty")

        return cleaned

    @field_validator("price", mode="before")
    @classmethod
    def parse_price(cls, value: Any) -> float:
        """Convert currency string to float.

        Handles common currency formats:
        - "£51.77" -> 51.77
        - "$99.99" -> 99.99
        - "€123,45" -> 123.45 (European format)
        - "1,234.56" -> 1234.56 (comma thousands separator)

        Args:
            value: Raw price string or numeric value.

        Returns:
            Numeric price as float.

        Raises:
            ValueError: If price cannot be parsed.
        """
        if isinstance(value, (int, float)):
            return float(value)

        if not isinstance(value, str):
            raise ValueError(f"Price must be string or number, got {type(value).__name__}")

        # Remove currency symbols and whitespace
        cleaned = re.sub(r"[£$€¥₹\s]", "", value.strip())

        # Handle European format (comma as decimal separator)
        if "," in cleaned and "." not in cleaned:
            # Simple European format: 123,45 → 123.45
            cleaned = cleaned.replace(",", ".")
        elif "," in cleaned and "." in cleaned:
            # Determine format by position: period before comma = European (1.234,56)
            # Comma before period = US/UK format (1,234.56)
            last_dot = cleaned.rfind(".")
            last_comma = cleaned.rfind(",")
            if last_comma > last_dot:
                # European format: 1.234,56 → period is thousands, comma is decimal
                cleaned = cleaned.replace(".", "").replace(",", ".")
            else:
                # US/UK format: 1,234.56 → comma is thousands separator
                cleaned = cleaned.replace(",", "")

        try:
            return float(cleaned)
        except ValueError as exc:
            raise ValueError(f"Cannot parse price from '{value}'") from exc

    @field_validator("rating", mode="before")
    @classmethod
    def parse_rating(cls, value: Any) -> int:
        """Convert rating representation to integer.

        Handles various rating formats:
        - Integer: 5
        - String number: "5"
        - Word format: "Five", "five", "FIVE"
        - Class name: "star-rating Five" (extracts word)

        Args:
            value: Raw rating value from extraction.

        Returns:
            Integer rating from 1-5.

        Raises:
            ValueError: If rating cannot be parsed or is out of range.
        """
        word_to_num = {
            "one": 1,
            "two": 2,
            "three": 3,
            "four": 4,
            "five": 5,
        }

        if isinstance(value, int):
            return value

        if isinstance(value, str):
            # Try direct numeric conversion
            try:
                return int(value)
            except ValueError:
                pass

            # Try word-to-number conversion
            value_lower = value.lower().strip()

            # Handle class-based format like "star-rating Three"
            for word, num in word_to_num.items():
                if word in value_lower:
                    return num

            raise ValueError(f"Cannot parse rating from '{value}'")

        raise ValueError(f"Rating must be int or string, got {type(value).__name__}")

    @field_validator("stock", mode="before")
    @classmethod
    def parse_stock(cls, value: Any) -> bool:
        """Convert stock status to boolean.

        Handles various availability representations:
        - Boolean: True/False
        - String: "In stock", "in stock", "available", "Out of stock"
        - Numeric: 1/0, >0 items

        Args:
            value: Raw stock status from extraction.

        Returns:
            Boolean availability status.
        """
        if isinstance(value, bool):
            return value

        if isinstance(value, int):
            return value > 0

        if isinstance(value, str):
            value_lower = value.lower().strip()
            in_stock_indicators = ["in stock", "available", "in_stock", "instock"]
            out_of_stock_indicators = ["out of stock", "unavailable", "sold out", "out_of_stock"]

            for indicator in in_stock_indicators:
                if indicator in value_lower:
                    return True

            for indicator in out_of_stock_indicators:
                if indicator in value_lower:
                    return False

            # Default to True if text doesn't match known patterns
            # (presence of stock element usually indicates availability)
            return bool(value_lower)

        return bool(value)

    @model_validator(mode="after")
    def validate_consistency(self) -> "ProductSchema":
        """Perform cross-field validation for data consistency.

        Ensures logical consistency between related fields.

        Returns:
            Validated model instance.
        """
        # No specific cross-field rules for current schema
        # Placeholder for future business logic validation
        return self


class QualityMonitor:
    """Watchdog for monitoring extraction quality and detecting anomalies.

    Tracks extraction attempts and success rates, triggering alerts
    when quality degrades beyond configurable thresholds. This prevents
    database pollution from layout shifts or selector drift.

    The monitor evaluates quality in batches (per-page) rather than
    cumulatively, providing granular detection of localized issues.

    Attributes:
        config: GlobalConfig with threshold settings.
        _batch_attempts: Current batch attempt counter.
        _batch_successes: Current batch success counter.
        _total_attempts: Cumulative attempt counter.
        _total_successes: Cumulative success counter.
        _current_url: URL being monitored for error context.

    Example:
        monitor = QualityMonitor()
        monitor.start_batch("https://example.com/page/1")

        for item in raw_items:
            try:
                validated = ProductSchema(**item)
                monitor.record_success()
            except ValidationError:
                monitor.record_failure()

        monitor.evaluate_batch()  # Raises LayoutShiftError if threshold exceeded
    """

    def __init__(self, config: GlobalConfig | None = None) -> None:
        """Initialize the quality monitor.

        Args:
            config: Optional GlobalConfig. Uses singleton if not provided.
        """
        self.config = config or get_config()
        self._batch_attempts: int = 0
        self._batch_successes: int = 0
        self._total_attempts: int = 0
        self._total_successes: int = 0
        self._current_url: str = ""

    def start_batch(self, url: str) -> None:
        """Begin monitoring a new extraction batch.

        Resets batch counters while preserving cumulative totals.

        Args:
            url: URL of the page being extracted (for error context).
        """
        self._batch_attempts = 0
        self._batch_successes = 0
        self._current_url = url
        log.debug("Batch monitoring started", url=url)

    def record_success(self) -> None:
        """Record a successful extraction attempt."""
        self._batch_attempts += 1
        self._batch_successes += 1
        self._total_attempts += 1
        self._total_successes += 1

    def record_failure(self) -> None:
        """Record a failed extraction attempt."""
        self._batch_attempts += 1
        self._total_attempts += 1

    @property
    def batch_failure_ratio(self) -> float:
        """Calculate failure ratio for current batch.

        Returns:
            Float between 0.0 and 1.0 representing failure ratio.
        """
        if self._batch_attempts == 0:
            return 0.0
        return 1.0 - (self._batch_successes / self._batch_attempts)

    @property
    def total_failure_ratio(self) -> float:
        """Calculate cumulative failure ratio across all batches.

        Returns:
            Float between 0.0 and 1.0 representing failure ratio.
        """
        if self._total_attempts == 0:
            return 0.0
        return 1.0 - (self._total_successes / self._total_attempts)

    def evaluate_batch(self) -> None:
        """Evaluate current batch quality against threshold.

        This method should be called after processing each page.
        If the failure ratio exceeds the configured threshold,
        a LayoutShiftError is raised to halt execution.

        Raises:
            LayoutShiftError: If batch failure ratio exceeds threshold.
        """
        if self._batch_attempts == 0:
            log.warning(
                "Empty batch evaluated - no items found",
                url=self._current_url,
            )
            return

        failure_ratio = self.batch_failure_ratio
        threshold = self.config.watchdog_failure_threshold

        log.info(
            "Batch quality evaluated",
            url=self._current_url,
            batch_attempts=self._batch_attempts,
            batch_successes=self._batch_successes,
            failure_ratio=f"{failure_ratio:.1%}",
            threshold=f"{threshold:.1%}",
        )

        # Use epsilon for floating point comparison to handle precision issues
        if failure_ratio > threshold + 1e-9:
            log.critical(
                "WATCHDOG ALERT: Failure threshold exceeded",
                failure_ratio=f"{failure_ratio:.1%}",
                threshold=f"{threshold:.1%}",
                batch_size=self._batch_attempts,
                url=self._current_url,
            )
            raise LayoutShiftError(
                failure_ratio=failure_ratio,
                threshold=threshold,
                batch_size=self._batch_attempts,
                url=self._current_url,
            )

    def get_summary(self) -> dict[str, Any]:
        """Generate summary statistics for reporting.

        Returns:
            Dictionary with cumulative quality metrics.
        """
        return {
            "total_attempts": self._total_attempts,
            "total_successes": self._total_successes,
            "total_failures": self._total_attempts - self._total_successes,
            "total_success_rate": f"{1.0 - self.total_failure_ratio:.1%}",
            "total_failure_rate": f"{self.total_failure_ratio:.1%}",
            "threshold": f"{self.config.watchdog_failure_threshold:.1%}",
        }

    def reset(self) -> None:
        """Reset all counters for a fresh monitoring session."""
        self._batch_attempts = 0
        self._batch_successes = 0
        self._total_attempts = 0
        self._total_successes = 0
        self._current_url = ""
        log.debug("Quality monitor reset")
