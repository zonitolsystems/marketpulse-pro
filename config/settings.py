"""Global configuration management using pydantic-settings.

This module implements the 12-factor app methodology for configuration,
loading values from environment variables with strict type validation.
The Singleton pattern ensures consistent configuration state across the application.
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class GlobalConfig(BaseSettings):
    """Centralized configuration with environment variable binding.

    All configuration values are loaded from environment variables,
    with sensible defaults for development. Production deployments
    should override these via .env or environment injection.

    Attributes:
        app_name: Application identifier for logging and telemetry.
        environment: Deployment environment (development/staging/production).
        debug: Enable verbose debugging output.
        log_level: Minimum log level for output filtering.
        log_dir: Directory path for structured JSON log files.
        log_rotation: Log file rotation interval.
        log_retention: Log file retention period.
        base_url: Target website URL for scraping operations.
        max_concurrent_requests: Semaphore limit for async I/O operations.
        request_timeout_ms: Network request timeout in milliseconds.
        retry_max_attempts: Maximum retry attempts for failed requests.
        retry_base_delay_sec: Base delay for exponential backoff.
        retry_max_delay_sec: Maximum delay cap for backoff.
        watchdog_failure_threshold: Maximum null/failure ratio before halt.
        pagination_limit: Maximum pages to traverse (0 = unlimited).
        storage_state_path: Path for browser session persistence.
        output_dir: Directory for generated reports and exports.
        user_agents: Rotating user-agent strings for stealth.
        css_selector_title: CSS selector for product title extraction.
        css_selector_price: CSS selector for price extraction.
        css_selector_stock: CSS selector for availability status.
        css_selector_rating: CSS selector for rating extraction.
        css_selector_next_page: CSS selector for pagination control.
        css_selector_product_container: CSS selector for product list container.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Application Metadata
    app_name: str = Field(default="MarketPulse-Pro", description="Application identifier")
    environment: Literal["development", "staging", "production"] = Field(
        default="development", description="Deployment environment"
    )
    debug: bool = Field(default=False, description="Enable debug mode")

    # Browser Configuration
    headless: bool = Field(default=True, description="Run browser in headless mode")

    # Logging Configuration
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field(
        default="INFO", description="Minimum log level"
    )
    log_dir: Path = Field(default=Path("logs"), description="Log output directory")
    log_rotation: str = Field(default="1 week", description="Log rotation interval")
    log_retention: str = Field(default="1 month", description="Log retention period")

    # Target Configuration
    base_url: str = Field(
        default="https://books.toscrape.com/",
        description="Target website base URL",
    )

    # Resilience Parameters
    max_concurrent_requests: int = Field(
        default=5, ge=1, le=20, description="Async semaphore limit"
    )
    request_timeout_ms: int = Field(
        default=30000, ge=5000, le=120000, description="Request timeout in milliseconds"
    )
    retry_max_attempts: int = Field(
        default=3, ge=1, le=10, description="Maximum retry attempts"
    )
    retry_base_delay_sec: float = Field(
        default=1.0, ge=0.1, le=10.0, description="Base delay for exponential backoff"
    )
    retry_max_delay_sec: float = Field(
        default=60.0, ge=1.0, le=300.0, description="Maximum backoff delay"
    )

    # Watchdog Configuration
    watchdog_failure_threshold: float = Field(
        default=0.30, ge=0.0, le=1.0, description="Null/failure ratio threshold (0.30 = 30%)"
    )

    # Pagination
    pagination_limit: int = Field(
        default=0, ge=0, description="Max pages to scrape (0 = unlimited)"
    )

    # State Persistence
    storage_state_path: Path = Field(
        default=Path("storage_state.json"), description="Browser session state file"
    )

    # Output Configuration
    output_dir: Path = Field(default=Path("output"), description="Report output directory")

    # Stealth Configuration - User Agent Rotation Pool
    user_agents: list[str] = Field(
        default=[
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        ],
        description="User-agent rotation pool for stealth",
    )

    # CSS Selectors (Target: books.toscrape.com)
    css_selector_title: str = Field(
        default="h3 > a", description="Product title selector"
    )
    css_selector_price: str = Field(
        default=".price_color", description="Price selector"
    )
    css_selector_stock: str = Field(
        default=".instock.availability", description="Stock availability selector"
    )
    css_selector_rating: str = Field(
        default=".star-rating", description="Rating selector"
    )
    css_selector_next_page: str = Field(
        default=".next > a", description="Pagination next button selector"
    )
    css_selector_product_container: str = Field(
        default="article.product_pod", description="Product container selector"
    )

    @field_validator("log_dir", "output_dir", mode="before")
    @classmethod
    def ensure_path(cls, value: str | Path) -> Path:
        """Convert string paths to Path objects."""
        return Path(value) if isinstance(value, str) else value

    @field_validator("base_url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        """Ensure base_url ends with trailing slash for consistent URL joining."""
        return value if value.endswith("/") else f"{value}/"


@lru_cache(maxsize=1)
def get_config() -> GlobalConfig:
    """Retrieve the singleton GlobalConfig instance.

    Uses LRU cache to ensure single instantiation across the application lifecycle.
    This pattern provides thread-safe lazy initialization without explicit locking.

    Returns:
        GlobalConfig: The validated configuration instance.
    """
    return GlobalConfig()
