"""
Tests for courseware middleware
"""


from django.http import Http404
from django.test.client import RequestFactory

from lms.djangoapps.courseware.exceptions import Redirect
from lms.djangoapps.courseware.middleware import RedirectMiddleware
from xmodule.modulestore.tests.django_utils import (
    SharedModuleStoreTestCase,  # lint-amnesty, pylint: disable=wrong-import-order
)
from xmodule.modulestore.tests.factories import CourseFactory  # lint-amnesty, pylint: disable=wrong-import-order


class CoursewareMiddlewareTestCase(SharedModuleStoreTestCase):
    """Tests that courseware middleware is correctly redirected"""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course = CourseFactory.create()

    @staticmethod
    def get_headers(cache_response):
        """
        Django 3.2 has no ._headers
        See https://docs.djangoproject.com/en/3.2/releases/3.2/#requests-and-responses
        """
        if hasattr(cache_response, '_headers'):
            headers = cache_response._headers.copy()  # pylint: disable=protected-access
        else:
            headers = {k.lower(): (k, v) for k, v in cache_response.items()}

        return headers

    def test_process_404(self):
        """A 404 should not trigger anything"""
        request = RequestFactory().get("dummy_url")
        response = RedirectMiddleware(get_response=lambda request: None).process_exception(
            request, Http404()
        )
        assert response is None

    def test_redirect_exceptions(self):
        """
        Unit tests for handling of Redirect exceptions.
        """
        request = RequestFactory().get("dummy_url")
        test_url = '/test_url'
        exception = Redirect(test_url)
        response = RedirectMiddleware(get_response=lambda request: None).process_exception(
            request, exception
        )
        assert response.status_code == 302
        headers = self.get_headers(response)
        target_url = headers['location'][1]
        assert target_url.endswith(test_url)
