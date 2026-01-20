"""Tests for configuration management and validation.

Validates GlobalConfig behavior including:
- Environment variable loading precedence
- Pydantic validation rules
- Path normalization
- Singleton cache behavior

Testing Philosophy:
    Configuration errors should fail-fast at startup, not during runtime.
    These tests ensure invalid configurations are caught immediately.
"""

from pathlib import Path

import pytest
from pydantic import ValidationError

from config.settings import GlobalConfig


class TestGlobalConfigValidation:
    """Test suite for GlobalConfig validation rules."""

    def test_default_values_are_sane(self, mock_config: GlobalConfig) -> None:
        """Verify default configuration provides safe production values.

        Ensures that if .env is missing, the application can still start
        with reasonable defaults that won't cause immediate failures.
        """
        # Critical safety defaults
        assert mock_config.headless is True
        assert mock_config.max_concurrent_requests >= 1
        assert mock_config.request_timeout_ms >= 1000
        assert 0.0 <= mock_config.watchdog_failure_threshold <= 1.0

    def test_pagination_limit_validation(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Verify pagination_limit accepts 0 (unlimited) and positive integers."""
        from config.settings import get_config

        get_config.cache_clear()

        # Valid: 0 means unlimited
        monkeypatch.setenv("PAGINATION_LIMIT", "0")
        config = get_config()
        assert config.pagination_limit == 0

        get_config.cache_clear()

        # Valid: positive integer
        monkeypatch.setenv("PAGINATION_LIMIT", "10")
        config = get_config()
        assert config.pagination_limit == 10

        get_config.cache_clear()

    def test_concurrent_requests_bounds(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Verify max_concurrent_requests enforces sensible bounds (1-20)."""
        from config.settings import get_config

        get_config.cache_clear()

        # Invalid: 0 concurrent requests makes no sense
        monkeypatch.setenv("MAX_CONCURRENT_REQUESTS", "0")
        with pytest.raises(ValidationError) as exc_info:
            get_config()

        assert "MAX_CONCURRENT_REQUESTS" in str(
            exc_info.value
        ).upper() or "max_concurrent_requests" in str(exc_info.value)

        get_config.cache_clear()

        # Invalid: 100 concurrent requests is excessive for anti-bot scenarios
        monkeypatch.setenv("MAX_CONCURRENT_REQUESTS", "100")
        with pytest.raises(ValidationError):
            get_config()

        get_config.cache_clear()

    def test_watchdog_threshold_range(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Verify watchdog_failure_threshold is a valid ratio (0.0-1.0)."""
        from config.settings import get_config

        get_config.cache_clear()

        # Invalid: negative ratio
        monkeypatch.setenv("WATCHDOG_FAILURE_THRESHOLD", "-0.1")
        with pytest.raises(ValidationError):
            get_config()

        get_config.cache_clear()

        # Invalid: > 100%
        monkeypatch.setenv("WATCHDOG_FAILURE_THRESHOLD", "1.5")
        with pytest.raises(ValidationError):
            get_config()

        get_config.cache_clear()

    def test_path_field_normalization(self, mock_config: GlobalConfig) -> None:
        """Verify string paths are converted to Path objects."""
        assert isinstance(mock_config.log_dir, Path)
        assert isinstance(mock_config.output_dir, Path)
        assert isinstance(mock_config.storage_state_path, Path)

    def test_base_url_trailing_slash_normalization(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Verify base_url always ends with trailing slash for URL joining."""
        from config.settings import get_config

        get_config.cache_clear()

        monkeypatch.setenv("BASE_URL", "https://example.com")
        config = get_config()
        assert config.base_url.endswith("/")

        get_config.cache_clear()

        monkeypatch.setenv("BASE_URL", "https://example.com/")
        config = get_config()
        assert config.base_url.endswith("/")
        assert config.base_url.count("/") == 3  # https:// (2) + trailing / (1)

        get_config.cache_clear()


class TestConfigSingletonBehavior:
    """Test suite for get_config() singleton caching."""

    def test_singleton_returns_same_instance(self, mock_config: GlobalConfig) -> None:
        """Verify get_config() returns cached instance within same scope."""
        from config.settings import get_config

        # After mock_config fixture, get_config should return same instance
        config1 = get_config()
        config2 = get_config()

        assert config1 is config2

    def test_cache_clear_forces_new_instance(
        self, mock_config: GlobalConfig, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Verify cache_clear() allows reconfiguration."""
        from config.settings import get_config

        config1 = get_config()
        original_app_name = config1.app_name

        get_config.cache_clear()
        monkeypatch.setenv("APP_NAME", "NewApp")

        config2 = get_config()

        assert config1 is not config2
        assert config2.app_name == "NewApp"
        assert original_app_name != config2.app_name

        get_config.cache_clear()


class TestEnvironmentVariableOverrides:
    """Test suite for environment variable precedence."""

    def test_env_var_overrides_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Verify environment variables override default values."""
        from config.settings import get_config

        get_config.cache_clear()

        custom_timeout = "99999"
        monkeypatch.setenv("REQUEST_TIMEOUT_MS", custom_timeout)

        config = get_config()
        assert config.request_timeout_ms == int(custom_timeout)

        get_config.cache_clear()

    def test_boolean_env_var_parsing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Verify boolean environment variables are parsed correctly.

        Pydantic accepts: true/false, 1/0, yes/no, on/off (case-insensitive).
        """
        from config.settings import get_config

        test_cases = [
            ("true", True),
            ("True", True),
            ("1", True),
            ("yes", True),
            ("false", False),
            ("False", False),
            ("0", False),
            ("no", False),
        ]

        for env_value, expected in test_cases:
            get_config.cache_clear()
            monkeypatch.setenv("HEADLESS", env_value)
            config = get_config()
            assert config.headless is expected, f"Failed for {env_value}"

        get_config.cache_clear()
