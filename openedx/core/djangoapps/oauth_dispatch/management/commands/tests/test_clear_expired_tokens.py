"""
Tests the ``edx_clear_expired_tokens`` management command.
"""

import math
from datetime import timedelta
from unittest.mock import patch

import ddt
import pytest
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.management import call_command
from django.db.models import QuerySet
from django.test import TestCase
from django.test.utils import override_settings
from django.utils import timezone
from oauth2_provider.models import AccessToken, Grant, RefreshToken
from testfixtures import LogCapture

from common.djangoapps.student.tests.factories import UserFactory
from openedx.core.djangoapps.oauth_dispatch.tests import factories
from openedx.core.djangolib.testing.utils import skip_unless_lms

LOGGER_NAME = 'openedx.core.djangoapps.oauth_dispatch.management.commands.edx_clear_expired_tokens'


def counter(fn):
    """
    Adds a call counter to the given function.
    Source: http://code.activestate.com/recipes/577534-counting-decorator/
    """
    def _counted(*largs, **kargs):
        _counted.invocations += 1
        return fn(*largs, **kargs)

    _counted.invocations = 0
    return _counted


def create_factory_refresh_token_for_user(user, expires, revoked=None):
    application = factories.ApplicationFactory(user=user)
    access_token = factories.AccessTokenFactory(user=user, application=application, expires=expires)
    return factories.RefreshTokenFactory(access_token=access_token, application=application, user=user,
                                         revoked=revoked)


@ddt.ddt
@skip_unless_lms
class EdxClearExpiredTokensTests(TestCase):  # lint-amnesty, pylint: disable=missing-class-docstring

    # patching REFRESH_TOKEN_EXPIRE_SECONDS because override_settings not working.
    @patch('oauth2_provider.settings.oauth2_settings.REFRESH_TOKEN_EXPIRE_SECONDS', 'xyz')
    def test_invalid_expiration_time(self):
        with LogCapture(LOGGER_NAME) as log:
            with pytest.raises(ImproperlyConfigured):
                call_command('edx_clear_expired_tokens')
                log.check(
                    (
                        LOGGER_NAME,
                        'EXCEPTION',
                        'REFRESH_TOKEN_EXPIRE_SECONDS must be either a timedelta or seconds'
                    )
                )

    @override_settings()
    def test_excluded_application_ids(self):
        settings.OAUTH2_PROVIDER['REFRESH_TOKEN_EXPIRE_SECONDS'] = 3600
        expires = timezone.now() - timedelta(days=1)
        application = factories.ApplicationFactory()
        access_token = factories.AccessTokenFactory(user=application.user, application=application, expires=expires)
        factories.RefreshTokenFactory(user=application.user, application=application, access_token=access_token)
        with LogCapture(LOGGER_NAME) as log:
            call_command('edx_clear_expired_tokens', sleep_time=0, excluded_application_ids=str(application.id))
            log.check_present(
                (
                    LOGGER_NAME,
                    'INFO',
                    f'Final deletion count: Cleaned {0} rows from {RefreshToken.__name__} table'
                ),
                (
                    LOGGER_NAME,
                    'INFO',
                    f'Final deletion count: Cleaned {0} rows from {RefreshToken.__name__} table'
                ),
                (
                    LOGGER_NAME,
                    'INFO',
                    f'Final deletion count: Cleaned {0} rows from {AccessToken.__name__} table',
                ),
                (
                    LOGGER_NAME,
                    'INFO',
                    f'Final deletion count: Cleaned 0 rows from {Grant.__name__} table',
                )
            )
        assert RefreshToken.objects.filter(application=application).exists()

    @override_settings()
    def test_clear_expired_tokens(self):
        settings.OAUTH2_PROVIDER['REFRESH_TOKEN_EXPIRE_SECONDS'] = 3600
        initial_count = 5
        now = timezone.now()
        expires = now - timedelta(days=1)
        refresh_expire_cutoff = now - timedelta(seconds=3600)
        users = UserFactory.create_batch(initial_count)
        for user in users:
            application = factories.ApplicationFactory(user=user)
            factories.AccessTokenFactory(user=user, application=application, expires=expires)
        assert AccessToken.objects.filter(refresh_token__isnull=True, expires__lt=refresh_expire_cutoff).count()\
               == initial_count
        original_delete = QuerySet.delete
        QuerySet.delete = counter(QuerySet.delete)
        try:
            call_command('edx_clear_expired_tokens', batch_size=1, sleep_time=0)
            # four being the number of tables we'll end up unnecessarily calling .delete on once
            assert QuerySet.delete.invocations == initial_count + 4  # pylint: disable=no-member
            assert AccessToken.objects.filter(refresh_token__isnull=True,
                                              expires__lt=refresh_expire_cutoff).count() == 0
        finally:
            QuerySet.delete = original_delete

    @override_settings()
    def test_clear_revoked_refresh_tokens(self):
        settings.OAUTH2_PROVIDER['REFRESH_TOKEN_EXPIRE_SECONDS'] = 3600
        now = timezone.now()
        # expiry date in the future because we only want to check revoked tokens, not expired ones
        expires = now + timedelta(days=1)
        refresh_expires = now - timedelta(seconds=3600)
        user_keep = UserFactory()
        user_revoke = UserFactory()
        keep_token = create_factory_refresh_token_for_user(user_keep, expires=expires)
        revoke_token = create_factory_refresh_token_for_user(user_revoke, expires=expires,
                                                             revoked=refresh_expires - timedelta(seconds=1))
        original_delete = QuerySet.delete
        QuerySet.delete = counter(QuerySet.delete)
        try:
            call_command('edx_clear_expired_tokens', sleep_time=0, access_tokens=False, refresh_tokens=False,
                         grants=False)
            # 1 overhead call, 1 real call
            assert QuerySet.delete.invocations == 2
            assert RefreshToken.objects.filter(revoked__lt=refresh_expires).count() == 0
            # revoked token has been deleted
            with self.assertRaises(RefreshToken.DoesNotExist):
                RefreshToken.objects.get(token=revoke_token.token)
            # normal token is still there
            assert RefreshToken.objects.get(token=keep_token.token) == keep_token
        finally:
            QuerySet.delete = original_delete

    @override_settings()
    @ddt.unpack
    @ddt.data(
        (5, 1),
        (500, 1),
        (7, 5),
        (500, 50),
    )
    def test_clear_expired_refreshtokens(self, initial_count, batch_size):
        settings.OAUTH2_PROVIDER['REFRESH_TOKEN_EXPIRE_SECONDS'] = 3600
        now = timezone.now()
        expires = now - timedelta(days=1)
        refresh_expires = now - timedelta(seconds=3600)
        users = UserFactory.create_batch(initial_count)
        for user in users:
            application = factories.ApplicationFactory(user=user)
            access_token = factories.AccessTokenFactory(user=user, application=application, expires=expires)
            factories.RefreshTokenFactory(access_token=access_token, application=application, user=user)
        assert RefreshToken.objects.filter(access_token__expires__lt=refresh_expires).count() == initial_count
        original_delete = QuerySet.delete
        QuerySet.delete = counter(QuerySet.delete)
        try:
            call_command('edx_clear_expired_tokens', batch_size=batch_size, sleep_time=0)
            assert QuerySet.delete.invocations == (math.ceil(initial_count / batch_size) * 2 + 4)
            assert RefreshToken.objects.filter(access_token__expires__lt=refresh_expires).count() == 0
        finally:
            QuerySet.delete = original_delete
