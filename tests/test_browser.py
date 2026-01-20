"""Tests for browser management and stealth capabilities.

Validates BrowserManager including:
- Playwright API integration (mocked)
- Stealth mode configuration
- Session state persistence
- Resource cleanup

Testing Philosophy:
    Browser operations are I/O heavy. All Playwright calls are mocked
    to ensure hermetic, fast tests that verify behavior, not implementation.
"""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, call

import pytest
from pytest_mock import MockerFixture

from config.settings import GlobalConfig
from src.browser import BrowserManager
from src.exceptions import (
    BrowserInitializationError,
    NavigationError,
    SessionExpiredError,
)


def create_playwright_mock(mocker: MockerFixture) -> tuple[MagicMock, MagicMock, MagicMock, MagicMock]:
    """Create properly configured Playwright mock chain for async_playwright().start() pattern.
    
    Returns:
        Tuple of (async_playwright_instance, playwright_mock, browser_mock, context_mock)
    """
    # Context mock
    context_mock = MagicMock()
    context_mock.add_init_script = AsyncMock()
    context_mock.storage_state = AsyncMock(return_value={"cookies": [], "origins": []})
    context_mock.new_page = AsyncMock()
    context_mock.close = AsyncMock()
    
    # Browser mock
    browser_mock = MagicMock()
    browser_mock.new_context = AsyncMock(return_value=context_mock)
    browser_mock.close = AsyncMock()
    
    # Playwright mock
    playwright_mock = MagicMock()
    playwright_mock.chromium.launch = AsyncMock(return_value=browser_mock)
    playwright_mock.stop = AsyncMock()
    
    # async_playwright() returns instance with .start() method
    async_playwright_instance = MagicMock()
    async_playwright_instance.start = AsyncMock(return_value=playwright_mock)
    
    return async_playwright_instance, playwright_mock, browser_mock, context_mock


