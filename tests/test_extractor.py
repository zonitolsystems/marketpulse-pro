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

from unittest.mock import AsyncMock, MagicMock, patch
from typing import Callable

import pytest
from pytest_mock import MockerFixture

from config.settings import GlobalConfig
from src.browser import BrowserManager
from src.exceptions import LayoutShiftError
from src.scraper import BookScraper
from src.validator import ProductSchema, QualityMonitor
from src.extractor import ExtractionResult


def create_playwright_mock(mocker: MockerFixture) -> tuple[MagicMock, MagicMock, MagicMock, MagicMock]:
    """Create properly configured Playwright mock chain for async_playwright().start() pattern."""
    context_mock = MagicMock()
    context_mock.add_init_script = AsyncMock()
    context_mock.storage_state = AsyncMock(return_value={"cookies": [], "origins": []})
    context_mock.new_page = AsyncMock()
    context_mock.close = AsyncMock()
    
    browser_mock = MagicMock()
    browser_mock.new_context = AsyncMock(return_value=context_mock)
    browser_mock.close = AsyncMock()
    
    playwright_mock = MagicMock()
    playwright_mock.chromium.launch = AsyncMock(return_value=browser_mock)
    playwright_mock.stop = AsyncMock()
    
    async_playwright_instance = MagicMock()
    async_playwright_instance.start = AsyncMock(return_value=playwright_mock)
    
    return async_playwright_instance, playwright_mock, browser_mock, context_mock


def create_mock_page(mocker: MockerFixture, elements: list[MagicMock], has_next: bool = False) -> MagicMock:
    """Create a properly configured mock page.
    
    Args:
        mocker: pytest-mock fixture
        elements: List of mock product elements
        has_next: Whether a next page link exists
    
    Returns:
        Configured MagicMock for a Playwright Page
    """
    mock_page = MagicMock()
    mock_page.url = "https://test.example.com/"
    mock_page.wait_for_selector = AsyncMock()
    mock_page.goto = AsyncMock(return_value=MagicMock(status=200))
    mock_page.close = AsyncMock()
    
    # Container locator for product elements
    container_locator = MagicMock()
    container_locator.all = AsyncMock(return_value=elements)
    
    # Next page locator
    next_locator = MagicMock()
    if has_next:
        next_locator.count = AsyncMock(return_value=1)
        next_locator.get_attribute = AsyncMock(return_value="page2.html")
    else:
        next_locator.count = AsyncMock(return_value=0)
        next_locator.get_attribute = AsyncMock(return_value=None)
    
    def locator_side_effect(selector: str) -> MagicMock:
        if "product_pod" in selector:
            return container_locator
        elif ".next" in selector:
            return next_locator
        # Default locator for wait_for_content
        default_loc = MagicMock()
        default_loc.count = AsyncMock(return_value=1)
        return default_loc
    
    mock_page.locator = locator_side_effect
    return mock_page


def create_valid_product_element(mocker: MockerFixture, index: int) -> MagicMock:
    """Create a mock product element that returns valid data."""
    element = MagicMock()
    
    title = f"Test Book {index}"
    price = f"£{10.0 + index:.2f}"
    stock = "In stock (20 available)"
    rating = "star-rating Three"
    relative_url = f"catalogue/book_{index}.html"
    
    def locator_side_effect(selector: str) -> MagicMock:
        loc = MagicMock()
        
        # Title selector: "h3 > a"
        if "h3" in selector:
            loc.get_attribute = AsyncMock(
                side_effect=lambda attr: title if attr == "title" else relative_url if attr == "href" else None
            )
            loc.inner_text = AsyncMock(return_value=title)
            return loc
        # Price selector: ".price_color"
        elif "price" in selector.lower():
            loc.inner_text = AsyncMock(return_value=price)
            loc.get_attribute = AsyncMock(return_value=None)
            return loc
        # Stock selector: ".instock.availability"
        elif "instock" in selector.lower() or "availability" in selector:
            loc.inner_text = AsyncMock(return_value=stock)
            loc.get_attribute = AsyncMock(return_value=None)
            return loc
        # Rating selector: ".star-rating"
        elif "rating" in selector.lower():
            loc.get_attribute = AsyncMock(return_value=rating)
            loc.inner_text = AsyncMock(return_value="")
            return loc
        
        # Default
        loc.inner_text = AsyncMock(return_value="")
        loc.get_attribute = AsyncMock(return_value=None)
        return loc
    
    element.locator = locator_side_effect
    return element


def create_invalid_product_element(mocker: MockerFixture, index: int) -> MagicMock:
    """Create a mock product element that returns invalid data."""
    element = MagicMock()
    
    def locator_side_effect(selector: str) -> MagicMock:
        loc = MagicMock()
        # Return empty/invalid data for all fields
        loc.inner_text = AsyncMock(return_value="")
        loc.get_attribute = AsyncMock(return_value=None)
        return loc
    
    element.locator = locator_side_effect
    return element


