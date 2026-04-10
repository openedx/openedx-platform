"""
Regression tests for the security-header defaults declared in
``openedx/envs/common.py`` that are consumed by Django's
``SecurityMiddleware``.
"""
from django.conf import settings
from django.test import SimpleTestCase


class SecurityHeaderDefaultsTest(SimpleTestCase):
    """
    Lock in the values of the two security-header settings that
    Django's ``SecurityMiddleware`` reads. Neither setting has runtime
    effect until ``django.middleware.security.SecurityMiddleware`` is
    wired into ``MIDDLEWARE``, but the defaults must still be correct
    so that a future deployment does not silently inherit a weaker
    value (no ``Referrer-Policy`` header at all) or a stricter value
    (``Cross-Origin-Opener-Policy: same-origin``) that would break
    legitimate OAuth / SSO / LTI 1.3 popup flows.
    """

    def test_referrer_policy_default(self):
        assert settings.SECURE_REFERRER_POLICY == 'strict-origin-when-cross-origin'

    def test_cross_origin_opener_policy_default(self):
        # ``same-origin-allow-popups`` keeps the Spectre isolation properties
        # of COOP for the current document while still allowing popup OAuth
        # / SSO flows to retain their ``window.opener`` reference, which the
        # LMS third_party_auth and LTI 1.3 launch flows rely on.
        assert settings.SECURE_CROSS_ORIGIN_OPENER_POLICY == 'same-origin-allow-popups'
