"""
Bookmarks service.
"""


import logging

from django.core.exceptions import ObjectDoesNotExist
from edx_django_utils.cache import DEFAULT_REQUEST_CACHE

from xmodule.modulestore.django import modulestore
from xmodule.modulestore.exceptions import ItemNotFoundError

from . import DEFAULT_FIELDS
from . import api_impl as api

log = logging.getLogger(__name__)

CACHE_KEY_TEMPLATE = "bookmarks.list.{}.{}"


class BookmarksService:
    """
    A service that provides access to the bookmarks API.

    When bookmarks() or is_bookmarked() is called for the
    first time, the service fetches and caches all the bookmarks
    of the user for the relevant course. So multiple calls to
    get bookmark status during a request (for, example when
    rendering courseware and getting bookmarks status for search
    results) will not cause repeated queries to the database.
    """

    def __init__(self, user, **kwargs):
        super().__init__(**kwargs)
        self._user = user

    def _bookmarks_cache(self, course_key, fetch=False):
        """
        Return the user's bookmarks cache for a particular course.

        Arguments:
            course_key (CourseKey): course_key of the course whose bookmarks cache should be returned.
            fetch (Bool): if the bookmarks should be fetched and cached if they already aren't.
        """
        store = modulestore()
        course_key = store.fill_in_run(course_key)
        if course_key.run is None:
            return []
        cache_key = CACHE_KEY_TEMPLATE.format(self._user.id, course_key)

        bookmarks_cache = DEFAULT_REQUEST_CACHE.data.get(cache_key, None)
        if bookmarks_cache is None and fetch is True:
            bookmarks_cache = api.get_bookmarks(
                self._user, course_key=course_key, fields=DEFAULT_FIELDS
            )
            DEFAULT_REQUEST_CACHE.data[cache_key] = bookmarks_cache

        return bookmarks_cache

    def bookmarks(self, course_key):
        """
        Return a list of bookmarks for the course for the current user.

        Arguments:
            course_key: CourseKey of the course for which to retrieve the user's bookmarks for.

        Returns:
            list of dict:
        """
        return self._bookmarks_cache(course_key, fetch=True)

    def is_bookmarked(self, usage_key):
        """
        Return whether the block has been bookmarked by the user.

        Arguments:
            usage_key: UsageKey of the block.

        Returns:
            Bool
        """
        usage_id = str(usage_key)
        bookmarks_cache = self._bookmarks_cache(usage_key.course_key, fetch=True)
        for bookmark in bookmarks_cache:
            if bookmark['usage_id'] == usage_id:
                return True

        return False

    def set_bookmarked(self, usage_key):
        """
        Adds a bookmark for the block.

        Arguments:
            usage_key: UsageKey of the block.

        Returns:
            Bool indicating whether the bookmark was added.
        """
        try:
            bookmark = api.create_bookmark(user=self._user, usage_key=usage_key)
        except ItemNotFoundError:
            log.error('Block with usage_id: %s not found.', usage_key)
            return False

        bookmarks_cache = self._bookmarks_cache(usage_key.course_key)
        if bookmarks_cache is not None:
            bookmarks_cache.append(bookmark)

        return True

    def unset_bookmarked(self, usage_key):
        """
        Removes the bookmark for the block.

        Arguments:
            usage_key: UsageKey of the block.

        Returns:
            Bool indicating whether the bookmark was removed.
        """
        try:
            api.delete_bookmark(self._user, usage_key=usage_key)
        except ObjectDoesNotExist:
            log.error('Bookmark with usage_id: %s does not exist.', usage_key)
            return False

        bookmarks_cache = self._bookmarks_cache(usage_key.course_key)
        if bookmarks_cache is not None:
            deleted_bookmark_index = None
            usage_id = str(usage_key)
            for index, bookmark in enumerate(bookmarks_cache):
                if bookmark['usage_id'] == usage_id:
                    deleted_bookmark_index = index
                    break
            if deleted_bookmark_index is not None:
                bookmarks_cache.pop(deleted_bookmark_index)

        return True
