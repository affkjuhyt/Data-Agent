"""FastAPI application module for sandbox_executor.

The HTTP surface still lives in main.py for compatibility with the existing
unit/integration tests that import and inspect it directly.
"""

from main import app

__all__ = ["app"]
