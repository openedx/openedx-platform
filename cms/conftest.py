# ruff: noqa: I001
"""
Studio unit test configuration and fixtures.

This module needs to exist because the pytest.ini in the cms package stops
pytest from looking for the conftest.py module in the parent directory when
only running cms tests.
"""

import logging

import pytest

from openedx.core.pytest_hooks import DeferPlugin

# Patch the xml libs before anything else.
from openedx.core.lib.safe_lxml import defuse_xml_libs  # must patch xml libs before other imports execute
defuse_xml_libs()


def pytest_configure(config):
    """
    Do core setup operations from manage.py before collecting tests.
    """
    if config.pluginmanager.hasplugin("pytest_jsonreport") or config.pluginmanager.hasplugin("json-report"):
        config.pluginmanager.register(DeferPlugin())
    else:
        logging.info("pytest did not register json_report correctly")


@pytest.fixture(autouse=True, scope='function')  # noqa: PT003
def _django_clear_site_cache():
    """
    pytest-django uses this fixture to automatically clear the Site object
    cache by replacing it with a new dictionary.  edx-django-sites-extensions
    grabs the cache dictionary at startup, and uses that one for all lookups
    from then on.  Our CacheIsolationMixin class tries to clear the cache by
    grabbing the current dictionary from the site models module and clearing
    it.  Long story short: if you use this all together, neither cache
    clearing mechanism actually works.  So override this fixture to not mess
    with what has been working for us so far.
    """
    pass  # pylint: disable=unnecessary-pass
