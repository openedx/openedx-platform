"""
An :class:`~xblock.runtime.KeyValueStore` that stores data in the django cache

This is used for low-priority ephemeral student state data:
* Anonymous users browsing and previewing content
* Studio authors testing out XBlocks

We could also store this data in django sessions, but its a bit tricky to access
session data during any requests which don't have any cookies or other normal
authentication mechanisms (like XBlock handler calls from within XBlock <iframe>
sandboxes). And keeping this storage completely separate from django session
data and registered user XBlock state reduces the potential for security
problems. We expect the data in this store to be low-value and free of
personally identifiable information (PII) so if some security bug results in one
user accessing a different user's entries in this particular store, it's not a
big deal.
"""


from django.conf import settings
from django.core.cache import caches
from xblock.runtime import KeyValueStore

FIELD_DATA_TIMEOUT = None  # keep in cache indefinitely, until cache needs pruning


class NotFound:
    """
    This class is a unique value that can be stored in a cache to indicate "not found"
    """
    # Store the class itself, not an instance of it.


class EphemeralKeyValueStore(KeyValueStore):
    """
    An XBlock field data key-value store that is backed by the django cache
    """
    def _wrap_key(self, key):
        """
        Expand the given XBlock key tuple to a format we can use as a key.
        """
        return f"ephemeral-xblock:{repr(tuple(key))}"

    @property
    def _cache(self):
        return caches[settings.XBLOCK_RUNTIME_V2_EPHEMERAL_DATA_CACHE]

    def get(self, key):
        value = self._cache.get(self._wrap_key(key), default=NotFound)
        if value is NotFound:
            raise KeyError  # Normal, this is how we indicate a value is not found
        return value

    def set(self, key, value):
        self._cache.set(self._wrap_key(key), value, timeout=FIELD_DATA_TIMEOUT)

    def delete(self, key):
        self._cache.delete(self._wrap_key(key))

    def has(self, key):
        return self._cache.get(self._wrap_key(key), default=NotFound) is not NotFound
