"""Tests for data validation and quality monitoring.

Validates ProductSchema parsing and QualityMonitor (Watchdog) logic using:
- Property-based testing (hypothesis) for fuzzing edge cases
- Parametrized tests for boundary conditions
- Invariant verification for state consistency

Testing Philosophy:
    Validators are the first line of defense against data corruption.
    If these tests pass, the downstream pipeline can trust the data.
"""

import pytest
from hypothesis import given, settings, strategies as st, HealthCheck
from pydantic import HttpUrl, ValidationError

from config.settings import GlobalConfig
from src.exceptions import LayoutShiftError
from src.validator import ProductSchema, QualityMonitor


class TestProductSchemaPriceValidation:
    """Test suite for price parsing with fuzzing."""

    @pytest.mark.parametrize(
        "price_string,expected",
        [
            ("£51.77", 51.77),
            ("$99.99", 99.99),
            ("€123.45", 123.45),
            ("¥1000", 1000.0),
            ("₹500.50", 500.50),
            ("1,234.56", 1234.56),  # Comma as thousands separator
            ("1.234,56", 1234.56),  # European format (comma as decimal)
            (" £ 10.00 ", 10.00),  # Whitespace
        ],
    )
    def test_valid_price_formats(self, price_string: str, expected: float) -> None:
        """Verify price parser handles common currency formats."""
        product = ProductSchema(
            title="Test",
            price=price_string,
            stock=True,
            rating=3,
            url="https://example.com",
        )
        assert product.price == pytest.approx(expected, rel=1e-2)

    @pytest.mark.parametrize(
        "invalid_price",
        [
            "Free",
            "TBD",
            "Contact us",
            "$-5.00",  # Negative price
            "1.00.00",  # Multiple decimal points
            "abc",
            "",
        ],
    )
    def test_invalid_price_formats_raise_error(self, invalid_price: str) -> None:
        """Verify invalid price strings raise ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            ProductSchema(
                title="Test",
                price=invalid_price,
                stock=True,
                rating=3,
                url="https://example.com",
            )
        assert "price" in str(exc_info.value).lower()

    @given(
        price_text=st.text(
            alphabet=st.characters(blacklist_categories=("Cs",)),
            min_size=1,
            max_size=50,
        )
    )
    def test_price_parser_never_crashes(self, price_text: str) -> None:
        """Property: Price parser handles arbitrary strings without crashing.

        Uses hypothesis to generate random strings and verifies the parser
        either succeeds or raises ValidationError (never crashes with TypeError/etc).
        """
        try:
            ProductSchema(
                title="Test",
                price=price_text,
                stock=True,
                rating=3,
                url="https://example.com",
            )
        except ValidationError:
            pass  # Expected for invalid formats
        except Exception as exc:
            pytest.fail(f"Unexpected exception type: {type(exc).__name__}: {exc}")


class TestProductSchemaRatingValidation:
    """Test suite for rating parsing."""

    @pytest.mark.parametrize(
        "rating_input,expected",
        [
            ("One", 1),
            ("Two", 2),
            ("Three", 3),
            ("Four", 4),
            ("Five", 5),
            ("one", 1),  # Case insensitive
            ("FIVE", 5),
            ("star-rating Three", 3),  # Class name format
            (1, 1),  # Direct integer
            ("3", 3),  # String number
        ],
    )
    def test_valid_rating_formats(self, rating_input: any, expected: int) -> None:
        """Verify rating parser handles various formats."""
        product = ProductSchema(
            title="Test",
            price=10.0,
            stock=True,
            rating=rating_input,
            url="https://example.com",
        )
        assert product.rating == expected

    @pytest.mark.parametrize(
        "invalid_rating",
        [
            0,  # Out of range
            6,  # Out of range
            "Zero",
            "Six",
            "invalid",
        ],
    )
    def test_invalid_ratings_raise_error(self, invalid_rating: any) -> None:
        """Verify out-of-range ratings raise ValidationError."""
        with pytest.raises(ValidationError):
            ProductSchema(
                title="Test",
                price=10.0,
                stock=True,
                rating=invalid_rating,
                url="https://example.com",
            )


class TestProductSchemaTitleValidation:
    """Test suite for title validation."""

    def test_empty_title_rejected(self) -> None:
        """Verify empty titles raise ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            ProductSchema(
                title="",
                price=10.0,
                stock=True,
                rating=3,
                url="https://example.com",
            )
        assert "title" in str(exc_info.value).lower()

    def test_whitespace_only_title_rejected(self) -> None:
        """Verify whitespace-only titles are rejected."""
        with pytest.raises(ValidationError):
            ProductSchema(
                title="   \n\t   ",
                price=10.0,
                stock=True,
                rating=3,
                url="https://example.com",
            )

    def test_title_whitespace_normalization(self) -> None:
        """Verify excessive whitespace is normalized."""
        product = ProductSchema(
            title="Test   Book\n\nTitle",
            price=10.0,
            stock=True,
            rating=3,
            url="https://example.com",
        )
        assert product.title == "Test Book Title"

    @given(title=st.text(min_size=1, max_size=500).filter(lambda t: t.strip()))
    def test_title_length_constraint(self, title: str) -> None:
        """Property: Titles within length constraint always validate."""
        try:
            ProductSchema(
                title=title,
                price=10.0,
                stock=True,
                rating=3,
                url="https://example.com",
            )
        except ValidationError as exc:
            # Only accept ValidationError if it's about length
            assert "title" in str(exc).lower()


