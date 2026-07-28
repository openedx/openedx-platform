"""
Django management command used to enable Credentials functionality in this Open edX instance.
"""
from django.core.management.base import BaseCommand, CommandError

from openedx.core.djangoapps.credentials.models import CredentialsApiConfig


class Command(BaseCommand):
    """
    Creates a CredentialsApiConfig in the LMS to enable Credentials functionality. This command was primarily used as
    part of an optional devstack provisioning step for developers who work with the Credentials IDA.
    """
    # pylint: disable=unused-argument
    def handle(self, *args, **kwargs):
        try:
            CredentialsApiConfig.objects.create(
                enabled=True,
                enable_learner_issuance=True,
            )
        except Exception as e:
            raise CommandError from e
