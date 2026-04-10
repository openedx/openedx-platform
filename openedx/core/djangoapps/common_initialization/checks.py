"""
Common settings validations for the LMS and CMS.

Only populate this module with general settings validators which do not fit in
other, more specific djangoapps.  Usually, settings which are widely used
across the entire LMS or CMS can be validated here.
"""


from django.conf import settings
from django.core.checks import Error, Tags, Warning, register  # noqa: A004


@register(Tags.compatibility)
def validate_lms_root_url_setting(app_configs, **kwargs):  # lint-amnesty, pylint: disable=unused-argument
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
def validate_marketing_site_setting(app_configs, **kwargs):  # lint-amnesty, pylint: disable=unused-argument
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


@register(Tags.security, deploy=True)
def validate_secret_key(app_configs, **kwargs):  # lint-amnesty, pylint: disable=unused-argument
    """
    Deploy-time check: ``SECRET_KEY`` must be overridden from the insecure
    default built into ``openedx/envs/common.py``.

    Runs only under ``manage.py check --deploy`` and is skipped when
    ``DEBUG`` is True (devstack / test environments).
    """
    errors = []
    if getattr(settings, 'DEBUG', False):
        return errors
    secret = getattr(settings, 'SECRET_KEY', '') or ''
    if secret == 'dev key' or len(secret) < 32:
        errors.append(
            Error(
                'SECRET_KEY is still set to an insecure default. Override '
                'it via your deployment YAML with a 50+ character random '
                'value before running this instance with DEBUG=False.',
                id='common.djangoapps.common_initialization.E004',
            )
        )
    return errors


@register(Tags.security, deploy=True)
def validate_allowed_hosts(app_configs, **kwargs):  # lint-amnesty, pylint: disable=unused-argument
    """
    Deploy-time check: ``ALLOWED_HOSTS`` must not be a wildcard list.

    The built-in default is ``['*']`` because dev environments accept any
    Host header. Production deployments must override it to an explicit
    list of hostnames to prevent HTTP Host header attacks.
    """
    errors = []
    if getattr(settings, 'DEBUG', False):
        return errors
    allowed_hosts = getattr(settings, 'ALLOWED_HOSTS', [])
    if allowed_hosts == ['*'] or '*' in allowed_hosts:
        errors.append(
            Error(
                'ALLOWED_HOSTS is set to ["*"]. Override it via your '
                'deployment YAML with the exact hostnames this instance '
                'should serve to prevent Host header attacks.',
                id='common.djangoapps.common_initialization.E005',
            )
        )
    return errors


@register(Tags.security, deploy=True)
def validate_secure_cookie_settings(app_configs, **kwargs):  # lint-amnesty, pylint: disable=unused-argument
    """
    Deploy-time warning: ``SESSION_COOKIE_SECURE``, ``CSRF_COOKIE_SECURE``,
    and ``SECURE_HSTS_SECONDS`` should be enabled in any deployment that
    terminates TLS.

    Emitted as a Warning (not Error) so it surfaces on
    ``manage.py check --deploy`` without blocking startup for legacy
    deployments mid-upgrade.
    """
    warnings = []
    if getattr(settings, 'DEBUG', False):
        return warnings
    if not getattr(settings, 'SESSION_COOKIE_SECURE', False):
        warnings.append(
            Warning(
                'SESSION_COOKIE_SECURE is False. Session cookies may be '
                'transmitted over plain HTTP. Override to True via your '
                'deployment YAML.',
                id='common.djangoapps.common_initialization.W006',
            )
        )
    if not getattr(settings, 'CSRF_COOKIE_SECURE', False):
        warnings.append(
            Warning(
                'CSRF_COOKIE_SECURE is False. CSRF cookies may be '
                'transmitted over plain HTTP. Override to True via your '
                'deployment YAML.',
                id='common.djangoapps.common_initialization.W007',
            )
        )
    if getattr(settings, 'SECURE_HSTS_SECONDS', 0) == 0:
        warnings.append(
            Warning(
                'SECURE_HSTS_SECONDS is 0. Browsers will not pin this '
                'instance to HTTPS. Override to a non-zero value (e.g. '
                '31536000 for one year) via your deployment YAML once '
                'you are confident the site is served exclusively over '
                'HTTPS.',
                id='common.djangoapps.common_initialization.W008',
            )
        )
    return warnings
