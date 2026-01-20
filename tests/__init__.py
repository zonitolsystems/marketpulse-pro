"""Test suite for MarketPulse-Pro.

This package contains hermetic tests following the pytest framework.
Tests are structured to mirror the src/ package hierarchy for discoverability.

Testing Philosophy:
    - Use pytest-mock and responses for network isolation
    - Focus coverage on complex transformations and Watchdog logic
    - Avoid external dependencies - all I/O should be mocked
"""
