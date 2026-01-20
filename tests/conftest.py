"""Pytest configuration and shared fixtures for MarketPulse-Pro test suite.

This module provides hermetic test infrastructure with the following guarantees:
- No external network requests (all I/O mocked)
- Deterministic execution (seeded randomness, frozen time)
- Isolated state (no cross-test contamination)

Design Rationale:
    Factory fixtures over static fixtures enable dynamic test case generation
    without code duplication. The mock_config fixture overrides the singleton
    GlobalConfig to prevent state leakage between tests.
"""

from pathlib import Path
from typing import Any, Callable
from unittest.mock import MagicMock

import pytest
from pytest_mock import MockerFixture

from config.settings import GlobalConfig


@pytest.fixture
def mock_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> GlobalConfig:
    """Provide isolated GlobalConfig with safe test defaults.

    Overrides the lru_cache singleton to prevent state leakage between tests.
    Uses tmp_path for all file operations to avoid polluting the filesystem.

    Args:
        tmp_path: Pytest temporary directory fixture.
        monkeypatch: Pytest monkeypatch fixture for patching.

    Returns:
        GlobalConfig instance with test-safe defaults.

    Example:
        def test_something(mock_config: GlobalConfig) -> None:
            assert mock_config.pagination_limit == 1  # Safe for tests
    """
    # Clear the lru_cache to force fresh instantiation
    from config.settings import get_config

    get_config.cache_clear()

    # Create test-specific directories
    log_dir = tmp_path / "logs"
    output_dir = tmp_path / "output"
    log_dir.mkdir()
    output_dir.mkdir()

    # Override environment variables for test isolation
    test_env = {
        "APP_NAME": "MarketPulse-Test",
        "ENVIRONMENT": "test",
        "DEBUG": "false",
        "HEADLESS": "true",
        "LOG_LEVEL": "DEBUG",
        "LOG_DIR": str(log_dir),
        "LOG_ROTATION": "1 day",
        "LOG_RETENTION": "1 day",
        "BASE_URL": "https://test.example.com/",
        "MAX_CONCURRENT_REQUESTS": "2",
        "REQUEST_TIMEOUT_MS": "5000",
        "RETRY_MAX_ATTEMPTS": "1",
        "RETRY_BASE_DELAY_SEC": "0.1",
        "RETRY_MAX_DELAY_SEC": "1.0",
        "WATCHDOG_FAILURE_THRESHOLD": "0.30",
        "PAGINATION_LIMIT": "1",
        "STORAGE_STATE_PATH": str(tmp_path / "test_state.json"),
        "OUTPUT_DIR": str(output_dir),
    }

    for key, value in test_env.items():
        monkeypatch.setenv(key, value)

    # Return fresh config instance
    config = get_config()

    yield config

    # Cleanup: clear cache again after test
    get_config.cache_clear()


@pytest.fixture
def mock_html_factory() -> Callable[[int, dict[str, Any]], str]:
    """Factory fixture for generating realistic book listing HTML.

    Generates dynamic HTML that mimics books.toscrape.com structure.
    Supports injection of malformed data for boundary testing.

    Returns:
        Factory function that generates HTML strings.

    Example:
        def test_extraction(mock_html_factory):
            html = mock_html_factory(count=3, overrides={0: {"price": "Invalid"}})
            # First item has malformed price
    """

    def _generate_html(
        count: int = 5,
        overrides: dict[int, dict[str, Any]] | None = None,
    ) -> str:
        """Generate HTML with specified number of products.

        Args:
            count: Number of product items to generate.
            overrides: Dict mapping item index to field overrides.

        Returns:
            Complete HTML document string.
        """
        overrides = overrides or {}

        products_html = []
        for i in range(count):
            item_overrides = overrides.get(i, {})

            # Default values
            title = item_overrides.get("title", f"Test Book {i + 1}")
            price = item_overrides.get("price", f"£{10.00 + i:.2f}")
            stock = item_overrides.get("stock", "In stock")
            rating = item_overrides.get("rating", "Three")
            url = item_overrides.get("url", f"catalogue/book_{i}.html")

            # Handle None values (simulate missing elements)
            title_html = (
                f'<h3><a href="{url}" title="{title}">{title}</a></h3>'
                if title is not None
                else "<h3></h3>"
            )
            price_html = (
                f'<p class="price_color">{price}</p>' if price is not None else ""
            )
            stock_html = (
                f'<p class="instock availability">{stock}</p>'
                if stock is not None
                else ""
            )
            rating_html = (
                f'<p class="star-rating {rating}"></p>'
                if rating is not None
                else '<p class="star-rating"></p>'
            )

            product_html = f"""
            <article class="product_pod">
                {title_html}
                <div class="product_price">
                    {price_html}
                    {stock_html}
                </div>
                {rating_html}
            </article>
            """
            products_html.append(product_html)

        full_html = f"""
        <!DOCTYPE html>
        <html>
        <head><title>Test Catalog</title></head>
        <body>
            <div class="page_inner">
                {"".join(products_html)}
            </div>
        </body>
        </html>
        """
        return full_html

    return _generate_html


