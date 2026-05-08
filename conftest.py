"""  # pylint: disable=django-not-configured
Default unit test configuration and fixtures.
"""

from unittest import TestCase

import pytest  # noqa: F401

from cms.conftest import _django_clear_site_cache, pytest_configure  # pylint: disable=unused-import  # noqa: F401

# Import hooks and fixture overrides from the cms package to
# avoid duplicating the implementation



# When using self.assertEquals, diffs are truncated. We don't want that, always
# show the whole diff.
TestCase.maxDiff = None
