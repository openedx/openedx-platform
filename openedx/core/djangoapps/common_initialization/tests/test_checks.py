"""
Regression tests for the deploy-time security checks in
``openedx.core.djangoapps.common_initialization.checks``.
"""
from django.test import TestCase, override_settings

from openedx.core.djangoapps.common_initialization.checks import (
    validate_allowed_hosts,
    validate_secret_key,
    validate_secure_cookie_settings,
)


class ValidateSecretKeyTests(TestCase):
    """
    Deploy-time check: SECRET_KEY must not be the insecure default.
    """

    @override_settings(DEBUG=False, SECRET_KEY='dev key')
    def test_sec_secret_key_default_dev_key_raises_error_regression(self):
        errors = validate_secret_key(None)
        assert len(errors) == 1
        assert errors[0].id == 'common.djangoapps.common_initialization.E004'

    @override_settings(DEBUG=False, SECRET_KEY='short')
    def test_sec_secret_key_short_raises_error_regression(self):
        errors = validate_secret_key(None)
        assert len(errors) == 1
        assert errors[0].id == 'common.djangoapps.common_initialization.E004'

    @override_settings(
        DEBUG=False,
        SECRET_KEY='a' * 50,  # 50-char placeholder strong-enough key
    )
    def test_sec_secret_key_strong_passes_regression(self):
        errors = validate_secret_key(None)
        assert errors == []

    @override_settings(DEBUG=True, SECRET_KEY='dev key')
    def test_sec_secret_key_debug_mode_skipped_regression(self):
        """With DEBUG=True (devstack/test), the check is a no-op."""
        errors = validate_secret_key(None)
        assert errors == []


class ValidateAllowedHostsTests(TestCase):
    """
    Deploy-time check: ALLOWED_HOSTS must not be a wildcard.
    """

    @override_settings(DEBUG=False, ALLOWED_HOSTS=['*'])
    def test_sec_allowed_hosts_wildcard_raises_error_regression(self):
        errors = validate_allowed_hosts(None)
        assert len(errors) == 1
        assert errors[0].id == 'common.djangoapps.common_initialization.E005'

    @override_settings(DEBUG=False, ALLOWED_HOSTS=['lms.example.com'])
    def test_sec_allowed_hosts_specific_passes_regression(self):
        errors = validate_allowed_hosts(None)
        assert errors == []

    @override_settings(DEBUG=True, ALLOWED_HOSTS=['*'])
    def test_sec_allowed_hosts_debug_mode_skipped_regression(self):
        errors = validate_allowed_hosts(None)
        assert errors == []


class ValidateSecureCookieSettingsTests(TestCase):
    """
    Deploy-time warning: cookie security flags and HSTS should be enabled.
    """

    @override_settings(
        DEBUG=False,
        SESSION_COOKIE_SECURE=False,
        CSRF_COOKIE_SECURE=False,
        SECURE_HSTS_SECONDS=0,
    )
    def test_sec_cookie_insecure_defaults_warn_regression(self):
        warnings = validate_secure_cookie_settings(None)
        ids = {w.id for w in warnings}
        assert 'common.djangoapps.common_initialization.W006' in ids
        assert 'common.djangoapps.common_initialization.W007' in ids
        assert 'common.djangoapps.common_initialization.W008' in ids

    @override_settings(
        DEBUG=False,
        SESSION_COOKIE_SECURE=True,
        CSRF_COOKIE_SECURE=True,
        SECURE_HSTS_SECONDS=31536000,
    )
    def test_sec_cookie_secure_true_passes_regression(self):
        warnings = validate_secure_cookie_settings(None)
        assert warnings == []

    @override_settings(DEBUG=True, SESSION_COOKIE_SECURE=False)
    def test_sec_cookie_debug_mode_skipped_regression(self):
        warnings = validate_secure_cookie_settings(None)
        assert warnings == []


class SecurityMiddlewarePresenceTests(TestCase):
    """
    Integration: verify django.middleware.security.SecurityMiddleware is
    present in both LMS and CMS middleware stacks and sits between
    XForwardedForMiddleware and CommonMiddleware.
    """

    def test_sec_security_middleware_in_lms_stack_regression(self):
        from lms.envs.common import MIDDLEWARE
        assert 'django.middleware.security.SecurityMiddleware' in MIDDLEWARE

    def test_sec_security_middleware_in_cms_stack_regression(self):
        from cms.envs.common import MIDDLEWARE
        assert 'django.middleware.security.SecurityMiddleware' in MIDDLEWARE

    def test_sec_security_middleware_after_x_forwarded_for_lms_regression(self):
        from lms.envs.common import MIDDLEWARE
        xff_idx = MIDDLEWARE.index(
            'openedx.core.lib.x_forwarded_for.middleware.XForwardedForMiddleware'
        )
        sm_idx = MIDDLEWARE.index('django.middleware.security.SecurityMiddleware')
        common_idx = MIDDLEWARE.index('django.middleware.common.CommonMiddleware')
        assert xff_idx < sm_idx < common_idx
