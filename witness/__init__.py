"""Witness — local-first observability for browser agents."""

from witness.config import load as load_config
from witness.sdk import instrument
from witness.storage import init_db

__all__ = ["instrument", "init_db", "load_config"]
__version__ = "0.1.0"
