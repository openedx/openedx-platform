"""
Helpers for marking xmodule APIs as deprecated.
"""

import sys

if sys.version_info >= (3, 13):
    from warnings import deprecated
else:
    from typing_extensions import deprecated  # TODO: remove once we require Python 3.13+

__all__ = ['deprecated']
