"""
Context dictionary for templates that use the ace_common base template.
"""

from django.conf import settings
from django.urls import NoReverseMatch, reverse

from common.djangoapps.edxmako.shortcuts import marketing_link
from lms.djangoapps.branding.api import get_logo_url_for_email
from openedx.core.djangoapps.theming.helpers import get_config_value_from_site_or_settings


def get_base_template_context(site):
    """
    Dict with entries needed for all templates that use the base template.
    """
    # When on LMS and a dashboard is available, use that as the dashboard url.
    # Otherwise, use the home url instead.
    try:
        dashboard_url = reverse('dashboard')
    except NoReverseMatch:
        dashboard_url = reverse('home')

    if hasattr(site, 'configuration'):
        site_configuration_values = site.configuration.site_values
    else:
        site_configuration_values = {}

    return {
        # Platform information
        'homepage_url': marketing_link('ROOT'),
        'dashboard_url': dashboard_url,
        'template_revision': getattr(settings, 'EDX_PLATFORM_REVISION', None),
        'platform_name': get_config_value_from_site_or_settings(
            'PLATFORM_NAME',
            site=site,
            site_config_name='platform_name',
        ),
        'contact_email': get_config_value_from_site_or_settings(
            'CONTACT_EMAIL', site=site, site_config_name='contact_email'),
        'contact_mailing_address': get_config_value_from_site_or_settings(
            'CONTACT_MAILING_ADDRESS', site=site, site_config_name='contact_mailing_address'),
        'social_media_urls': get_config_value_from_site_or_settings('SOCIAL_MEDIA_FOOTER_ACE_URLS', site=site),
        'social_media_logo_urls': get_config_value_from_site_or_settings('SOCIAL_MEDIA_LOGO_URLS', site=site),
        'mobile_store_urls': get_config_value_from_site_or_settings('MOBILE_STORE_ACE_URLS', site=site),
        'mobile_store_logo_urls': get_config_value_from_site_or_settings('MOBILE_STORE_LOGO_URLS', site=site),
        'logo_url': get_logo_url_for_email(),
        'site_configuration_values': site_configuration_values,
    }
