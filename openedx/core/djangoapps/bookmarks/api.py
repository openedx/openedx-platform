"""
Bookmarks Python public API.
"""
# pylint: disable=unused-import

from .api_impl import (
    BookmarksLimitReachedError,  # noqa: F401
    can_create_more,  # noqa: F401
    create_bookmark,  # noqa: F401
    delete_bookmark,  # noqa: F401
    delete_bookmarks,  # noqa: F401
    get_bookmark,  # noqa: F401
    get_bookmarks,  # noqa: F401
)
from .services import BookmarksService  # noqa: F401
