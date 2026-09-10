"""
ADR 0029 - Standardized error-response exception handler and helpers.

The implementation lives in ``edx_rest_framework_extensions.errors``; this
module re-exports it for existing import sites. New code should import from
``edx_rest_framework_extensions.errors`` directly.
"""
from edx_rest_framework_extensions.errors import Conflict, standardized_error_exception_handler

__all__ = ["Conflict", "standardized_error_exception_handler"]
