"""Configuration module for MarketPulse-Pro.

This module provides centralized configuration management using pydantic-settings,
ensuring 12-factor app compliance and strict validation of all environment variables.
"""

from config.settings import GlobalConfig, get_config

__all__ = ["GlobalConfig", "get_config"]
