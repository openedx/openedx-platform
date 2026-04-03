"""
Tests for Django template context processors.
"""


from django.test import TestCase
from django.test.client import RequestFactory
from django.test.utils import override_settings

from openedx.core.djangoapps.site_configuration.context_processors import configuration_context
from openedx.core.djangoapps.site_configuration.tests.test_util import with_site_configuration

PLATFORM_NAME = 'Test Platform'


@override_settings(PLATFORM_NAME=PLATFORM_NAME)
class ContextProcessorTests(TestCase):
    """
    Tests for the configuration context processor.
    """

    def test_platform_name(self):
        """
        Verify the context includes the platform name.
        """
        request = RequestFactory().get('/')
        context = configuration_context(request)

        assert context['platform_name'] == PLATFORM_NAME

    @with_site_configuration(configuration={"platform_name": "Testing Configuration Platform Name"})
    def test_configuration_platform_name(self):
        """
        Verify the context includes  correct platform name.
        """
        request = RequestFactory().get('/')
        context = configuration_context(request)
        assert context['platform_name'] == 'Testing Configuration Platform Name'
