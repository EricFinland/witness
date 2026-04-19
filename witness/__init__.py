"""Witness — local-first observability for browser agents."""

from witness.sdk import instrument
from witness.storage import init_db

__all__ = ["instrument", "init_db"]
__version__ = "0.0.1"
