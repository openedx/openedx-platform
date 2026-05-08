"""
Custom exceptions raised by Discussion API.
"""


class TeamDiscussionHiddenFromUserException(BaseException):
    """
    This is the exception raised when a user is not
    permitted to view the discussion thread
    """
    pass  # pylint: disable=unnecessary-pass