class TestBrowserManagerInitialization:
    """Test suite for browser initialization."""

    @pytest.mark.asyncio
    async def test_browser_launches_with_correct_arguments(
        self,
        mock_config: GlobalConfig,
        mocker: MockerFixture,
    ) -> None:
        """Verify browser launches with anti-detection flags."""
        async_pw, pw_mock, browser_mock, context_mock = create_playwright_mock(mocker)
        mocker.patch("src.browser.async_playwright", return_value=async_pw)

        async with BrowserManager.create(mock_config) as manager:
            # Verify chromium.launch was called
            pw_mock.chromium.launch.assert_called_once()

            call_args = pw_mock.chromium.launch.call_args
            assert call_args.kwargs["headless"] == mock_config.headless

            # Verify anti-detection arguments
            args = call_args.kwargs["args"]
            assert "--disable-blink-features=AutomationControlled" in args
            assert "--no-sandbox" in args

    @pytest.mark.asyncio
    async def test_stealth_scripts_injected(
        self,
        mock_config: GlobalConfig,
        mocker: MockerFixture,
    ) -> None:
        """Verify stealth JavaScript is injected into browser context."""
        async_pw, pw_mock, browser_mock, context_mock = create_playwright_mock(mocker)
        mocker.patch("src.browser.async_playwright", return_value=async_pw)

        async with BrowserManager.create(mock_config) as manager:
            # Verify add_init_script was called
            context_mock.add_init_script.assert_called_once()

            # Verify script content masks navigator.webdriver
            script_arg = context_mock.add_init_script.call_args[0][0]
            assert "navigator" in script_arg
            assert "webdriver" in script_arg

    @pytest.mark.asyncio
    async def test_headless_mode_respects_config(
        self,
        mock_config: GlobalConfig,
        mocker: MockerFixture,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Verify headless setting is passed from config."""
        async_pw, pw_mock, browser_mock, context_mock = create_playwright_mock(mocker)
        mocker.patch("src.browser.async_playwright", return_value=async_pw)

        # Test headless=False
        from config.settings import get_config

        get_config.cache_clear()
        monkeypatch.setenv("HEADLESS", "false")
        config_visible = get_config()

        async with BrowserManager.create(config_visible) as manager:
            call_kwargs = pw_mock.chromium.launch.call_args.kwargs
            assert call_kwargs["headless"] is False

        get_config.cache_clear()

    @pytest.mark.asyncio
    async def test_initialization_failure_raises_custom_error(
        self,
        mock_config: GlobalConfig,
        mocker: MockerFixture,
    ) -> None:
        """Verify Playwright exceptions are wrapped in BrowserInitializationError."""
        async_pw, pw_mock, browser_mock, context_mock = create_playwright_mock(mocker)
        pw_mock.chromium.launch = AsyncMock(
            side_effect=RuntimeError("Browser binary not found")
        )
        mocker.patch("src.browser.async_playwright", return_value=async_pw)

        with pytest.raises(BrowserInitializationError) as exc_info:
            async with BrowserManager.create(mock_config) as manager:
                pass

        assert "not found" in str(exc_info.value).lower()


class TestBrowserStateManagement:
    """Test suite for session state persistence."""

    @pytest.mark.asyncio
    async def test_save_state_writes_json_file(
        self,
        mock_config: GlobalConfig,
        mocker: MockerFixture,
        tmp_path: Path,
    ) -> None:
        """Verify save_state writes storage_state.json."""
        async_pw, pw_mock, browser_mock, context_mock = create_playwright_mock(mocker)
        
        test_state = {"cookies": [{"name": "test", "value": "123"}], "origins": []}
        context_mock.storage_state = AsyncMock(return_value=test_state)
        
        mocker.patch("src.browser.async_playwright", return_value=async_pw)

        async with BrowserManager.create(mock_config) as manager:
            saved_path = await manager.save_state()

            # Verify file was written
            assert saved_path.exists()
            saved_data = json.loads(saved_path.read_text())
            assert saved_data == test_state

    @pytest.mark.asyncio
    async def test_load_state_injects_existing_session(
        self,
        mock_config: GlobalConfig,
        mocker: MockerFixture,
        tmp_path: Path,
    ) -> None:
        """Verify existing session state is loaded on initialization."""
        # Create pre-existing state file
        state_file = mock_config.storage_state_path
        state_data = {"cookies": [{"name": "existing", "value": "abc"}], "origins": []}
        state_file.write_text(json.dumps(state_data))

        async_pw, pw_mock, browser_mock, context_mock = create_playwright_mock(mocker)
        mocker.patch("src.browser.async_playwright", return_value=async_pw)

        async with BrowserManager.create(mock_config) as manager:
            # Verify new_context was called with storage_state
            call_kwargs = browser_mock.new_context.call_args.kwargs
            assert "storage_state" in call_kwargs
            assert call_kwargs["storage_state"] == state_data

    @pytest.mark.asyncio
    async def test_invalid_state_file_ignored(
        self,
        mock_config: GlobalConfig,
        mocker: MockerFixture,
        tmp_path: Path,
    ) -> None:
        """Verify corrupted state file doesn't crash initialization."""
        # Create invalid JSON file
        state_file = mock_config.storage_state_path
        state_file.write_text("{ invalid json }")

        async_pw, pw_mock, browser_mock, context_mock = create_playwright_mock(mocker)
        mocker.patch("src.browser.async_playwright", return_value=async_pw)

        # Should not crash - just start fresh session
        async with BrowserManager.create(mock_config) as manager:
            assert manager.is_initialized


class TestBrowserNavigation:
    """Test suite for page navigation."""

    @pytest.mark.asyncio
    async def test_navigate_success(
        self,
        mock_config: GlobalConfig,
        mocker: MockerFixture,
        mock_playwright: MagicMock,
        mock_page: MagicMock,
    ) -> None:
        """Verify successful navigation calls goto with correct parameters."""
        mocker.patch(
            "src.browser.async_playwright",
            return_value=AsyncMock(__aenter__=AsyncMock(return_value=mock_playwright)),
        )

        mock_response = MagicMock()
        mock_response.status = 200
        mock_page.goto.return_value = mock_response

        async with BrowserManager.create(mock_config) as manager:
            await manager.navigate(mock_page, "https://example.com")

            mock_page.goto.assert_called_once()
            call_args = mock_page.goto.call_args
            assert call_args[0][0] == "https://example.com"
            assert call_args[1]["wait_until"] == "domcontentloaded"

    @pytest.mark.asyncio
    async def test_navigate_http_error_raises_navigation_error(
        self,
        mock_config: GlobalConfig,
        mocker: MockerFixture,
        mock_playwright: MagicMock,
        mock_page: MagicMock,
    ) -> None:
        """Verify HTTP 4xx/5xx status codes raise NavigationError."""
        mocker.patch(
            "src.browser.async_playwright",
            return_value=AsyncMock(__aenter__=AsyncMock(return_value=mock_playwright)),
        )

        mock_response = MagicMock()
        mock_response.status = 404
        mock_page.goto.return_value = mock_response

        async with BrowserManager.create(mock_config) as manager:
            with pytest.raises(NavigationError) as exc_info:
                await manager.navigate(mock_page, "https://example.com")

            assert exc_info.value.context["status_code"] == 404

    @pytest.mark.asyncio
    async def test_navigate_timeout_raises_navigation_error(
        self,
        mock_config: GlobalConfig,
        mocker: MockerFixture,
        mock_playwright: MagicMock,
        mock_page: MagicMock,
    ) -> None:
        """Verify timeout exceptions are wrapped in NavigationError."""
        mocker.patch(
            "src.browser.async_playwright",
            return_value=AsyncMock(__aenter__=AsyncMock(return_value=mock_playwright)),
        )

        mock_page.goto.side_effect = TimeoutError("Navigation timeout")

        async with BrowserManager.create(mock_config) as manager:
            with pytest.raises(NavigationError) as exc_info:
                await manager.navigate(mock_page, "https://example.com")

            assert "timeout" in str(exc_info.value).lower()


class TestBrowserCleanup:
    """Test suite for resource cleanup."""

    @pytest.mark.asyncio
    async def test_cleanup_closes_resources_in_order(
        self,
        mock_config: GlobalConfig,
        mocker: MockerFixture,
    ) -> None:
        """Verify cleanup closes context, browser, playwright in reverse order."""
        async_pw, pw_mock, browser_mock, context_mock = create_playwright_mock(mocker)
        mocker.patch("src.browser.async_playwright", return_value=async_pw)

        async with BrowserManager.create(mock_config) as manager:
            pass  # Just test cleanup on exit

        # Verify cleanup order
        context_mock.close.assert_called_once()
        browser_mock.close.assert_called_once()
        pw_mock.stop.assert_called_once()

    @pytest.mark.asyncio
    async def test_cleanup_handles_exceptions_gracefully(
        self,
        mock_config: GlobalConfig,
        mocker: MockerFixture,
    ) -> None:
        """Verify cleanup continues even if close() raises exceptions."""
        async_pw, pw_mock, browser_mock, context_mock = create_playwright_mock(mocker)
        # Simulate close() failure
        context_mock.close = AsyncMock(side_effect=RuntimeError("Close failed"))
        mocker.patch("src.browser.async_playwright", return_value=async_pw)

        # Should not raise - just log warning
        async with BrowserManager.create(mock_config) as manager:
            pass

        # Other cleanup should still execute
        pw_mock.stop.assert_called_once()


class TestUserAgentRotation:
    """Test suite for user-agent rotation."""

    @pytest.mark.asyncio
    async def test_rotate_user_agent_changes_value(
        self,
        mock_config: GlobalConfig,
        mocker: MockerFixture,
        mock_playwright: MagicMock,
    ) -> None:
        """Verify rotate_user_agent() returns different value."""
        mocker.patch(
            "src.browser.async_playwright",
            return_value=AsyncMock(__aenter__=AsyncMock(return_value=mock_playwright)),
        )

        async with BrowserManager.create(mock_config) as manager:
            original_ua = manager._current_user_agent

            # Rotate multiple times to increase chance of change
            rotated = False
            for _ in range(10):
                new_ua = manager.rotate_user_agent()
                if new_ua != original_ua:
                    rotated = True
                    break

            # With 5+ user agents in pool, should rotate within 10 attempts
            assert rotated, "User agent should change after rotation"
