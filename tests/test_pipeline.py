"""Integration tests for end-to-end pipeline execution.

Validates main.py orchestration including:
- Configuration initialization
- Logging setup
- Browser → Extractor → Reporter pipeline
- Error propagation and graceful shutdown

Testing Philosophy:
    Integration tests verify component interactions, not individual logic.
    All external dependencies (Playwright, filesystem) are mocked, but
    the internal component wiring is tested end-to-end.
"""

import json
import signal
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pytest_mock import MockerFixture

from config.settings import GlobalConfig
from src.exceptions import LayoutShiftError


class TestPipelineOrchestration:
    """Test suite for main.py pipeline flow."""

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_successful_pipeline_execution(
        self,
        mock_config: GlobalConfig,
        mocker: MockerFixture,
        mock_playwright: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Verify complete pipeline executes successfully with valid data.

        Flow:
        1. Config loads
        2. Logging initializes
        3. Browser launches
        4. Scraper extracts data
        5. Reports generate
        6. Resources cleanup
        """
        # Mock Playwright
        mocker.patch(
            "src.browser.async_playwright",
            return_value=AsyncMock(__aenter__=AsyncMock(return_value=mock_playwright)),
        )

        # Mock successful extraction
        mock_result = MagicMock()
        mock_result.items = [
            MagicMock(
                title="Test Book",
                price=10.0,
                stock=True,
                rating=3,
                url="https://test.example.com",
            )
        ]
        mock_result.pages_scraped = 1
        mock_result.success_rate = 1.0

        # Mock scraper
        mock_scraper_class = mocker.patch("main.BookScraper")
        mock_scraper_instance = mock_scraper_class.return_value
        mock_scraper_instance.extract = AsyncMock(return_value=mock_result)
        mock_scraper_instance.get_quality_summary = MagicMock(
            return_value={"total_attempts": 1, "total_successes": 1}
        )

        # Mock reporter
        mock_reporter_class = mocker.patch("main.ReportGenerator")
        mock_reporter_instance = mock_reporter_class.return_value
        mock_reporter_instance.generate_all = MagicMock(
            return_value={
                "excel": tmp_path / "test.xlsx",
                "dashboard": tmp_path / "test.html",
            }
        )

        # Import and run pipeline
        from main import _run_pipeline

        exit_code = await _run_pipeline(mock_config)

        # Verify success
        assert exit_code == 0

        # Verify components were called
        mock_scraper_instance.extract.assert_called_once()
        mock_reporter_instance.generate_all.assert_called_once()

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_pipeline_handles_layout_shift_error(
        self,
        mock_config: GlobalConfig,
        mocker: MockerFixture,
        mock_playwright: MagicMock,
    ) -> None:
        """Verify pipeline propagates LayoutShiftError without catching."""
        mocker.patch(
            "src.browser.async_playwright",
            return_value=AsyncMock(__aenter__=AsyncMock(return_value=mock_playwright)),
        )

        # Mock scraper that raises LayoutShiftError
        mock_scraper_class = mocker.patch("main.BookScraper")
        mock_scraper_instance = mock_scraper_class.return_value
        mock_scraper_instance.extract = AsyncMock(
            side_effect=LayoutShiftError(
                failure_ratio=0.5,
                threshold=0.3,
                batch_size=10,
                url="https://test.example.com",
            )
        )

        from main import _run_pipeline

        with pytest.raises(LayoutShiftError):
            await _run_pipeline(mock_config)

    @pytest.mark.integration
    def test_main_handles_keyboard_interrupt(
        self,
        mocker: MockerFixture,
        mock_config: GlobalConfig,
    ) -> None:
        """Verify Ctrl+C (SIGINT) causes graceful shutdown with exit code 130."""
        # Mock asyncio.run to raise KeyboardInterrupt
        mocker.patch(
            "main.asyncio.run",
            side_effect=KeyboardInterrupt(),
        )

        # Mock config loading
        mocker.patch("main.get_config", return_value=mock_config)

        # Mock logging initialization
        mocker.patch("main.configure_logging")
        mocker.patch("main._validate_startup_requirements")

        from main import main

        exit_code = main()

        # Unix convention: 128 + signal number (SIGINT=2) = 130
        assert exit_code == 130


class TestLoggingInfrastructure:
    """Test suite for structured logging setup."""

    def test_log_directory_created_on_init(
        self,
        mock_config: GlobalConfig,
        tmp_path: Path,
    ) -> None:
        """Verify log directory is created during initialization."""
        from src.logger import configure_logging

        # mock_config uses tmp_path, so log_dir should exist
        configure_logging(mock_config)

        assert mock_config.log_dir.exists()
        assert mock_config.log_dir.is_dir()

    def test_log_file_contains_valid_json(
        self,
        mock_config: GlobalConfig,
        tmp_path: Path,
    ) -> None:
        """Verify log output is valid JSON format.

        Critical for log aggregation systems (ELK, Splunk).
        """
        from src.logger import configure_logging, get_logger

        configure_logging(mock_config)

        log = get_logger(__name__)
        log.info("Test message", test_field="test_value")

        # Find the log file (has date in filename)
        log_files = list(mock_config.log_dir.glob("*.json"))
        assert len(log_files) > 0, "No log files created"

        log_file = log_files[0]
        log_content = log_file.read_text()

        # Each line should be valid JSON
        for line in log_content.strip().split("\n"):
            if not line:
                continue

            log_entry = json.loads(line)  # Should not raise

            # Verify required fields
            assert "timestamp" in log_entry
            assert "level" in log_entry
            assert "message" in log_entry

    def test_logging_fails_fast_with_invalid_directory(
        self,
        mocker: MockerFixture,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Verify logging initialization fails fast if directory is not writable."""
        from config.settings import get_config
        from src.exceptions import LoggingInitializationError
        from src.logger import configure_logging

        get_config.cache_clear()

        # Set log_dir to an invalid path (root requires permissions)
        if sys.platform == "win32":
            invalid_path = "C:\\Windows\\System32\\MarketPulse"
        else:
            invalid_path = "/root/marketpulse"

        monkeypatch.setenv("LOG_DIR", invalid_path)
        config = get_config()

        with pytest.raises(LoggingInitializationError) as exc_info:
            configure_logging(config)

        assert invalid_path in str(exc_info.value)

        get_config.cache_clear()


class TestConfigurationValidationOnStartup:
    """Test suite for startup validation checks."""

    def test_output_directory_created_if_missing(
        self,
        mock_config: GlobalConfig,
    ) -> None:
        """Verify output directory is created during startup validation."""
        from main import _validate_startup_requirements

        # Remove output dir if it exists
        if mock_config.output_dir.exists():
            mock_config.output_dir.rmdir()

        _validate_startup_requirements(mock_config)

        assert mock_config.output_dir.exists()
        assert mock_config.output_dir.is_dir()

    def test_startup_validation_exits_on_permission_error(
        self,
        mocker: MockerFixture,
        mock_config: GlobalConfig,
    ) -> None:
        """Verify startup validation exits if output directory cannot be created."""
        from main import _validate_startup_requirements

        # Mock mkdir to raise PermissionError
        mocker.patch.object(
            Path,
            "mkdir",
            side_effect=PermissionError("Access denied"),
        )

        with pytest.raises(SystemExit):
            _validate_startup_requirements(mock_config)


class TestReportGeneration:
    """Test suite for report output."""

    def test_reports_not_generated_when_no_items(
        self,
        mock_config: GlobalConfig,
        mocker: MockerFixture,
        mock_playwright: MagicMock,
    ) -> None:
        """Verify report generation is skipped if extraction yields zero items."""
        mocker.patch(
            "src.browser.async_playwright",
            return_value=AsyncMock(__aenter__=AsyncMock(return_value=mock_playwright)),
        )

        # Mock empty extraction result
        mock_result = MagicMock()
        mock_result.items = []  # Empty list
        mock_result.pages_scraped = 1

        mock_scraper_class = mocker.patch("main.BookScraper")
        mock_scraper_instance = mock_scraper_class.return_value
        mock_scraper_instance.extract = AsyncMock(return_value=mock_result)

        # Mock reporter (should not be called)
        mock_reporter_class = mocker.patch("main.ReportGenerator")
        mock_reporter_instance = mock_reporter_class.return_value

        from main import _run_pipeline
        import asyncio

        exit_code = asyncio.run(_run_pipeline(mock_config))

        # Reporter should not be instantiated for empty results
        mock_reporter_class.assert_not_called()
        assert exit_code == 0
