"""
Simple data structures used for XBlock serialization
"""
from __future__ import annotations

from typing import NamedTuple


class StaticFile(NamedTuple):
    """ A static file required by an XBlock """
    name: str
    url: str | None
    data: bytes | None
