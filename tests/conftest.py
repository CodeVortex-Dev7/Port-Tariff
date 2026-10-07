"""Pytest configuration: quiet, human-readable structlog for tests.

Sets env vars before any application module is imported, then configures
structlog at WARNING so pytest output stays clean while still surfacing
real problems.
"""

import os

os.environ.setdefault("LOG_LEVEL", "WARNING")
os.environ.setdefault("LOG_FORMAT", "console")

from port_tariff.logging_config import configure_logging  # noqa: E402

configure_logging(level="WARNING", fmt="console", debug=True)
