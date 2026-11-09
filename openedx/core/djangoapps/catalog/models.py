"""Models governing integration with the catalog service."""
from config_models.models import ConfigurationModel
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import models
from django.utils.translation import gettext_lazy as _

from openedx.core.djangoapps.site_configuration import helpers

User = get_user_model()


class CatalogIntegration(ConfigurationModel):
    """
    Manages configuration for connecting to the catalog service and using its API.

    .. no_pii:
    """
    API_NAME = 'catalog'
    CACHE_KEY = 'catalog.api.data'

    # TODO Replace all usages of this field with a call to get_internal_api_url().
    internal_api_url = models.URLField(
        verbose_name=_('Internal API URL'),
        help_text=_(
            'DEPRECATED: Use the setting COURSE_CATALOG_API_URL.'
        )
    )

    cache_ttl = models.PositiveIntegerField(
        verbose_name=_('Cache Time To Live'),
        default=0,
        help_text=_(
            'Specified in seconds. Enable caching of API responses by setting this to a value greater than 0.'
        )
    )

    long_term_cache_ttl = models.PositiveIntegerField(
        verbose_name=_('Long Term Cache Time To Live'),
        default=86400,
        help_text=_(
            'Specified in seconds (defaults to 86400s, 24hr). In some cases the cache does needs to be refreshed '
            'less frequently. Enable long term caching of API responses by setting this to a value greater than 0.'
        )
    )

    # pylint: disable-next=pii-invalid-no-pii-annotation  # field does not store user PII data, safe under OEP-30
    service_username = models.CharField(
        max_length=100,
        default='lms_catalog_service_user',
        null=False,
        blank=False,
        help_text=_(
            'Username created for Course Catalog Integration, e.g. lms_catalog_service_user.'
        )
    )

    page_size = models.PositiveIntegerField(
        verbose_name=_('Page Size'),
        default=100,
        help_text=_(
            'Maximum number of records in paginated response of a single request to catalog service.'
        )
    )

    @property
    def is_cache_enabled(self):
        """Whether responses from the catalog API will be cached."""
        return self.cache_ttl > 0

    def get_internal_api_url(self):
        """ Returns the internal Catalog API URL associated with the request's site. """
        return helpers.get_value('COURSE_CATALOG_API_URL', settings.COURSE_CATALOG_API_URL)

    def get_service_user(self):
        return User.objects.get(username=self.service_username)