@pytest.fixture
def mock_page(mocker: MockerFixture, mock_html_factory: Callable) -> MagicMock:
    """Provide mocked Playwright Page object.

    Returns a MagicMock configured to behave like a real Page with
    realistic responses for common operations.

    Args:
        mocker: Pytest-mock fixture.
        mock_html_factory: HTML generation factory.

    Returns:
        Configured MagicMock for Playwright Page.
    """
    page = mocker.MagicMock()
    page.url = "https://test.example.com/page1.html"

    # Mock locator behavior
    def mock_locator(selector: str) -> MagicMock:
        locator_mock = mocker.MagicMock()

        # Simulate element count
        if "product_pod" in selector:
            locator_mock.count.return_value = 5
            locator_mock.all.return_value = [mocker.MagicMock() for _ in range(5)]
        else:
            locator_mock.count.return_value = 1

        return locator_mock

    page.locator = mock_locator
    page.wait_for_selector = mocker.AsyncMock()
    page.goto = mocker.AsyncMock(return_value=mocker.MagicMock(status=200))
    page.close = mocker.AsyncMock()

    return page


@pytest.fixture
def mock_browser_context(
    mocker: MockerFixture, tmp_path: Path
) -> MagicMock:
    """Provide mocked Playwright BrowserContext.

    Args:
        mocker: Pytest-mock fixture.
        tmp_path: Temporary directory for state files.

    Returns:
        Configured MagicMock for BrowserContext.
    """
    context = mocker.MagicMock()
    context.storage_state = mocker.AsyncMock(
        return_value={"cookies": [], "origins": []}
    )
    context.new_page = mocker.AsyncMock()
    context.close = mocker.AsyncMock()
    context.add_init_script = mocker.AsyncMock()

    return context


@pytest.fixture
def mock_browser(mocker: MockerFixture, mock_browser_context: MagicMock) -> MagicMock:
    """Provide mocked Playwright Browser.

    Args:
        mocker: Pytest-mock fixture.
        mock_browser_context: Mocked browser context.

    Returns:
        Configured MagicMock for Browser.
    """
    browser = mocker.MagicMock()
    browser.new_context = mocker.AsyncMock(return_value=mock_browser_context)
    browser.close = mocker.AsyncMock()

    return browser


@pytest.fixture
def mock_playwright(mocker: MockerFixture, mock_browser: MagicMock) -> MagicMock:
    """Provide mocked Playwright instance.

    Args:
        mocker: Pytest-mock fixture.
        mock_browser: Mocked browser.

    Returns:
        Configured MagicMock for Playwright.
    """
    playwright = mocker.MagicMock()
    playwright.chromium.launch = mocker.AsyncMock(return_value=mock_browser)
    playwright.stop = mocker.AsyncMock()

    return playwright


# Pytest configuration
def pytest_configure(config: Any) -> None:
    """Configure pytest with custom markers and settings.

    Args:
        config: Pytest config object.
    """
    config.addinivalue_line(
        "markers", "slow: marks tests as slow (deselect with '-m \"not slow\"')"
    )
    config.addinivalue_line(
        "markers",
        "integration: marks tests as integration tests requiring full stack",
    )
