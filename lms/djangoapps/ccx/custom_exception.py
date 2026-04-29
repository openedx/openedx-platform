"""
This file has custom exceptions for ccx
"""


class CCXUserValidationException(Exception):
    """
    Custom Exception for validation of users in CCX
    """
    pass  # pylint: disable=unnecessary-pass


class CCXLocatorValidationException(Exception):
    """
    Custom Exception to validate CCX locator
    """
    pass  # pylint: disable=unnecessary-pass
