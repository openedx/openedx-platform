"""
Tests for the deprecated CountryMiddleware shim.
"""
import logging

import pytest
from django.conf import settings
from django.core.exceptions import MiddlewareNotUsed
from django.core.handlers.base import BaseHandler
from django.test import override_settings

from openedx.core.djangoapps.geoinfo.middleware import CountryMiddleware

MIDDLEWARE_PATH = "openedx.core.djangoapps.geoinfo.middleware.CountryMiddleware"


def test_init_warns_logs_and_raises_not_used(caplog):
    with caplog.at_level(logging.WARNING, logger="openedx.core.djangoapps.geoinfo.middleware"):
        with pytest.warns(DeprecationWarning, match="remove it from MIDDLEWARE"):
            with pytest.raises(MiddlewareNotUsed):
                CountryMiddleware(lambda request: None)
    assert [record.getMessage() for record in caplog.records] == [
        f"{MIDDLEWARE_PATH} is a deprecated no-op; remove it from MIDDLEWARE."
    ]


@pytest.mark.django_db
def test_still_listed_in_middleware_loads():
    with override_settings(MIDDLEWARE=[*settings.MIDDLEWARE, MIDDLEWARE_PATH]):
        with pytest.warns(DeprecationWarning, match="remove it from MIDDLEWARE"):
            BaseHandler().load_middleware()
