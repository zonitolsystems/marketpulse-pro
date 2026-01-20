"""Tests for extraction logic and scraper implementation.

Validates BookScraper including:
- DOM traversal and data extraction
- Watchdog integration
- Pagination handling
- Error recovery

Testing Philosophy:
    Test the extractor against realistic but controlled HTML fixtures.
    Use the factory pattern to inject boundary conditions without duplication.
"""

from unittest.mock import AsyncMock, MagicMock
from typing import Callable

import pytest
from pytest_mock import MockerFixture

from config.settings import GlobalConfig
from src.browser import BrowserManager
from src.exceptions import LayoutShiftError
from src.scraper import BookScraper
from src.validator import ProductSchema


class TestBookScraperExtractionScenarios:
    """Test suite for BookScraper extraction scenarios."""

    @pytest.mark.asyncio
    async def test_scenario_perfect_page_all_items_valid(
        self,
        mock_config: GlobalConfig,
        mocker: MockerFixture,
        mock_playwright: MagicMock,
        mock_html_factory: Callable,
    ) -> None:
        """Scenario 1: Perfect page with all valid items.

        Verifies:
        - All items are extracted and validated
        - Success rate is 100%
        - Watchdog passes
        """
        mocker.patch(
            "src.browser.async_playwright",
            return_value=AsyncMock(__aenter__=AsyncMock(return_value=mock_playwright)),
        )

        # Generate perfect HTML with 5 items
        html = mock_html_factory(count=5)

        # Mock page locator behavior
        mock_page = MagicMock()
        mock_page.url = "https://test.example.com"
        mock_page.wait_for_selector = AsyncMock()

        # Mock product container locator
        container_locator = MagicMock()
        container_locator.all = AsyncMock(return_value=[
            _create_mock_product_element(mocker, i) for i in range(5)
        ])

        # Mock next page selector (no next page)
        next_locator = MagicMock()
        next_locator.count = AsyncMock(return_value=0)

        def locator_side_effect(selector: str) -> MagicMock:
            if "product_pod" in selector:
                return container_locator
            elif ".next" in selector:
                return next_locator
            return MagicMock()

        mock_page.locator = locator_side_effect
        mock_page.close = AsyncMock()

        async with BrowserManager.create(mock_config) as browser:
            # Mock new_page to return our configured page
            browser._context.new_page = AsyncMock(return_value=mock_page)

            scraper = BookScraper(browser, mock_config)
            result = await scraper.extract()

            # Verify all items extracted
            assert len(result.items) == 5
            assert result.total_attempted == 5
            assert result.total_succeeded == 5
            assert result.success_rate == 1.0

    @pytest.mark.asyncio
    async def test_scenario_partial_failure_watchdog_passes(
        self,
        mock_config: GlobalConfig,
        mocker: MockerFixture,
        mock_playwright: MagicMock,
    ) -> None:
        """Scenario 2: Partial failure (< 30%) passes Watchdog.

        Verifies:
        - Some items fail validation
        - Failure rate < 30%
        - Watchdog does NOT raise LayoutShiftError
        """
        mocker.patch(
            "src.browser.async_playwright",
            return_value=AsyncMock(__aenter__=AsyncMock(return_value=mock_playwright)),
        )

        mock_page = MagicMock()
        mock_page.url = "https://test.example.com"
        mock_page.wait_for_selector = AsyncMock()

        # Create 10 items: 7 valid, 3 invalid (30% failure exactly)
        container_locator = MagicMock()
        elements = [
            _create_mock_product_element(mocker, i, valid=(i < 7))
            for i in range(10)
        ]
        container_locator.all = AsyncMock(return_value=elements)

        next_locator = MagicMock()
        next_locator.count = AsyncMock(return_value=0)

        def locator_side_effect(selector: str) -> MagicMock:
            if "product_pod" in selector:
                return container_locator
            elif ".next" in selector:
                return next_locator
            return MagicMock()

        mock_page.locator = locator_side_effect
        mock_page.close = AsyncMock()

        async with BrowserManager.create(mock_config) as browser:
            browser._context.new_page = AsyncMock(return_value=mock_page)

            scraper = BookScraper(browser, mock_config)

            # Should not raise - 30% is exactly at threshold
            result = await scraper.extract()

            assert len(result.items) == 7
            assert result.total_attempted == 10
            assert result.failure_rate == pytest.approx(0.30, abs=0.01)

    @pytest.mark.asyncio
    async def test_scenario_total_failure_watchdog_raises(
        self,
        mock_config: GlobalConfig,
        mocker: MockerFixture,
        mock_playwright: MagicMock,
    ) -> None:
        """Scenario 3: Total failure (> 30%) triggers Watchdog.

        Verifies:
        - All items fail validation (DOM changed)
        - Failure rate > 30%
        - Watchdog raises LayoutShiftError
        """
        mocker.patch(
            "src.browser.async_playwright",
            return_value=AsyncMock(__aenter__=AsyncMock(return_value=mock_playwright)),
        )

        mock_page = MagicMock()
        mock_page.url = "https://test.example.com"
        mock_page.wait_for_selector = AsyncMock()

        # Create 10 items: all invalid
        container_locator = MagicMock()
        elements = [
            _create_mock_product_element(mocker, i, valid=False)
            for i in range(10)
        ]
        container_locator.all = AsyncMock(return_value=elements)

        next_locator = MagicMock()
        next_locator.count = AsyncMock(return_value=0)

        def locator_side_effect(selector: str) -> MagicMock:
            if "product_pod" in selector:
                return container_locator
            elif ".next" in selector:
                return next_locator
            return MagicMock()

        mock_page.locator = locator_side_effect
        mock_page.close = AsyncMock()

        async with BrowserManager.create(mock_config) as browser:
            browser._context.new_page = AsyncMock(return_value=mock_page)

            scraper = BookScraper(browser, mock_config)

            with pytest.raises(LayoutShiftError) as exc_info:
                await scraper.extract()

            assert exc_info.value.failure_ratio > 0.30

    @pytest.mark.asyncio
    async def test_scenario_pagination_exhaustion(
        self,
        mock_config: GlobalConfig,
        mocker: MockerFixture,
        mock_playwright: MagicMock,
    ) -> None:
        """Scenario 4: Pagination reaches natural end (no next link).

        Verifies:
        - Scraper stops when next page link is absent
        - No infinite loop
        - Pages_scraped count is correct
        """
        mocker.patch(
            "src.browser.async_playwright",
            return_value=AsyncMock(__aenter__=AsyncMock(return_value=mock_playwright)),
        )

        # Configure for 3 pages, then stop
        page_count = [0]

        def create_page_for_iteration() -> MagicMock:
            mock_page = MagicMock()
            mock_page.url = f"https://test.example.com/page{page_count[0] + 1}"
            mock_page.wait_for_selector = AsyncMock()

            container_locator = MagicMock()
            elements = [_create_mock_product_element(mocker, i) for i in range(2)]
            container_locator.all = AsyncMock(return_value=elements)

            # Next page exists for first 2 pages, then None
            next_locator = MagicMock()
            if page_count[0] < 2:
                next_locator.count = AsyncMock(return_value=1)
                next_locator.get_attribute = AsyncMock(
                    return_value=f"page{page_count[0] + 2}.html"
                )
            else:
                next_locator.count = AsyncMock(return_value=0)

            def locator_side_effect(selector: str) -> MagicMock:
                if "product_pod" in selector:
                    return container_locator
                elif ".next" in selector:
                    return next_locator
                return MagicMock()

            mock_page.locator = locator_side_effect
            mock_page.close = AsyncMock()

            page_count[0] += 1
            return mock_page

        # Override pagination limit to allow 3 pages
        mock_config.pagination_limit = 0  # Unlimited

        async with BrowserManager.create(mock_config) as browser:
            browser.navigate = AsyncMock()

            # Return different pages per call
            pages = [create_page_for_iteration() for _ in range(3)]
            browser._context.new_page = AsyncMock(side_effect=pages)

            scraper = BookScraper(browser, mock_config)
            result = await scraper.extract()

            # Should have scraped 3 pages (2 items each = 6 total)
            assert result.pages_scraped == 3
            assert len(result.items) == 6

    @pytest.mark.asyncio
    async def test_pagination_limit_enforcement(
        self,
        mock_config: GlobalConfig,
        mocker: MockerFixture,
        mock_playwright: MagicMock,
    ) -> None:
        """Verify pagination_limit prevents infinite scraping.

        Critical safety feature: even if next links never end,
        scraper respects the limit.
        """
        mocker.patch(
            "src.browser.async_playwright",
            return_value=AsyncMock(__aenter__=AsyncMock(return_value=mock_playwright)),
        )

        mock_config.pagination_limit = 2  # Only allow 2 pages

        def create_infinite_page() -> MagicMock:
            mock_page = MagicMock()
            mock_page.url = "https://test.example.com"
            mock_page.wait_for_selector = AsyncMock()

            container_locator = MagicMock()
            container_locator.all = AsyncMock(return_value=[
                _create_mock_product_element(mocker, 0)
            ])

            # Always has next page (simulate infinite pagination)
            next_locator = MagicMock()
            next_locator.count = AsyncMock(return_value=1)
            next_locator.get_attribute = AsyncMock(return_value="next.html")

            def locator_side_effect(selector: str) -> MagicMock:
                if "product_pod" in selector:
                    return container_locator
                elif ".next" in selector:
                    return next_locator
                return MagicMock()

            mock_page.locator = locator_side_effect
            mock_page.close = AsyncMock()
            return mock_page

        async with BrowserManager.create(mock_config) as browser:
            browser.navigate = AsyncMock()
            browser._context.new_page = AsyncMock(side_effect=lambda: create_infinite_page())

            scraper = BookScraper(browser, mock_config)
            result = await scraper.extract()

            # Should stop at 2 pages despite infinite next links
            assert result.pages_scraped == 2


