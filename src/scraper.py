"""Concrete scraper implementation for books.toscrape.com.

This module provides a production-ready extractor strategy for the
Books to Scrape demo site. It demonstrates:
- DOM traversal with Playwright selectors
- Pydantic validation integration
- Watchdog quality monitoring
- Robust error handling for individual item failures

Design Rationale:
    The BookScraper is intentionally coupled to books.toscrape.com's
    DOM structure. When targeting a new site, create a new concrete
    extractor rather than modifying this one (Open/Closed Principle).

    CSS selectors are externalized to GlobalConfig, enabling runtime
    adjustment without code changes for minor layout shifts.
"""

import asyncio
from urllib.parse import urljoin

from playwright.async_api import Page, Locator
from pydantic import ValidationError

from config.settings import GlobalConfig, get_config
from src.browser import BrowserManager
from src.exceptions import ExtractionError, SelectorNotFoundError
from src.extractor import BaseExtractor
from src.logger import get_logger
from src.validator import ProductSchema, QualityMonitor

log = get_logger(__name__)


class BookScraper(BaseExtractor[ProductSchema]):
    """Concrete extractor for books.toscrape.com.

    Extracts book product data including title, price, availability,
    and rating. Implements automatic pagination traversal and
    integrates with QualityMonitor for anomaly detection.

    Attributes:
        monitor: QualityMonitor instance for batch quality tracking.
        _semaphore: Asyncio semaphore for concurrent request limiting.

    Example:
        async with BrowserManager.create() as browser:
            scraper = BookScraper(browser)
            result = await scraper.extract()
            print(f"Extracted {len(result.items)} books")
    """

    def __init__(
        self,
        browser: BrowserManager,
        config: GlobalConfig | None = None,
    ) -> None:
        """Initialize BookScraper with browser and configuration.

        Args:
            browser: Initialized BrowserManager instance.
            config: Optional GlobalConfig. Uses singleton if not provided.
        """
        super().__init__(browser, config)
        self.monitor = QualityMonitor(self.config)
        self._semaphore = asyncio.Semaphore(self.config.max_concurrent_requests)

    @property
    def name(self) -> str:
        """Return extractor name for logging."""
        return "BookScraper"

    @property
    def start_url(self) -> str:
        """Return entry point URL for extraction."""
        return self.config.base_url

    async def extract_items_from_page(self, page: Page) -> list[ProductSchema]:
        """Extract all book products from the current page.

        Iterates through product containers, extracting and validating
        each item. Failed extractions are logged but do not halt
        processing - the Watchdog evaluates overall batch health.

        Args:
            page: Playwright Page positioned at a catalog page.

        Returns:
            List of validated ProductSchema instances.

        Note:
            The Watchdog is invoked at the end to evaluate batch quality.
        """
        current_url = page.url
        self.monitor.start_batch(current_url)

        validated_items: list[ProductSchema] = []

        # Wait for product containers to load
        container_selector = self.config.css_selector_product_container
        content_loaded = await self.wait_for_content(page, container_selector)

        if not content_loaded:
            log.warning(
                "Product container not found",
                selector=container_selector,
                url=current_url,
            )
            self.monitor.evaluate_batch()
            return validated_items

        # Get all product elements
        product_elements = await page.locator(container_selector).all()

        log.debug(
            "Found product elements",
            count=len(product_elements),
            url=current_url,
        )

        for idx, element in enumerate(product_elements):
            try:
                item = await self._extract_single_item(element, current_url, idx)
                if item is not None:
                    validated_items.append(item)
                    self.monitor.record_success()
                    self.record_attempt(succeeded=True)
                else:
                    self.monitor.record_failure()
                    self.record_attempt(succeeded=False)

            except Exception as exc:
                log.warning(
                    "Item extraction failed",
                    item_index=idx,
                    url=current_url,
                    error=str(exc),
                )
                self.monitor.record_failure()
                self.record_attempt(succeeded=False)

        # Evaluate batch quality (may raise LayoutShiftError)
        self.monitor.evaluate_batch()

        return validated_items

    async def _extract_single_item(
        self,
        element: Locator,
        page_url: str,
        item_idx: int,
    ) -> ProductSchema | None:
        """Extract data from a single product element.

        Args:
            element: Playwright Locator for the product container.
            page_url: Current page URL for constructing absolute URLs.
            item_idx: Item index for logging context.

        Returns:
            Validated ProductSchema or None if extraction fails.
        """
        try:
            # Extract title and URL
            title_element = element.locator(self.config.css_selector_title)
            title = await title_element.get_attribute("title")

            # Fallback to inner text if title attribute is missing
            if not title:
                title = await title_element.inner_text()

            # Extract relative URL and convert to absolute
            relative_url = await title_element.get_attribute("href")
            if relative_url:
                absolute_url = urljoin(page_url, relative_url)
            else:
                absolute_url = page_url

            # Extract price
            price_element = element.locator(self.config.css_selector_price)
            price_text = await price_element.inner_text()

            # Extract stock status
            stock_element = element.locator(self.config.css_selector_stock)
            stock_text = await stock_element.inner_text()

            # Extract rating from class attribute
            rating_element = element.locator(self.config.css_selector_rating)
            rating_classes = await rating_element.get_attribute("class")

            # Construct raw data dictionary
            raw_data = {
                "title": title,
                "price": price_text,
                "stock": stock_text,
                "rating": rating_classes,
                "url": absolute_url,
            }

            log.debug(
                "Raw item extracted",
                item_index=item_idx,
                title=title[:50] if title else "N/A",
            )

            # Validate through Pydantic schema
            validated = ProductSchema(**raw_data)
            return validated

        except ValidationError as exc:
            log.warning(
                "Validation failed for item",
                item_index=item_idx,
                errors=exc.error_count(),
                details=str(exc),
            )
            return None

        except Exception as exc:
            log.warning(
                "Extraction failed for item",
                item_index=item_idx,
                error_type=type(exc).__name__,
                error=str(exc),
            )
            return None

    async def get_next_page_url(self, page: Page) -> str | None:
        """Detect and return the next page URL if available.

        Looks for the "Next" pagination button and extracts its href.

        Args:
            page: Playwright Page at current pagination position.

        Returns:
            Absolute URL of next page, or None if at the last page.
        """
        try:
            next_selector = self.config.css_selector_next_page
            next_button = page.locator(next_selector)

            # Check if next button exists
            if await next_button.count() == 0:
                log.debug("No next page button found - reached last page")
                return None

            # Extract href and convert to absolute URL
            relative_url = await next_button.get_attribute("href")
            if not relative_url:
                log.debug("Next button has no href - reached last page")
                return None

            absolute_url = urljoin(page.url, relative_url)
            log.debug("Next page detected", url=absolute_url)
            return absolute_url

        except Exception as exc:
            log.warning(
                "Error detecting next page",
                error=str(exc),
            )
            return None

    def get_quality_summary(self) -> dict:
        """Get quality monitoring summary for reporting.

        Returns:
            Dictionary with Watchdog metrics.
        """
        return self.monitor.get_summary()
