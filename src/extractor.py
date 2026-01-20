"""Data extraction module implementing the Strategy Pattern.

This module provides an abstract base class for extraction strategies,
enabling polymorphic data extraction across different target websites.
Each concrete strategy encapsulates site-specific DOM traversal logic
while adhering to a consistent interface.

Design Rationale:
    The Strategy Pattern decouples extraction logic from orchestration,
    allowing new target sites to be supported by implementing a single
    class without modifying the core pipeline. This adheres to the
    Open/Closed Principle (OCP) of SOLID.

    Type hints using generics ensure compile-time validation of the
    relationship between extractors and their output schemas.
"""

from abc import ABC, abstractmethod
from typing import Generic, TypeVar

from playwright.async_api import Page
from pydantic import BaseModel

from config.settings import GlobalConfig, get_config
from src.browser import BrowserManager
from src.logger import get_logger

log = get_logger(__name__)

# Generic type variable for Pydantic models
T = TypeVar("T", bound=BaseModel)


class ExtractionResult(BaseModel, Generic[T]):
    """Container for extraction results with metadata.

    Encapsulates both the extracted data and operational metadata
    for observability and debugging purposes.

    Attributes:
        items: List of extracted and validated items.
        total_attempted: Number of items extraction was attempted on.
        total_succeeded: Number of items successfully extracted.
        pages_scraped: Number of pages processed.
        source_url: Base URL of the extraction target.
    """

    items: list[T]
    total_attempted: int
    total_succeeded: int
    pages_scraped: int
    source_url: str

    @property
    def success_rate(self) -> float:
        """Calculate extraction success rate as a ratio.

        Returns:
            Float between 0.0 and 1.0 representing success ratio.
        """
        if self.total_attempted == 0:
            return 0.0
        return self.total_succeeded / self.total_attempted

    @property
    def failure_rate(self) -> float:
        """Calculate extraction failure rate as a ratio.

        Returns:
            Float between 0.0 and 1.0 representing failure ratio.
        """
        return 1.0 - self.success_rate


