"""MarketPulse-Pro core source package.

This package contains the business logic components for the web scraping pipeline:
- browser: Playwright-based browser orchestration with stealth capabilities
- extractor: Strategy pattern implementations for data extraction
- validator: Pydantic schemas and Watchdog quality monitoring
- reporter: Pandas/Plotly-based reporting and visualization
- logger: Structured JSON logging configuration
- exceptions: Custom exception hierarchy
"""

__version__ = "1.0.0"
