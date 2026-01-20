"""MarketPulse-Pro Entry Point.

This module serves as the immutable bootstrap and orchestration layer.
It contains NO business logic - all functional code resides in /src.

Responsibilities:
    1. Initialize logging infrastructure (fail-fast on error)
    2. Load and validate configuration
    3. Orchestrate the scraping pipeline execution
    4. Handle top-level exceptions with graceful shutdown

Usage:
    python main.py
    # or
    make run
"""

import asyncio
import sys
from typing import NoReturn

from loguru import logger

from config.settings import GlobalConfig, get_config
from src.exceptions import (
    LayoutShiftError,
    LoggingInitializationError,
    MarketPulseError,
)
from src.logger import configure_logging


def _validate_startup_requirements(config: GlobalConfig) -> None:
    """Validate critical startup requirements before pipeline execution.

    Performs pre-flight checks to ensure the environment is properly
    configured. Implements fail-fast principle for configuration issues.

    Args:
        config: The validated GlobalConfig instance.

    Raises:
        SystemExit: If any critical validation fails.
    """
    # Ensure output directory exists or can be created
    try:
        config.output_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logger.critical(
            "Failed to create output directory",
            output_dir=str(config.output_dir),
            error=str(exc),
        )
        sys.exit(1)

    logger.debug(
        "Startup validation complete",
        output_dir=str(config.output_dir),
        base_url=config.base_url,
    )


async def _run_pipeline(config: GlobalConfig) -> int:
    """Execute the main scraping pipeline.

    This async function orchestrates the complete scraping workflow:
    1. Initialize browser manager
    2. Execute extraction strategy
    3. Validate extracted data (Watchdog)
    4. Generate reports

    Args:
        config: The validated GlobalConfig instance.

    Returns:
        Exit code (0 for success, non-zero for failure).
    """
    from src.browser import BrowserManager
    from src.reporter import ReportGenerator
    from src.scraper import BookScraper

    logger.info(
        "Pipeline execution started",
        app_name=config.app_name,
        environment=config.environment,
        base_url=config.base_url,
        pagination_limit=config.pagination_limit,
    )

    # Phase 1: Browser Initialization & Extraction
    async with BrowserManager.create(config) as browser:
        logger.info("Browser manager initialized")

        # Phase 2: Execute Extraction Strategy
        scraper = BookScraper(browser, config)
        result = await scraper.extract()

        # Save browser state for session persistence
        await browser.save_state()

    # Phase 3: Report Generation
    if len(result.items) > 0:
        logger.info(
            "Extraction complete, generating reports",
            total_items=len(result.items),
            pages_scraped=result.pages_scraped,
            success_rate=f"{result.success_rate:.1%}",
        )

        reporter = ReportGenerator(config)
        reports = reporter.generate_all(result)

        logger.info(
            "Reports generated successfully",
            excel_path=str(reports["excel"]),
            dashboard_path=str(reports["dashboard"]),
        )

        # Log quality summary
        quality_summary = scraper.get_quality_summary()
        logger.info(
            "Quality monitoring summary",
            **quality_summary,
        )
    else:
        logger.warning(
            "No items extracted - skipping report generation",
            pages_scraped=result.pages_scraped,
        )

    logger.info("Pipeline execution completed successfully")
    return 0


def _handle_fatal_error(exc: Exception) -> NoReturn:
    """Handle fatal errors with structured logging and graceful exit.

    Args:
        exc: The exception that caused the fatal error.
    """
    if isinstance(exc, LayoutShiftError):
        logger.critical(
            "CRITICAL: Layout shift detected - halting to prevent data pollution",
            failure_ratio=f"{exc.failure_ratio:.1%}",
            threshold=f"{exc.threshold:.1%}",
            batch_size=exc.batch_size,
        )
        sys.exit(2)

    if isinstance(exc, MarketPulseError):
        logger.critical(
            "Fatal application error",
            error_type=type(exc).__name__,
            message=exc.message,
            context=exc.context,
        )
        sys.exit(1)

    # Unexpected error - log full traceback
    logger.exception("Unexpected fatal error", error=str(exc))
    sys.exit(1)


def main() -> int:
    """Application entry point.

    Bootstraps the logging infrastructure, validates configuration,
    and initiates the async pipeline execution.

    Returns:
        Exit code (0 for success, non-zero for failure).
    """
    # Step 1: Load configuration (validates via Pydantic)
    try:
        config = get_config()
    except Exception as exc:
        # Cannot log yet - print to stderr
        print(f"FATAL: Configuration loading failed: {exc}", file=sys.stderr)
        return 1

    # Step 2: Initialize logging (fail-fast)
    try:
        configure_logging(config)
    except LoggingInitializationError as exc:
        print(f"FATAL: {exc}", file=sys.stderr)
        return 1

    # Step 3: Validate startup requirements
    try:
        _validate_startup_requirements(config)
    except SystemExit:
        raise
    except Exception as exc:
        logger.exception("Startup validation failed", error=str(exc))
        return 1

    # Step 4: Execute async pipeline
    try:
        exit_code = asyncio.run(_run_pipeline(config))
        return exit_code
    except KeyboardInterrupt:
        logger.warning("Pipeline interrupted by user (Ctrl+C)")
        return 130  # Standard Unix SIGINT exit code
    except Exception as exc:
        _handle_fatal_error(exc)


if __name__ == "__main__":
    sys.exit(main())