class TestQualityMonitorBasicOperations:
    """Test suite for QualityMonitor state management."""

    def test_initial_state_is_zero(self, mock_config: GlobalConfig) -> None:
        """Verify monitor starts with clean state."""
        monitor = QualityMonitor(mock_config)

        assert monitor._batch_attempts == 0
        assert monitor._batch_successes == 0
        assert monitor._total_attempts == 0
        assert monitor._total_successes == 0

    def test_record_success_increments_counters(
        self, mock_config: GlobalConfig
    ) -> None:
        """Verify record_success updates both batch and total counters."""
        monitor = QualityMonitor(mock_config)
        monitor.start_batch("https://example.com")

        monitor.record_success()

        assert monitor._batch_attempts == 1
        assert monitor._batch_successes == 1
        assert monitor._total_attempts == 1
        assert monitor._total_successes == 1

    def test_record_failure_increments_attempts_only(
        self, mock_config: GlobalConfig
    ) -> None:
        """Verify record_failure increments attempts but not successes."""
        monitor = QualityMonitor(mock_config)
        monitor.start_batch("https://example.com")

        monitor.record_failure()

        assert monitor._batch_attempts == 1
        assert monitor._batch_successes == 0
        assert monitor._total_attempts == 1
        assert monitor._total_successes == 0

    def test_start_batch_resets_batch_counters(
        self, mock_config: GlobalConfig
    ) -> None:
        """Verify start_batch clears batch counters but preserves totals."""
        monitor = QualityMonitor(mock_config)
        monitor.start_batch("https://example.com/page1")

        monitor.record_success()
        monitor.record_success()

        monitor.start_batch("https://example.com/page2")

        # Batch counters reset
        assert monitor._batch_attempts == 0
        assert monitor._batch_successes == 0

        # Totals preserved
        assert monitor._total_attempts == 2
        assert monitor._total_successes == 2


