"""
Common settings validations for the LMS and CMS.

Only populate this module with general settings validators which do not fit in
other, more specific djangoapps.  Usually, settings which are widely used
across the entire LMS or CMS can be validated here.
"""


from django.conf import settings
from django.core.checks import Error, Tags, register


@register(Tags.compatibility)
def validate_lms_root_url_setting(app_configs, **kwargs):  # pylint: disable=unused-argument
    """
    Validates the LMS_ROOT_URL setting.
    """
    errors = []
    if not getattr(settings, 'LMS_ROOT_URL', None):
        errors.append(
            Error(
                'LMS_ROOT_URL is not defined.',
                id='common.djangoapps.common_initialization.E001',
            )
        )
    return errors


@register(Tags.compatibility)
def validate_marketing_site_setting(app_configs, **kwargs):  # pylint: disable=unused-argument
    """
    Validates marketing site related settings.
    """
    errors = []
    if settings.FEATURES.get('ENABLE_MKTG_SITE'):
        if not hasattr(settings, 'MKTG_URLS'):
            errors.append(
                Error(
                    'ENABLE_MKTG_SITE is True, but MKTG_URLS is not defined.',
                    id='common.djangoapps.common_initialization.E002',
                )
            )
        if not settings.MKTG_URLS.get('ROOT'):
            errors.append(
                Error(
                    'There is no ROOT defined in MKTG_URLS.',
                    id='common.djangoapps.common_initialization.E003',
                )
            )
    return errors
