"""
Helper functions for caching course assets.
"""
from django.core.cache import caches  # noqa: I001
from django.core.cache.backends.base import InvalidCacheBackendError
from opaque_keys import InvalidKeyError

from xmodule.contentstore.content import STATIC_CONTENT_VERSION

# See if there's a "course_assets" cache configured, and if not, fallback to the default cache.
CONTENT_CACHE = caches['default']
try:
    CONTENT_CACHE = caches['course_assets']
except InvalidCacheBackendError:
    pass


def set_cached_content(content):
    """
    Stores the given piece of content in the cache, using its location as the key.
    """
    CONTENT_CACHE.set(str(content.location).encode("utf-8"), content, version=STATIC_CONTENT_VERSION)


def get_cached_content(location):
    """
    Retrieves the given piece of content by its location if cached.
    """
    return CONTENT_CACHE.get(str(location).encode("utf-8"), version=STATIC_CONTENT_VERSION)


def del_cached_content(location):
    """
    Delete content for the given location, as well versions of the content without a run.

    It's possible that the content could have been cached without knowing the course_key,
    and so without having the run.
    """
    def location_str(loc):
        """Force the location to a Unicode string."""
        return str(loc).encode("utf-8")

    locations = [location_str(location)]
    try:
        locations.append(location_str(location.replace(run=None)))
    except InvalidKeyError:
        # although deprecated keys allowed run=None, new keys don't if there is no version.
        pass

    CONTENT_CACHE.delete_many(locations, version=STATIC_CONTENT_VERSION)
