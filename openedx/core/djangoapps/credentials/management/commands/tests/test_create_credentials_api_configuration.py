"""
Tests for the create_credentials_api_configuration command
"""

from unittest import TestCase, mock

import pytest
from django.core.management import call_command

from openedx.core.djangoapps.credentials.models import CredentialsApiConfig
from openedx.core.djangolib.testing.utils import skip_unless_lms

from ..create_credentials_api_configuration import Command

COMMAND_MODULE = "openedx.core.djangoapps.credentials.management.commands.create_credentials_api_configuration"


@skip_unless_lms
@pytest.mark.django_db
class CertAllowlistGenerationTests(TestCase):
    """
    Tests for the create_credentials_api_configuration management command
    """

    @mock.patch(COMMAND_MODULE)
    # pylint: disable=unused-argument
    def test_successful_generation(self, mock_command):
        call_command(Command())
        assert len(CredentialsApiConfig.objects.all()) > 0