class BaseExtractor(ABC, Generic[T]):
    """Abstract base class for site-specific extraction strategies.

    Defines the contract that all extractors must fulfill, including:
    - Page extraction logic
    - Pagination handling
    - Result aggregation

    Subclasses must implement the abstract methods to provide
    site-specific extraction behavior.

    Attributes:
        config: GlobalConfig instance for runtime configuration.
        browser: BrowserManager for page operations.
        _items: Accumulated extracted items across pages.
        _total_attempted: Counter for extraction attempts.
        _total_succeeded: Counter for successful extractions.
        _pages_scraped: Counter for processed pages.

    Type Parameters:
        T: Pydantic model type for extracted items.

    Example:
        class BookScraper(BaseExtractor[BookSchema]):
            async def extract_items_from_page(self, page: Page) -> list[BookSchema]:
                # Site-specific extraction logic
                ...
    """

    def __init__(
        self,
        browser: BrowserManager,
        config: GlobalConfig | None = None,
    ) -> None:
        """Initialize extractor with browser and configuration.

        Args:
            browser: Initialized BrowserManager instance.
            config: Optional GlobalConfig. Uses singleton if not provided.
        """
        self.config = config or get_config()
        self.browser = browser
        self._items: list[T] = []
        self._total_attempted: int = 0
        self._total_succeeded: int = 0
        self._pages_scraped: int = 0

    @property
    @abstractmethod
    def name(self) -> str:
        """Return human-readable name for this extractor.

        Used in logging and reporting to identify the active strategy.
        """
        ...

    @property
    @abstractmethod
    def start_url(self) -> str:
        """Return the entry point URL for extraction.

        This is typically the first page of a paginated listing
        or the main catalog page of the target site.
        """
        ...

    @abstractmethod
    async def extract_items_from_page(self, page: Page) -> list[T]:
        """Extract all items from the current page.

        Implement site-specific DOM traversal and data extraction logic.
        Each extracted item should be validated against the Pydantic schema.

        Args:
            page: Playwright Page positioned at the target content.

        Returns:
            List of extracted items (may be empty if extraction fails).

        Note:
            This method should handle individual item extraction failures
            gracefully, logging errors but continuing with remaining items.
            The Watchdog will evaluate overall batch health.
        """
        ...

    @abstractmethod
    async def get_next_page_url(self, page: Page) -> str | None:
        """Determine the URL of the next page, if any.

        Implement pagination detection logic specific to the target site.

        Args:
            page: Playwright Page at current pagination position.

        Returns:
            URL string of the next page, or None if at the last page.
        """
        ...

    async def extract(self) -> ExtractionResult[T]:
        """Execute the full extraction workflow with pagination.

        Orchestrates the complete extraction process:
        1. Navigate to start URL
        2. Extract items from current page
        3. Detect and follow pagination links
        4. Repeat until exhaustion or limit reached

        Returns:
            ExtractionResult containing all extracted items and metadata.

        Note:
            This method manages its own page lifecycle but relies on
            the injected BrowserManager for browser context.
        """
        log.info(
            "Starting extraction",
            extractor=self.name,
            start_url=self.start_url,
            pagination_limit=self.config.pagination_limit,
        )

        # Reset state for fresh extraction run
        self._items = []
        self._total_attempted = 0
        self._total_succeeded = 0
        self._pages_scraped = 0

        page = await self.browser.new_page()

        try:
            current_url: str | None = self.start_url

            while current_url is not None:
                # Check pagination limit
                if (
                    self.config.pagination_limit > 0
                    and self._pages_scraped >= self.config.pagination_limit
                ):
                    log.info(
                        "Pagination limit reached",
                        limit=self.config.pagination_limit,
                        pages_scraped=self._pages_scraped,
                    )
                    break

                # Navigate to current page
                await self.browser.navigate(page, current_url)
                self._pages_scraped += 1

                # Extract items from this page
                page_items = await self.extract_items_from_page(page)
                self._items.extend(page_items)

                log.info(
                    "Page extraction complete",
                    page_number=self._pages_scraped,
                    url=current_url,
                    items_extracted=len(page_items),
                    total_items=len(self._items),
                )

                # Get next page URL (may be None if at last page)
                current_url = await self.get_next_page_url(page)

                # Rotate user-agent periodically for stealth
                if self._pages_scraped % 5 == 0:
                    self.browser.rotate_user_agent()

        finally:
            await page.close()

        result = ExtractionResult[T](
            items=self._items,
            total_attempted=self._total_attempted,
            total_succeeded=self._total_succeeded,
            pages_scraped=self._pages_scraped,
            source_url=self.start_url,
        )

        log.info(
            "Extraction complete",
            extractor=self.name,
            total_items=len(self._items),
            pages_scraped=self._pages_scraped,
            success_rate=f"{result.success_rate:.1%}",
        )

        return result

    def record_attempt(self, succeeded: bool = True) -> None:
        """Record an extraction attempt for Watchdog metrics.

        Call this method for each item extraction attempt to enable
        accurate success/failure ratio calculation by the Watchdog.

        Args:
            succeeded: Whether the extraction attempt was successful.
        """
        self._total_attempted += 1
        if succeeded:
            self._total_succeeded += 1

    async def wait_for_content(
        self,
        page: Page,
        selector: str,
        timeout_ms: int | None = None,
    ) -> bool:
        """Wait for a selector to appear, with configurable timeout.

        Utility method for handling dynamic content loading.

        Args:
            page: Playwright Page instance.
            selector: CSS selector to wait for.
            timeout_ms: Optional timeout override.

        Returns:
            True if element appeared, False if timeout occurred.
        """
        timeout = timeout_ms or self.config.request_timeout_ms

        try:
            await page.wait_for_selector(selector, timeout=timeout)
            return True
        except TimeoutError:
            log.warning(
                "Timeout waiting for selector",
                selector=selector,
                timeout_ms=timeout,
            )
            return False