class TestBookScraperExtractionScenarios:
    """Test suite for BookScraper extraction scenarios."""

    @pytest.mark.asyncio
    async def test_scenario_perfect_page_all_items_valid(
        self,
        mock_config: GlobalConfig,
        mocker: MockerFixture,
        mock_html_factory: Callable,
    ) -> None:
        """Scenario 1: Perfect page with all valid items.

        Verifies:
        - All items are extracted and validated
        - Success rate is 100%
        - Watchdog passes
        """
        async_pw, pw_mock, browser_mock, context_mock = create_playwright_mock(mocker)
        mocker.patch("src.browser.async_playwright", return_value=async_pw)

        # Create 5 valid product elements
        elements = [create_valid_product_element(mocker, i) for i in range(5)]
        mock_page = create_mock_page(mocker, elements, has_next=False)

        async with BrowserManager.create(mock_config) as browser:
            context_mock.new_page = AsyncMock(return_value=mock_page)
            browser._context = context_mock

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
    ) -> None:
        """Scenario 2: Partial failure (< 30%) passes Watchdog.

        Verifies:
        - Some items fail validation
        - Failure rate <= 30%
        - Watchdog does NOT raise LayoutShiftError
        """
        async_pw, pw_mock, browser_mock, context_mock = create_playwright_mock(mocker)
        mocker.patch("src.browser.async_playwright", return_value=async_pw)

        # Create 10 items: 7 valid, 3 invalid (30% failure exactly at threshold)
        elements = [
            create_valid_product_element(mocker, i) if i < 7 else create_invalid_product_element(mocker, i)
            for i in range(10)
        ]
        mock_page = create_mock_page(mocker, elements, has_next=False)

        async with BrowserManager.create(mock_config) as browser:
            context_mock.new_page = AsyncMock(return_value=mock_page)
            browser._context = context_mock

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
    ) -> None:
        """Scenario 3: Total failure (> 30%) triggers Watchdog.

        Verifies:
        - All items fail validation (DOM changed)
        - Failure rate > 30%
        - Watchdog raises LayoutShiftError
        """
        async_pw, pw_mock, browser_mock, context_mock = create_playwright_mock(mocker)
        mocker.patch("src.browser.async_playwright", return_value=async_pw)

        # Create 10 items: all invalid
        elements = [create_invalid_product_element(mocker, i) for i in range(10)]
        mock_page = create_mock_page(mocker, elements, has_next=False)

        async with BrowserManager.create(mock_config) as browser:
            context_mock.new_page = AsyncMock(return_value=mock_page)
            browser._context = context_mock

            scraper = BookScraper(browser, mock_config)

            with pytest.raises(LayoutShiftError) as exc_info:
                await scraper.extract()

            assert exc_info.value.failure_ratio > 0.30

    @pytest.mark.asyncio
    async def test_scenario_pagination_exhaustion(
        self,
        mock_config: GlobalConfig,
        mocker: MockerFixture,
    ) -> None:
        """Scenario 4: Single page extraction (no next link).

        Verifies:
        - Scraper stops when next page link is absent
        - No infinite loop
        - Pages_scraped count is correct
        """
        async_pw, pw_mock, browser_mock, context_mock = create_playwright_mock(mocker)
        mocker.patch("src.browser.async_playwright", return_value=async_pw)

        # Create 2 valid elements, no next page
        elements = [create_valid_product_element(mocker, i) for i in range(2)]
        mock_page = create_mock_page(mocker, elements, has_next=False)

        # Unlimited pagination
        mock_config.pagination_limit = 0

        async with BrowserManager.create(mock_config) as browser:
            context_mock.new_page = AsyncMock(return_value=mock_page)
            browser._context = context_mock

            scraper = BookScraper(browser, mock_config)
            result = await scraper.extract()

            # Should have scraped exactly 1 page
            assert result.pages_scraped == 1
            assert len(result.items) == 2

    @pytest.mark.asyncio
    async def test_pagination_limit_enforcement(
        self,
        mock_config: GlobalConfig,
        mocker: MockerFixture,
    ) -> None:
        """Verify pagination_limit prevents infinite scraping.

        Critical safety feature: even if next links never end,
        scraper respects the limit.
        """
        async_pw, pw_mock, browser_mock, context_mock = create_playwright_mock(mocker)
        mocker.patch("src.browser.async_playwright", return_value=async_pw)

        # Limit to 1 page
        mock_config.pagination_limit = 1

        # Create page with has_next=True (simulating infinite pagination)
        elements = [create_valid_product_element(mocker, 0)]
        mock_page = create_mock_page(mocker, elements, has_next=True)

        async with BrowserManager.create(mock_config) as browser:
            context_mock.new_page = AsyncMock(return_value=mock_page)
            browser._context = context_mock

            scraper = BookScraper(browser, mock_config)
            result = await scraper.extract()

            # Should stop at 1 page despite next link existing
            assert result.pages_scraped == 1
            assert len(result.items) == 1