def _create_mock_product_element(
    mocker: MockerFixture,
    index: int,
    valid: bool = True,
) -> MagicMock:
    """Helper to create mock product element with configurable validity.

    Args:
        mocker: Pytest mocker fixture.
        index: Item index for unique values.
        valid: If False, returns invalid data to trigger validation errors.

    Returns:
        MagicMock configured to behave like a Playwright Locator.
    """
    element = MagicMock()

    if valid:
        # Valid data
        title = f"Test Book {index}"
        price = f"£{10.0 + index:.2f}"
        stock = "In stock"
        rating = "Three"
        url = f"catalogue/book_{index}.html"
    else:
        # Invalid data (missing required fields)
        title = None  # Will cause extraction to return None
        price = "Invalid"
        stock = None
        rating = None
        url = "invalid"

    # Mock locator chain
    def create_field_locator(value: str | None, attr: str | None = None) -> MagicMock:
        loc = MagicMock()
        if attr:
            loc.get_attribute = AsyncMock(return_value=value)
        else:
            loc.inner_text = AsyncMock(return_value=value if value else "")
        return loc

    def locator_side_effect(selector: str) -> MagicMock:
        if "h3 > a" in selector or self.config.css_selector_title in selector:
            loc = create_field_locator(title, attr="title")
            loc.get_attribute = AsyncMock(
                side_effect=lambda attr: title if attr == "title" else url if attr == "href" else None
            )
            loc.inner_text = AsyncMock(return_value=title if title else "")
            return loc
        elif ".price_color" in selector:
            return create_field_locator(price)
        elif ".instock" in selector:
            return create_field_locator(stock)
        elif ".star-rating" in selector:
            loc = create_field_locator(None, attr="class")
            loc.get_attribute = AsyncMock(return_value=f"star-rating {rating}" if rating else "")
            return loc
        return MagicMock()

    element.locator = locator_side_effect
    return element
