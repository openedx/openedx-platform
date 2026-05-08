"""
An :class:`~xblock.runtime.KeyValueStore` that stores data in the django session
"""


from xblock.runtime import KeyValueStore


def stringify(key):
    return repr(tuple(key))


class SessionKeyValueStore(KeyValueStore):  # pylint: disable=missing-class-docstring
    def __init__(self, request):  # pylint: disable=super-init-not-called
        self._session = request.session

    def get(self, key):
        return self._session[stringify(key)]

    def set(self, key, value):
        self._session[stringify(key)] = value

    def delete(self, key):
        del self._session[stringify(key)]

    def has(self, key):
        return stringify(key) in self._session
