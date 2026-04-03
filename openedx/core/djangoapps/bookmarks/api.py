"""
Bookmarks Python public API.
"""
# pylint: disable=unused-import

from .api_impl import (
    BookmarksLimitReachedError,
    can_create_more,
    create_bookmark,
    delete_bookmark,
    delete_bookmarks,
    get_bookmark,
    get_bookmarks,
)
from .services import BookmarksService