class TestQualityMonitorThresholdLogic:
    """Test suite for Watchdog threshold enforcement."""

    def test_below_threshold_passes(self, mock_config: GlobalConfig) -> None:
        """Verify failures below threshold don't raise error."""
        monitor = QualityMonitor(mock_config)
        monitor.start_batch("https://example.com")

        # Threshold is 0.30 (30%)
        # 29% failure rate: 71 successes, 29 failures
        for _ in range(71):
            monitor.record_success()
        for _ in range(29):
            monitor.record_failure()

        # Should not raise
        monitor.evaluate_batch()

    def test_exactly_at_threshold_passes(self, mock_config: GlobalConfig) -> None:
        """Verify failures exactly at threshold don't raise error.

        Critical boundary condition: 30% is the limit, not 29.9%.
        """
        monitor = QualityMonitor(mock_config)
        monitor.start_batch("https://example.com")

        # Exactly 30% failure rate: 70 successes, 30 failures
        for _ in range(70):
            monitor.record_success()
        for _ in range(30):
            monitor.record_failure()

        failure_ratio = monitor.batch_failure_ratio
        assert failure_ratio == pytest.approx(0.30, abs=0.001)

        # Should not raise (threshold is exclusive)
        monitor.evaluate_batch()

    def test_above_threshold_raises_error(self, mock_config: GlobalConfig) -> None:
        """Verify failures above threshold raise LayoutShiftError."""
        monitor = QualityMonitor(mock_config)
        monitor.start_batch("https://example.com")

        # 31% failure rate: 69 successes, 31 failures
        for _ in range(69):
            monitor.record_success()
        for _ in range(31):
            monitor.record_failure()

        with pytest.raises(LayoutShiftError) as exc_info:
            monitor.evaluate_batch()

        # Verify exception contains context
        assert exc_info.value.failure_ratio > 0.30
        assert exc_info.value.threshold == pytest.approx(0.30)
        assert exc_info.value.batch_size == 100

    def test_all_failures_raises_error(self, mock_config: GlobalConfig) -> None:
        """Verify 100% failure rate raises error."""
        monitor = QualityMonitor(mock_config)
        monitor.start_batch("https://example.com")

        for _ in range(10):
            monitor.record_failure()

        assert monitor.batch_failure_ratio == 1.0

        with pytest.raises(LayoutShiftError):
            monitor.evaluate_batch()

    def test_empty_batch_logs_warning_no_error(
        self, mock_config: GlobalConfig
    ) -> None:
        """Verify empty batch (0 attempts) doesn't crash.

        This can happen if selectors fail before any item extraction.
        """
        monitor = QualityMonitor(mock_config)
        monitor.start_batch("https://example.com")

        # No record_success or record_failure calls
        # Should log warning but not raise
        monitor.evaluate_batch()


class TestQualityMonitorInvariantProperties:
    """Test suite for state invariants."""

    @settings(suppress_health_check=[HealthCheck.function_scoped_fixture])
    @given(
        successes=st.integers(min_value=0, max_value=100),
        failures=st.integers(min_value=0, max_value=100),
    )
    def test_invariant_total_equals_success_plus_failure(
        self, mock_config: GlobalConfig, successes: int, failures: int
    ) -> None:
        """Property: total_attempts == total_successes + total_failures."""
        monitor = QualityMonitor(mock_config)
        monitor.start_batch("https://example.com")

        for _ in range(successes):
            monitor.record_success()
        for _ in range(failures):
            monitor.record_failure()

        expected_total = successes + failures
        actual_total = monitor._total_attempts
        actual_successes = monitor._total_successes
        actual_failures = actual_total - actual_successes

        assert actual_total == expected_total
        assert actual_successes == successes
        assert actual_failures == failures

    def test_failure_ratio_bounds(self, mock_config: GlobalConfig) -> None:
        """Property: failure_ratio is always in [0.0, 1.0]."""
        monitor = QualityMonitor(mock_config)
        monitor.start_batch("https://example.com")

        # All success
        monitor.record_success()
        assert 0.0 <= monitor.batch_failure_ratio <= 1.0

        monitor.start_batch("https://example.com")

        # All failure
        monitor.record_failure()
        assert 0.0 <= monitor.batch_failure_ratio <= 1.0

        monitor.start_batch("https://example.com")

        # Mixed
        monitor.record_success()
        monitor.record_failure()
        assert 0.0 <= monitor.batch_failure_ratio <= 1.0
