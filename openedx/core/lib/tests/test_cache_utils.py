"""
Tests for cache_utils.py
"""
from time import sleep
from unittest import TestCase
from unittest.mock import Mock

import ddt
from django.core.cache import cache
from django.test.utils import override_settings
from edx_django_utils.cache import RequestCache

from openedx.core.lib.cache_utils import CacheService, request_cached


@ddt.ddt
class TestRequestCachedDecorator(TestCase):
    """
    Test the request_cached decorator.
    """
    def setUp(self):  # lint-amnesty, pylint: disable=super-method-not-called
        RequestCache.clear_all_namespaces()

    def test_request_cached_miss_and_then_hit(self):
        """
        Ensure that after a cache miss, we fill the cache and can hit it.
        """
        to_be_wrapped = Mock()
        to_be_wrapped.return_value = 42
        assert to_be_wrapped.call_count == 0

        def mock_wrapper(*args, **kwargs):
            """Simple wrapper to let us decorate our mock."""
            return to_be_wrapped(*args, **kwargs)

        wrapped = request_cached()(mock_wrapper)  # lint-amnesty, pylint: disable=no-value-for-parameter
        result = wrapped()
        assert result == 42
        assert to_be_wrapped.call_count == 1

        result = wrapped()
        assert result == 42
        assert to_be_wrapped.call_count == 1

    def test_request_cached_with_caches_despite_changing_wrapped_result(self):
        """
        Ensure that after caching a result, we always send it back, even if the underlying result changes.
        """
        to_be_wrapped = Mock()
        to_be_wrapped.side_effect = [1, 2, 3]
        assert to_be_wrapped.call_count == 0

        def mock_wrapper(*args, **kwargs):
            """Simple wrapper to let us decorate our mock."""
            return to_be_wrapped(*args, **kwargs)

        wrapped = request_cached()(mock_wrapper)  # lint-amnesty, pylint: disable=no-value-for-parameter
        result = wrapped()
        assert result == 1
        assert to_be_wrapped.call_count == 1

        result = wrapped()
        assert result == 1
        assert to_be_wrapped.call_count == 1

        direct_result = mock_wrapper()
        assert direct_result == 2
        assert to_be_wrapped.call_count == 2

        result = wrapped()
        assert result == 1
        assert to_be_wrapped.call_count == 2

        direct_result = mock_wrapper()
        assert direct_result == 3
        assert to_be_wrapped.call_count == 3

    def test_request_cached_with_changing_args(self):
        """
        Ensure that calling a decorated function with different positional arguments
        will not use a cached value invoked by a previous call with different arguments.
        """
        to_be_wrapped = Mock()
        to_be_wrapped.side_effect = [1, 2, 3, 4, 5, 6]
        assert to_be_wrapped.call_count == 0

        def mock_wrapper(*args, **kwargs):
            """Simple wrapper to let us decorate our mock."""
            return to_be_wrapped(*args, **kwargs)

        wrapped = request_cached()(mock_wrapper)  # lint-amnesty, pylint: disable=no-value-for-parameter

        # This will be a miss, and make an underlying call.
        result = wrapped(1)
        assert result == 1
        assert to_be_wrapped.call_count == 1

        # This will be a miss, and make an underlying call.
        result = wrapped(2)
        assert result == 2
        assert to_be_wrapped.call_count == 2

        # This is bypass of the decorator.
        direct_result = mock_wrapper(3)
        assert direct_result == 3
        assert to_be_wrapped.call_count == 3

        # These will be hits, and not make an underlying call.
        result = wrapped(1)
        assert result == 1
        assert to_be_wrapped.call_count == 3

        result = wrapped(2)
        assert result == 2
        assert to_be_wrapped.call_count == 3

    def test_request_cached_with_changing_kwargs(self):
        """
        Ensure that calling a decorated function with different keyword arguments
        will not use a cached value invoked by a previous call with different arguments.
        """
        to_be_wrapped = Mock()
        to_be_wrapped.side_effect = [1, 2, 3, 4, 5, 6]
        assert to_be_wrapped.call_count == 0

        def mock_wrapper(*args, **kwargs):
            """Simple wrapper to let us decorate our mock."""
            return to_be_wrapped(*args, **kwargs)

        wrapped = request_cached()(mock_wrapper)  # lint-amnesty, pylint: disable=no-value-for-parameter

        # This will be a miss, and make an underlying call.
        result = wrapped(1, foo=1)
        assert result == 1
        assert to_be_wrapped.call_count == 1

        # This will be a miss, and make an underlying call.
        result = wrapped(2, foo=2)
        assert result == 2
        assert to_be_wrapped.call_count == 2

        # This is bypass of the decorator.
        direct_result = mock_wrapper(3, foo=3)
        assert direct_result == 3
        assert to_be_wrapped.call_count == 3

        # These will be hits, and not make an underlying call.
        result = wrapped(1, foo=1)
        assert result == 1
        assert to_be_wrapped.call_count == 3

        result = wrapped(2, foo=2)
        assert result == 2
        assert to_be_wrapped.call_count == 3

        # Since we're changing foo, this will be a miss.
        result = wrapped(2, foo=5)
        assert result == 4
        assert to_be_wrapped.call_count == 4

        # Since we're adding bar, this will be a miss.
        result = wrapped(2, foo=1, bar=2)
        assert result == 5
        assert to_be_wrapped.call_count == 5

        # Should be a hit, even when kwargs are in a different order
        result = wrapped(2, bar=2, foo=1)
        assert result == 5
        assert to_be_wrapped.call_count == 5

    def test_request_cached_mixed_unicode_str_args(self):
        """
        Ensure that request_cached can work with mixed str and Unicode parameters.
        """
        def dummy_function(arg1, arg2):
            """
            A dummy function that expects an str and unicode arguments.
            """
            assert isinstance(arg1, str), 'First parameter has to be of type `str`'
            assert isinstance(arg2, str), 'Second parameter has to be of type `unicode`'
            return True

        assert dummy_function('Hello', 'World'), 'Should be callable with ASCII chars'
        assert dummy_function('H∂llå', 'Wørld'), 'Should be callable with non-ASCII chars'

        wrapped = request_cached()(dummy_function)  # lint-amnesty, pylint: disable=no-value-for-parameter

        assert wrapped('Hello', 'World'), 'Wrapper should handle ASCII only chars'
        assert wrapped('H∂llå', 'Wørld'), 'Wrapper should handle non-ASCII chars'

    def test_request_cached_with_none_result(self):
        """
        Ensure that calling a decorated function that returns None
        properly caches the result and doesn't recall the underlying
        function.
        """
        to_be_wrapped = Mock()
        to_be_wrapped.side_effect = [None, None, None, 1, 1]
        assert to_be_wrapped.call_count == 0

        def mock_wrapper(*args, **kwargs):
            """Simple wrapper to let us decorate our mock."""
            return to_be_wrapped(*args, **kwargs)

        wrapped = request_cached()(mock_wrapper)  # lint-amnesty, pylint: disable=no-value-for-parameter

        # This will be a miss, and make an underlying call.
        result = wrapped(1)
        assert result is None
        assert to_be_wrapped.call_count == 1

        # This will be a miss, and make an underlying call.
        result = wrapped(2)
        assert result is None
        assert to_be_wrapped.call_count == 2

        # This is bypass of the decorator.
        direct_result = mock_wrapper(3)
        assert direct_result is None
        assert to_be_wrapped.call_count == 3

        # These will be hits, and not make an underlying call.
        result = wrapped(1)
        assert result is None
        assert to_be_wrapped.call_count == 3

        result = wrapped(2)
        assert result is None
        assert to_be_wrapped.call_count == 3

    def test_request_cached_with_request_cache_getter(self):
        """
        Ensure that calling a decorated function uses
        request_cache_getter if supplied.
        """
        to_be_wrapped = Mock()
        to_be_wrapped.side_effect = [1, 2, 3]
        assert to_be_wrapped.call_count == 0

        def mock_wrapper(*args, **kwargs):
            """Simple wrapper to let us decorate our mock."""
            return to_be_wrapped(*args, **kwargs)

        request_cache_getter = lambda args, kwargs: RequestCache('test')
        wrapped = request_cached(request_cache_getter=request_cache_getter)(mock_wrapper)  # lint-amnesty, pylint: disable=no-value-for-parameter

        # This will be a miss, and make an underlying call.
        result = wrapped(1)
        assert result == 1
        assert to_be_wrapped.call_count == 1

        # This will be a miss, and make an underlying call.
        result = wrapped(2)
        assert result == 2
        assert to_be_wrapped.call_count == 2

        # These will be a hits, and not make an underlying call.
        result = wrapped(1)
        assert result == 1
        assert to_be_wrapped.call_count == 2

        # Ensure the appropriate request cache was used
        assert not RequestCache().data
        assert RequestCache('test').data

    def test_request_cached_with_arg_map_function(self):
        """
        Ensure that calling a decorated function uses
        arg_map_function to determined the cache key.
        """
        to_be_wrapped = Mock()
        to_be_wrapped.side_effect = [1, 2, 3]
        assert to_be_wrapped.call_count == 0

        def mock_wrapper(*args, **kwargs):
            """Simple wrapper to let us decorate our mock."""
            return to_be_wrapped(*args, **kwargs)

        arg_map_function = lambda arg: str(arg == 1)
        wrapped = request_cached(arg_map_function=arg_map_function)(mock_wrapper)  # lint-amnesty, pylint: disable=no-value-for-parameter

        # This will be a miss, and make an underlying call.
        result = wrapped(1)
        assert result == 1
        assert to_be_wrapped.call_count == 1

        # This will be a miss, and make an underlying call.
        result = wrapped(2)
        assert result == 2
        assert to_be_wrapped.call_count == 2

        # These will be a hits, and not make an underlying call.
        result = wrapped(1)
        assert result == 1
        assert to_be_wrapped.call_count == 2

        result = wrapped(3)
        assert result == 2
        assert to_be_wrapped.call_count == 2


class CacheServiceTest(TestCase):
    """
    Test CacheService methods.
    """
    @override_settings(CACHES={
        'default': {
            'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
        }
    })
    def test_cache(self):
        '''
        Ensure the default cache works as expected.
        '''
        cache_service = CacheService(cache)
        key = 'my_key'
        value = 'some random value'
        timeout = 1
        cache_service.set(key, value, timeout=timeout)
        assert cache_service.get(key) == value
        sleep(timeout)
        assert cache_service.get(key) is None
