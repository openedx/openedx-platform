# pylint: disable=no-member, missing-docstring


from unittest import TestCase

from celery import shared_task
from django.test.utils import override_settings
from edx_django_utils.cache import RequestCache
from pytest import mark


@mark.django_db
class TestClearRequestCache(TestCase):
    """
    Tests _clear_request_cache is called after celery task is run.
    """
    def _get_cache(self):
        return RequestCache("TestClearRequestCache")

    @shared_task
    def _dummy_task(self):
        """ A task that adds stuff to the request cache. """
        self._get_cache().set("cache_key", "blah blah")

    @override_settings(CLEAR_REQUEST_CACHE_ON_TASK_COMPLETION=True)
    def test_clear_cache_celery(self):
        self._dummy_task.apply(args=(self,)).get()
        assert not self._get_cache().get_cached_response('cache_key').is_found
