# =============================================================================
# MarketPulse-Pro Makefile
# Standardized commands for development, testing, and execution
# =============================================================================

.PHONY: help install install-dev setup run test test-cov lint format typecheck clean clean-all

# Default Python interpreter
PYTHON := python
PIP := pip

# Directories
SRC_DIR := src
TEST_DIR := tests
CONFIG_DIR := config
LOG_DIR := logs
OUTPUT_DIR := output

# =============================================================================
# Help
# =============================================================================
help:
	@echo "MarketPulse-Pro - Enterprise Web Scraping Pipeline"
	@echo ""
	@echo "Usage: make [target]"
	@echo ""
	@echo "Setup & Installation:"
	@echo "  install       Install production dependencies"
	@echo "  install-dev   Install development dependencies"
	@echo "  setup         Full setup (install-dev + playwright browsers)"
	@echo ""
	@echo "Execution:"
	@echo "  run           Execute the scraping pipeline"
	@echo ""
	@echo "Quality Assurance:"
	@echo "  test          Run test suite"
	@echo "  test-cov      Run tests with coverage report"
	@echo "  lint          Run linter (ruff)"
	@echo "  format        Auto-format code (ruff)"
	@echo "  typecheck     Run type checker (mypy)"
	@echo ""
	@echo "Maintenance:"
	@echo "  clean         Remove cached files and logs"
	@echo "  clean-all     Remove all generated files including outputs"

# =============================================================================
# Setup & Installation
# =============================================================================
install:
	$(PIP) install -e .

install-dev:
	$(PIP) install -e ".[dev]"

setup: install-dev
	$(PYTHON) -m playwright install chromium
	@echo "Setup complete. Copy .env.example to .env and configure."

# =============================================================================
# Execution
# =============================================================================
run:
	$(PYTHON) main.py

# =============================================================================
# Quality Assurance
# =============================================================================
test:
	$(PYTHON) -m pytest $(TEST_DIR) -v

test-cov:
	$(PYTHON) -m pytest $(TEST_DIR) --cov=$(SRC_DIR) --cov-report=html --cov-report=term-missing

lint:
	$(PYTHON) -m ruff check $(SRC_DIR) $(TEST_DIR) $(CONFIG_DIR) main.py

format:
	$(PYTHON) -m ruff check --fix $(SRC_DIR) $(TEST_DIR) $(CONFIG_DIR) main.py
	$(PYTHON) -m ruff format $(SRC_DIR) $(TEST_DIR) $(CONFIG_DIR) main.py

typecheck:
	$(PYTHON) -m mypy $(SRC_DIR) $(CONFIG_DIR) main.py

# =============================================================================
# Maintenance
# =============================================================================
clean:
	@echo "Cleaning cached files..."
	@if exist __pycache__ rd /s /q __pycache__
	@if exist .pytest_cache rd /s /q .pytest_cache
	@if exist .mypy_cache rd /s /q .mypy_cache
	@if exist .ruff_cache rd /s /q .ruff_cache
	@if exist $(LOG_DIR) rd /s /q $(LOG_DIR)
	@if exist htmlcov rd /s /q htmlcov
	@if exist .coverage del /f .coverage
	@for /d /r . %%d in (__pycache__) do @if exist "%%d" rd /s /q "%%d"
	@echo "Clean complete."

clean-all: clean
	@echo "Cleaning output files..."
	@if exist $(OUTPUT_DIR) rd /s /q $(OUTPUT_DIR)
	@if exist storage_state.json del /f storage_state.json
	@echo "Full clean complete."
