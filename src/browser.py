"""Browser orchestration module with stealth capabilities.

This module provides a production-grade Playwright wrapper implementing:
- Stealth mode to evade basic bot detection
- User-agent rotation for request fingerprint diversity
- Session state persistence for authentication caching
- Async context manager pattern for resource lifecycle management

Design Rationale:
    The BrowserManager uses Dependency Injection rather than Singleton to
    enable easier testing and support multiple browser contexts in future
    scaling scenarios. The async context manager pattern ensures proper
    cleanup even during exception propagation.

Anti-Bot Measures:
    - Disables navigator.webdriver flag
    - Randomizes viewport dimensions within realistic bounds
    - Applies human-like timing jitter to interactions
    - Rotates user-agents from a configurable pool
"""

import asyncio
import json
import random
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator, Self

from playwright.async_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    async_playwright,
)

from config.settings import GlobalConfig, get_config
from src.exceptions import (
    BrowserInitializationError,
    NavigationError,
    SessionExpiredError,
)
from src.logger import get_logger

log = get_logger(__name__)


class BrowserManager:
    """Manages Playwright browser lifecycle with stealth and state persistence.

    This class encapsulates all browser-related operations, providing a clean
    interface for page navigation while handling anti-bot countermeasures
    and session management transparently.

    Attributes:
        config: GlobalConfig instance for runtime configuration.
        _playwright: Playwright instance (initialized on context entry).
        _browser: Browser instance (Chromium by default).
        _context: BrowserContext with stealth settings applied.

    Example:
        async with BrowserManager.create() as browser:
            page = await browser.new_page()
            await browser.navigate(page, "https://example.com")
            # ... perform extraction
    """

    def __init__(self, config: GlobalConfig) -> None:
        """Initialize BrowserManager with configuration.

        Args:
            config: GlobalConfig instance containing browser settings.

        Note:
            Do not instantiate directly. Use the `create()` class method
            or async context manager pattern for proper lifecycle management.
        """
        self.config = config
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._current_user_agent: str = self._select_user_agent()

    @classmethod
    @asynccontextmanager
    async def create(
        cls, config: GlobalConfig | None = None
    ) -> AsyncGenerator[Self, None]:
        """Factory method with async context manager for lifecycle management.

        Creates a fully initialized BrowserManager instance with browser
        launched and context configured. Ensures proper cleanup on exit.

        Args:
            config: Optional GlobalConfig. Uses singleton if not provided.

        Yields:
            Initialized BrowserManager instance.

        Raises:
            BrowserInitializationError: If browser launch fails.

        Example:
            async with BrowserManager.create() as browser:
                page = await browser.new_page()
        """
        if config is None:
            config = get_config()

        instance = cls(config)
        try:
            await instance._initialize()
            yield instance
        finally:
            await instance._cleanup()

    def _select_user_agent(self) -> str:
        """Select a random user-agent from the configured pool.

        Returns:
            Randomly selected user-agent string.
        """
        return random.choice(self.config.user_agents)

    def rotate_user_agent(self) -> str:
        """Rotate to a new user-agent for the next request batch.

        This should be called between pagination batches or after
        rate-limiting responses to vary the request fingerprint.

        Returns:
            The newly selected user-agent string.
        """
        previous = self._current_user_agent
        self._current_user_agent = self._select_user_agent()
        log.debug(
            "User-agent rotated",
            previous=previous[:50] + "...",
            current=self._current_user_agent[:50] + "...",
        )
        return self._current_user_agent

    async def _initialize(self) -> None:
        """Initialize Playwright, browser, and context with stealth settings.

        Raises:
            BrowserInitializationError: If any initialization step fails.
        """
        log.info("Initializing browser with stealth settings")

        try:
            self._playwright = await async_playwright().start()

            # Launch browser with anti-detection flags
            self._browser = await self._playwright.chromium.launch(
                headless=True,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--disable-dev-shm-usage",
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-infobars",
                    "--window-position=0,0",
                    "--ignore-certificate-errors",
                    "--ignore-certificate-errors-spki-list",
                ],
            )

            # Create context with stealth configuration
            await self._create_stealth_context()

            log.info(
                "Browser initialized successfully",
                user_agent=self._current_user_agent[:50] + "...",
            )

        except Exception as exc:
            await self._cleanup()
            raise BrowserInitializationError(
                reason=str(exc), browser_type="chromium"
            ) from exc

    async def _create_stealth_context(self) -> None:
        """Create a browser context with stealth settings applied.

        Configures viewport, user-agent, and other fingerprinting
        countermeasures to reduce bot detection probability.
        """
        if self._browser is None:
            raise BrowserInitializationError(
                reason="Browser not initialized", browser_type="chromium"
            )

        # Randomize viewport within realistic desktop bounds
        viewport_width = random.randint(1280, 1920)
        viewport_height = random.randint(720, 1080)

        # Check for existing session state
        storage_state = await self._load_state()

        context_options = {
            "viewport": {"width": viewport_width, "height": viewport_height},
            "user_agent": self._current_user_agent,
            "locale": "en-US",
            "timezone_id": "America/New_York",
            "permissions": ["geolocation"],
            "java_script_enabled": True,
            "bypass_csp": True,
        }

        if storage_state is not None:
            context_options["storage_state"] = storage_state
            log.info("Loaded existing session state")

        self._context = await self._browser.new_context(**context_options)

        # Inject stealth scripts to mask automation
        await self._inject_stealth_scripts()

    async def _inject_stealth_scripts(self) -> None:
        """Inject JavaScript to mask Playwright automation indicators.

        These scripts run before any page content loads, modifying
        browser APIs that are commonly checked by anti-bot systems.
        """
        if self._context is None:
            return

        stealth_js = """
        // Override navigator.webdriver
        Object.defineProperty(navigator, 'webdriver', {
            get: () => undefined,
        });

        // Override navigator.plugins to appear non-empty
        Object.defineProperty(navigator, 'plugins', {
            get: () => [1, 2, 3, 4, 5],
        });

        // Override navigator.languages
        Object.defineProperty(navigator, 'languages', {
            get: () => ['en-US', 'en'],
        });

        // Override chrome runtime to appear as real Chrome
        window.chrome = {
            runtime: {},
        };

        // Override permissions query
        const originalQuery = window.navigator.permissions.query;
        window.navigator.permissions.query = (parameters) => (
            parameters.name === 'notifications' ?
                Promise.resolve({ state: Notification.permission }) :
                originalQuery(parameters)
        );
        """

        await self._context.add_init_script(stealth_js)
        log.debug("Stealth scripts injected")

    async def _load_state(self) -> dict | None:
        """Load browser session state from disk if valid.

        Returns:
            Storage state dictionary if valid file exists, None otherwise.

        Note:
            Returns None silently if file doesn't exist or is invalid,
            allowing fresh session initialization.
        """
        state_path = self.config.storage_state_path

        if not state_path.exists():
            log.debug("No existing session state found", path=str(state_path))
            return None

        try:
            state_data = json.loads(state_path.read_text(encoding="utf-8"))

            # Basic validation - ensure required keys exist
            if "cookies" not in state_data or "origins" not in state_data:
                log.warning(
                    "Invalid session state structure, starting fresh",
                    path=str(state_path),
                )
                return None

            return state_data

        except json.JSONDecodeError as exc:
            log.warning(
                "Corrupted session state file, starting fresh",
                path=str(state_path),
                error=str(exc),
            )
            return None
        except OSError as exc:
            log.warning(
                "Failed to read session state, starting fresh",
                path=str(state_path),
                error=str(exc),
            )
            return None

    async def save_state(self) -> Path:
        """Persist current browser session state to disk.

        Saves cookies and localStorage for session resumption.
        This enables skipping login flows on subsequent runs.

        Returns:
            Path to the saved state file.

        Raises:
            SessionExpiredError: If context is not initialized.
        """
        if self._context is None:
            raise SessionExpiredError(state_path=str(self.config.storage_state_path))

        state_path = self.config.storage_state_path

        try:
            storage_state = await self._context.storage_state()
            state_path.write_text(
                json.dumps(storage_state, indent=2), encoding="utf-8"
            )
            log.info("Session state saved", path=str(state_path))
            return state_path

        except Exception as exc:
            log.error(
                "Failed to save session state",
                path=str(state_path),
                error=str(exc),
            )
            raise

    async def new_page(self) -> Page:
        """Create a new page within the current browser context.

        Returns:
            Playwright Page instance ready for navigation.

        Raises:
            BrowserInitializationError: If context is not initialized.
        """
        if self._context is None:
            raise BrowserInitializationError(
                reason="Browser context not initialized", browser_type="chromium"
            )

        page = await self._context.new_page()

        # Set default timeout from configuration
        page.set_default_timeout(self.config.request_timeout_ms)
        page.set_default_navigation_timeout(self.config.request_timeout_ms)

        log.debug("New page created")
        return page

    async def navigate(
        self,
        page: Page,
        url: str,
        wait_until: str = "domcontentloaded",
    ) -> None:
        """Navigate to URL with error handling and human-like delay.

        Args:
            page: Playwright Page instance.
            url: Target URL to navigate to.
            wait_until: Navigation wait condition (load, domcontentloaded, networkidle).

        Raises:
            NavigationError: If navigation fails or times out.
        """
        # Apply human-like jitter before navigation
        jitter_ms = random.randint(100, 500)
        await asyncio.sleep(jitter_ms / 1000)

        log.debug("Navigating to URL", url=url, wait_until=wait_until)

        try:
            response = await page.goto(url, wait_until=wait_until)

            if response is None:
                raise NavigationError(url=url, reason="No response received")

            status_code = response.status

            if status_code >= 400:
                raise NavigationError(
                    url=url,
                    reason=f"HTTP {status_code}",
                    status_code=status_code,
                )

            log.info(
                "Navigation successful",
                url=url,
                status_code=status_code,
            )

        except TimeoutError as exc:
            raise NavigationError(
                url=url,
                reason=f"Navigation timeout after {self.config.request_timeout_ms}ms",
            ) from exc
        except Exception as exc:
            if isinstance(exc, NavigationError):
                raise
            raise NavigationError(url=url, reason=str(exc)) from exc

    async def _cleanup(self) -> None:
        """Clean up browser resources in reverse initialization order."""
        if self._context is not None:
            try:
                await self._context.close()
            except Exception as exc:
                log.warning("Error closing context", error=str(exc))
            self._context = None

        if self._browser is not None:
            try:
                await self._browser.close()
            except Exception as exc:
                log.warning("Error closing browser", error=str(exc))
            self._browser = None

        if self._playwright is not None:
            try:
                await self._playwright.stop()
            except Exception as exc:
                log.warning("Error stopping playwright", error=str(exc))
            self._playwright = None

        log.info("Browser resources cleaned up")

    @property
    def context(self) -> BrowserContext | None:
        """Access the current browser context (for advanced operations)."""
        return self._context

    @property
    def is_initialized(self) -> bool:
        """Check if browser is fully initialized and ready."""
        return all([
            self._playwright is not None,
            self._browser is not None,
            self._context is not None,
        ])
