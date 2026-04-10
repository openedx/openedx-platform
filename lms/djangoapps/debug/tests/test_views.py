"""
Regression tests for the debug app URL gating.

The ``/debug/run_python`` and ``/debug/show_parameters`` endpoints are
development aids and must never be reachable from a production image.
See the security bug report for full context.
"""
from django.test import TestCase

from common.djangoapps.student.tests.factories import UserFactory


class DebugEndpointProductionGatingTests(TestCase):
    """
    Security regression tests: debug endpoints must be unreachable in a
    production URL configuration (``settings.DEBUG`` is False) and must
    reject non-staff users even when exposed in dev.
    """

    def setUp(self):
        super().setUp()
        self.staff = UserFactory(is_staff=True)
        self.learner = UserFactory(is_staff=False)

    def test_sec_debug_endpoint_show_parameters_returns_404_in_default_env_regression(self):
        """
        URL-resolution test: in the default (production-like) test env
        ``/debug/show_parameters`` must not be registered at all. Using
        the URL resolver directly instead of the test client avoids the
        edx-platform middleware chain, which can short-circuit before
        the 404 handler runs.
        """
        from django.urls import Resolver404, resolve
        try:
            resolve('/debug/show_parameters')
        except Resolver404:
            return
        raise AssertionError(
            '/debug/show_parameters must not be registered when DEBUG=False'
        )

    def test_sec_debug_endpoint_run_python_returns_404_in_default_env_regression(self):
        """
        URL-resolution test: ``/debug/run_python`` must not be
        registered in the default environment (both because ``DEBUG``
        is False and because the ``ENABLE_DEBUG_RUN_PYTHON`` feature
        flag defaults to False).
        """
        from django.urls import Resolver404, resolve
        try:
            resolve('/debug/run_python')
        except Resolver404:
            return
        raise AssertionError(
            '/debug/run_python must not be registered when DEBUG=False '
            'and ENABLE_DEBUG_RUN_PYTHON is off'
        )

    def test_sec_debug_endpoint_show_parameters_denies_learner_regression(self):
        """
        Unit: even when the view is reached (e.g. via a direct call in
        dev), it must refuse non-staff users. Defence in depth against
        future URL-registration slips.
        """
        from django.http import Http404
        from django.test import RequestFactory

        from lms.djangoapps.debug.views import show_parameters

        request = RequestFactory().get('/debug/show_parameters')
        request.user = self.learner
        try:
            show_parameters(request)
        except Http404:
            return
        raise AssertionError(
            'show_parameters must raise Http404 for non-staff users'
        )

    def test_sec_debug_endpoint_show_parameters_allows_staff_regression(self):
        """
        Unit: the view still works for staff users when reached directly.
        """
        from django.test import RequestFactory

        from lms.djangoapps.debug.views import show_parameters

        request = RequestFactory().get('/debug/show_parameters?x=1')
        request.user = self.staff
        response = show_parameters(request)
        assert response.status_code == 200
        assert b'GET' in response.content
