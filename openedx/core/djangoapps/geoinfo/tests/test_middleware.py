"""
Tests for the deprecated CountryMiddleware shim.
"""
import pytest
from django.conf import settings
from django.core.exceptions import MiddlewareNotUsed
from django.core.handlers.base import BaseHandler
from django.test import TestCase, override_settings

from openedx.core.djangoapps.geoinfo.middleware import CountryMiddleware


class CountryMiddlewareShimTests(TestCase):
    """
    CountryMiddleware warns and removes itself from the middleware chain.
    """

    def test_init_warns_and_raises_not_used(self):
        with pytest.warns(DeprecationWarning, match="remove it from MIDDLEWARE"):
            with pytest.raises(MiddlewareNotUsed):
                CountryMiddleware(lambda request: None)

    def test_still_listed_in_middleware_loads(self):
        middleware = [*settings.MIDDLEWARE, "openedx.core.djangoapps.geoinfo.middleware.CountryMiddleware"]
        with override_settings(MIDDLEWARE=middleware):
            with pytest.warns(DeprecationWarning, match="remove it from MIDDLEWARE"):
                BaseHandler().load_middleware()
