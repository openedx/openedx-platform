"""
Commerce-related models.
"""


from config_models.models import ConfigurationModel
from django.db import models
from django.utils.translation import gettext_lazy as _


class CommerceConfiguration(ConfigurationModel):
    """
    Commerce configuration

    .. no_pii:
    """

    class Meta:
        app_label = "commerce"

    API_NAME = 'commerce'
    CACHE_KEY = 'commerce.api.data'
    DEFAULT_RECEIPT_PAGE_URL = '/checkout/receipt/?order_number='
    DEFAULT_ORDER_DASHBOARD_URL = '/dashboard/orders/'

    checkout_on_ecommerce_service = models.BooleanField(
        default=False,
        help_text=_('Use the checkout page hosted by the E-Commerce service.')
    )

    basket_checkout_page = models.CharField(
        max_length=255,
        default='/basket/add/',
        help_text=_('Path to course(s) checkout page hosted by the E-Commerce service.')
    )
    cache_ttl = models.PositiveIntegerField(
        verbose_name=_('Cache Time To Live'),
        default=0,
        help_text=_(
            'Specified in seconds. Enable caching by setting this to a value greater than 0.'
        )
    )
    # receipt_page no longer used but remains in the model until we can purge old data.
    # removing this will casue 500 errors when trying to access the Django admin.
    receipt_page = models.CharField(
        max_length=255,
        default=DEFAULT_RECEIPT_PAGE_URL,
        help_text=_('Path to order receipt page.')
    )
    enable_automatic_refund_approval = models.BooleanField(
        default=True,
        help_text=_('Automatically approve valid refund requests, without manual processing')
    )

    def __str__(self):
        return "Commerce configuration"

    @property
    def is_cache_enabled(self):
        """Whether responses from the Ecommerce API will be cached."""
        return self.cache_ttl > 0
