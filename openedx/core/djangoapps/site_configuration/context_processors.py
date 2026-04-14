"""
Django template context processors.
"""


from urllib.parse import quote_plus  # lint-amnesty, pylint: disable=wrong-import-order

from django.conf import settings

from openedx.core.djangoapps.site_configuration import helpers as configuration_helpers


def configuration_context(request):
    """
    Configuration context for django templates.
    """
    return {
        'platform_name': configuration_helpers.get_value('platform_name', settings.PLATFORM_NAME),
        'current_url': quote_plus(request.build_absolute_uri(request.path)),
        'current_site_url': quote_plus(request.build_absolute_uri('/')),
    }
