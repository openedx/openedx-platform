"""
Models for credentials support for the LMS and Studio.
"""


from urllib.parse import urljoin  # pylint: disable=import-error

from config_models.models import ConfigurationModel
from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from openedx.core.djangoapps.site_configuration import helpers

from .helpers import is_learner_records_enabled

API_VERSION = 'v2'


class CredentialsApiConfig(ConfigurationModel):
    """
    Manages configuration for connecting to the Credential service and using its
    API.

    .. no_pii:
    """

    class Meta:
        app_label = 'credentials'

    OAUTH2_CLIENT_NAME = 'credentials'
    API_NAME = 'credentials'
    CACHE_KEY = 'credentials.api.data'

    internal_service_url = models.URLField(
        verbose_name=_('Internal Service URL'),
        help_text='DEPRECATED: Use the setting CREDENTIALS_INTERNAL_SERVICE_URL.'
    )
    public_service_url = models.URLField(
        verbose_name=_('Public Service URL'),
        help_text='DEPRECATED: Use the setting CREDENTIALS_PUBLIC_SERVICE_URL.'
    )

    enable_learner_issuance = models.BooleanField(
        verbose_name=_('Enable Learner Issuance'),
        default=False,
        help_text=_(
            'Enable issuance of credentials via Credential Service.'
        )
    )
    enable_studio_authoring = models.BooleanField(
        verbose_name=_('Enable Authoring of Credential in Studio'),
        default=False,
        help_text=_(
            'Enable authoring of Credential Service credentials in Studio.'
        )
    )
    cache_ttl = models.PositiveIntegerField(
        verbose_name=_('Cache Time To Live'),
        default=0,
        help_text=_(
            'Specified in seconds. Enable caching by setting this to a value greater than 0.'
        )
    )

    def __str__(self):
        return self.public_api_url

    @property
    def internal_api_url(self):
        """
        Internally-accessible API URL root, looked up based on the current request.
        """
        root = helpers.get_value('CREDENTIALS_INTERNAL_SERVICE_URL', settings.CREDENTIALS_INTERNAL_SERVICE_URL)
        return urljoin(root, f'/api/{API_VERSION}/')

    @staticmethod
    def get_internal_api_url_for_org(org):
        """
        Internally-accessible API URL root, looked up by org rather than the current request.
        """
        root = helpers.get_value_for_org(org, 'CREDENTIALS_INTERNAL_SERVICE_URL',
                                         settings.CREDENTIALS_INTERNAL_SERVICE_URL)
        return urljoin(root, f'/api/{API_VERSION}/')

    @property
    def public_api_url(self):
        """
        Publicly-accessible API URL root.
        """
        root = helpers.get_value('CREDENTIALS_PUBLIC_SERVICE_URL', settings.CREDENTIALS_PUBLIC_SERVICE_URL)
        return urljoin(root, f'/api/{API_VERSION}/')

    @property
    def public_records_url(self):
        """
        Publicly-accessible Records URL root.
        """
        # Not every site wants the Learner Records feature, so we allow opting out.
        if not is_learner_records_enabled():
            return None
        root = helpers.get_value('CREDENTIALS_PUBLIC_SERVICE_URL', settings.CREDENTIALS_PUBLIC_SERVICE_URL)
        return urljoin(root, '/records/')

    @property
    def is_learner_issuance_enabled(self):
        """
        Returns boolean indicating if credentials should be issued.
        """
        return self.enabled and self.enable_learner_issuance

    @property
    def is_cache_enabled(self):
        """Whether responses from the Credentials API will be cached."""
        return self.cache_ttl > 0


class NotifyCredentialsConfig(ConfigurationModel):
    """
    Manages configuration for a run of the notify_credentials management command.

    .. no_pii:
    """

    class Meta:
        app_label = 'credentials'
        verbose_name = 'notify_credentials argument'

    arguments = models.TextField(
        blank=True,
        help_text='Useful for manually running a Jenkins job. Specify like "--start-date=2018 --courses A B".',
        default='',
    )

    def __str__(self):
        return str(self.arguments)
