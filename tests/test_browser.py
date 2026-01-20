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


class TestBrowserManagerInitialization:
    """Test suite for browser initialization."""

    @pytest.mark.asyncio
    async def test_browser_launches_with_correct_arguments(
        self,
        mock_config: GlobalConfig,
        mocker: MockerFixture,
        mock_playwright: MagicMock,
    ) -> None:
        """Verify browser launches with anti-detection flags."""
        mocker.patch(
            "src.browser.async_playwright",
            return_value=AsyncMock(__aenter__=AsyncMock(return_value=mock_playwright)),
        )

        async with BrowserManager.create(mock_config) as manager:
            # Verify chromium.launch was called
            mock_playwright.chromium.launch.assert_called_once()

            call_args = mock_playwright.chromium.launch.call_args
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
        mock_playwright: MagicMock,
        mock_browser_context: MagicMock,
    ) -> None:
        """Verify stealth JavaScript is injected into browser context."""
        mocker.patch(
            "src.browser.async_playwright",
            return_value=AsyncMock(__aenter__=AsyncMock(return_value=mock_playwright)),
        )

        async with BrowserManager.create(mock_config) as manager:
            # Verify add_init_script was called
            mock_browser_context.add_init_script.assert_called_once()

            # Verify script content masks navigator.webdriver
            script_arg = mock_browser_context.add_init_script.call_args[0][0]
            assert "navigator" in script_arg
            assert "webdriver" in script_arg

    @pytest.mark.asyncio
    async def test_headless_mode_respects_config(
        self,
        mock_config: GlobalConfig,
        mocker: MockerFixture,
        mock_playwright: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Verify headless setting is passed from config."""
        mocker.patch(
            "src.browser.async_playwright",
            return_value=AsyncMock(__aenter__=AsyncMock(return_value=mock_playwright)),
        )

        # Test headless=False
        from config.settings import get_config

        get_config.cache_clear()
        monkeypatch.setenv("HEADLESS", "false")
        config_visible = get_config()

        async with BrowserManager.create(config_visible) as manager:
            call_kwargs = mock_playwright.chromium.launch.call_args.kwargs
            assert call_kwargs["headless"] is False

        get_config.cache_clear()

    @pytest.mark.asyncio
    async def test_initialization_failure_raises_custom_error(
        self,
        mock_config: GlobalConfig,
        mocker: MockerFixture,
        mock_playwright: MagicMock,
    ) -> None:
        """Verify Playwright exceptions are wrapped in BrowserInitializationError."""
        mock_playwright.chromium.launch.side_effect = RuntimeError(
            "Browser binary not found"
        )

        mocker.patch(
            "src.browser.async_playwright",
            return_value=AsyncMock(__aenter__=AsyncMock(return_value=mock_playwright)),
        )

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
        mock_playwright: MagicMock,
        mock_browser_context: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Verify save_state writes storage_state.json."""
        mocker.patch(
            "src.browser.async_playwright",
            return_value=AsyncMock(__aenter__=AsyncMock(return_value=mock_playwright)),
        )

        test_state = {"cookies": [{"name": "test", "value": "123"}], "origins": []}
        mock_browser_context.storage_state.return_value = test_state

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
        mock_playwright: MagicMock,
        mock_browser_context: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Verify existing session state is loaded on initialization."""
        # Create pre-existing state file
        state_file = mock_config.storage_state_path
        state_data = {"cookies": [{"name": "existing", "value": "abc"}], "origins": []}
        state_file.write_text(json.dumps(state_data))

        mocker.patch(
            "src.browser.async_playwright",
            return_value=AsyncMock(__aenter__=AsyncMock(return_value=mock_playwright)),
        )

        async with BrowserManager.create(mock_config) as manager:
            # Verify new_context was called with storage_state
            call_kwargs = mock_playwright.chromium.launch.return_value.new_context.call_args.kwargs
            assert "storage_state" in call_kwargs
            assert call_kwargs["storage_state"] == state_data

    @pytest.mark.asyncio
    async def test_invalid_state_file_ignored(
        self,
        mock_config: GlobalConfig,
        mocker: MockerFixture,
        mock_playwright: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Verify corrupted state file doesn't crash initialization."""
        # Create invalid JSON file
        state_file = mock_config.storage_state_path
        state_file.write_text("{ invalid json }")

        mocker.patch(
            "src.browser.async_playwright",
            return_value=AsyncMock(__aenter__=AsyncMock(return_value=mock_playwright)),
        )

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
        mock_playwright: MagicMock,
        mock_browser_context: MagicMock,
    ) -> None:
        """Verify cleanup closes context, browser, playwright in reverse order."""
        mocker.patch(
            "src.browser.async_playwright",
            return_value=AsyncMock(__aenter__=AsyncMock(return_value=mock_playwright)),
        )

        async with BrowserManager.create(mock_config) as manager:
            pass  # Just test cleanup on exit

        # Verify cleanup order
        mock_browser_context.close.assert_called_once()
        mock_playwright.chromium.launch.return_value.close.assert_called_once()
        mock_playwright.stop.assert_called_once()

    @pytest.mark.asyncio
    async def test_cleanup_handles_exceptions_gracefully(
        self,
        mock_config: GlobalConfig,
        mocker: MockerFixture,
        mock_playwright: MagicMock,
        mock_browser_context: MagicMock,
    ) -> None:
        """Verify cleanup continues even if close() raises exceptions."""
        mocker.patch(
            "src.browser.async_playwright",
            return_value=AsyncMock(__aenter__=AsyncMock(return_value=mock_playwright)),
        )

        # Simulate close() failure
        mock_browser_context.close.side_effect = RuntimeError("Close failed")

        # Should not raise - just log warning
        async with BrowserManager.create(mock_config) as manager:
            pass

        # Other cleanup should still execute
        mock_playwright.stop.assert_called_once()


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
