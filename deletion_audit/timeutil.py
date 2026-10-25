"""Cached timestamp parsing (evidence carries thousands of repeated ISO strings)."""
from __future__ import annotations

from datetime import datetime
from functools import lru_cache

from .model import parse_ts


@lru_cache(maxsize=None)
def T(s: str) -> datetime:
    return parse_ts(s)
