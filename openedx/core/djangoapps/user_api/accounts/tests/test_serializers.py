"""
Test cases to cover Accounts-related serializers of the User API application
"""


import logging

from django.test import TestCase
from django.test.client import RequestFactory
from django.test.utils import override_settings
from django.utils.timezone import now
from testfixtures import LogCapture

from common.djangoapps.student.models import UserProfile
from common.djangoapps.student.tests.factories import UserFactory
from openedx.core.djangoapps.user_api.accounts import (
    ALL_USERS_VISIBILITY,
    PRIVATE_VISIBILITY,
)
from openedx.core.djangoapps.user_api.accounts.serializers import (
    UserReadOnlySerializer,
    get_profile_visibility,
)
from openedx.core.djangoapps.user_api.models import UserPreference
from openedx.core.djangoapps.user_api.preferences.api import set_user_preference

LOGGER_NAME = "openedx.core.djangoapps.user_api.accounts.serializers"
ACCOUNT_VISIBILITY_PREF_KEY = 'account_privacy'


class UserReadOnlySerializerTest(TestCase):  # lint-amnesty, pylint: disable=missing-class-docstring
    def setUp(self):
        super().setUp()
        request_factory = RequestFactory()
        self.request = request_factory.get('/api/user/v1/accounts/')
        self.user = UserFactory.build(username='test_user', email='test_user@test.com')
        self.user.save()
        self.config = {
            "default_visibility": "public",
            "public_fields": [
                'email', 'name', 'username'
            ],
        }

    def test_serializer_data(self):
        """
        Test serializer return data properly.
        """
        UserProfile.objects.create(user=self.user, name='test name')
        data = UserReadOnlySerializer(self.user, configuration=self.config, context={'request': self.request}).data
        assert data['username'] == self.user.username
        assert data['name'] == 'test name'
        assert data['email'] == self.user.email

    def test_user_no_profile(self):
        """
        Test serializer return data properly when user does not have profile.
        """
        with LogCapture(LOGGER_NAME, level=logging.DEBUG) as logger:
            data = UserReadOnlySerializer(self.user, configuration=self.config, context={'request': self.request}).data
            logger.check(
                (LOGGER_NAME, 'WARNING', 'user profile for the user [test_user] does not exist')
            )

        assert data['username'] == self.user.username
        assert data['name'] is None


class GetProfileVisibilityCoppaTest(TestCase):
    """
    Regression tests for Bug #37987.

    When ENABLE_COPPA_COMPLIANCE=True the platform scrubs year_of_birth for
    every learner at registration. The 2015 ``requires_parental_consent()``
    default treats a missing year_of_birth as "unknown age, assume minor"
    and ``get_profile_visibility`` therefore forced ``PRIVATE_VISIBILITY``,
    silently overriding the user's explicit ``account_privacy`` preference.
    """

    VISIBILITY_CONFIGURATION = {
        'default_visibility': ALL_USERS_VISIBILITY,
        'public_fields': ['account_privacy', 'profile_image', 'username'],
    }

    def setUp(self):
        super().setUp()
        self.user = UserFactory.create()
        self.profile = UserProfile.objects.get(user=self.user)
        self.profile.year_of_birth = None
        self.profile.save()

    @override_settings(ENABLE_COPPA_COMPLIANCE=True)
    def test_unit_coppa_mode_honors_all_users_preference(self):
        """
        Unit test for Bug #37987: when COPPA mode is on and year_of_birth
        is None, the user's explicit all_users preference must be honored.
        """
        set_user_preference(self.user, ACCOUNT_VISIBILITY_PREF_KEY, ALL_USERS_VISIBILITY)
        visibility = get_profile_visibility(self.profile, self.user, self.VISIBILITY_CONFIGURATION)
        assert visibility == ALL_USERS_VISIBILITY

    @override_settings(ENABLE_COPPA_COMPLIANCE=True)
    def test_unit_coppa_mode_honors_private_preference(self):
        """Under COPPA mode, explicit private preference still works."""
        set_user_preference(self.user, ACCOUNT_VISIBILITY_PREF_KEY, PRIVATE_VISIBILITY)
        visibility = get_profile_visibility(self.profile, self.user, self.VISIBILITY_CONFIGURATION)
        assert visibility == PRIVATE_VISIBILITY

    @override_settings(ENABLE_COPPA_COMPLIANCE=True)
    def test_integration_coppa_mode_falls_back_to_configuration_default(self):
        """
        With no preference set under COPPA mode, fall back to the configured
        default_visibility. Before the fix this returned PRIVATE_VISIBILITY.
        """
        UserPreference.objects.filter(user=self.user, key=ACCOUNT_VISIBILITY_PREF_KEY).delete()
        visibility = get_profile_visibility(self.profile, self.user, self.VISIBILITY_CONFIGURATION)
        assert visibility == ALL_USERS_VISIBILITY

    @override_settings(ENABLE_COPPA_COMPLIANCE=False)
    def test_non_coppa_mode_preserves_legacy_private_default(self):
        """
        When COPPA mode is off and year_of_birth is genuinely unknown, the
        pre-2021 contract MUST be preserved: force PRIVATE_VISIBILITY even
        if the user chose all_users.
        """
        set_user_preference(self.user, ACCOUNT_VISIBILITY_PREF_KEY, ALL_USERS_VISIBILITY)
        visibility = get_profile_visibility(self.profile, self.user, self.VISIBILITY_CONFIGURATION)
        assert visibility == PRIVATE_VISIBILITY

    @override_settings(ENABLE_COPPA_COMPLIANCE=True, PARENTAL_CONSENT_AGE_LIMIT=13)
    def test_bug_37987_regression_coppa_still_blocks_real_minor(self):
        """
        Regression for #37987: if a profile has a real year_of_birth that
        puts the user under PARENTAL_CONSENT_AGE_LIMIT, visibility must
        still be forced PRIVATE even under COPPA mode. The COPPA flag must
        not become a backdoor to publish minor profiles.
        """
        self.profile.year_of_birth = now().year - 8
        self.profile.save()
        set_user_preference(self.user, ACCOUNT_VISIBILITY_PREF_KEY, ALL_USERS_VISIBILITY)
        visibility = get_profile_visibility(self.profile, self.user, self.VISIBILITY_CONFIGURATION)
        assert visibility == PRIVATE_VISIBILITY

    @override_settings(ENABLE_COPPA_COMPLIANCE=True, PARENTAL_CONSENT_AGE_LIMIT=13)
    def test_coppa_mode_allows_adult_with_real_year_of_birth(self):
        """Adult with real year_of_birth is public under COPPA mode + all_users."""
        self.profile.year_of_birth = now().year - 25
        self.profile.save()
        set_user_preference(self.user, ACCOUNT_VISIBILITY_PREF_KEY, ALL_USERS_VISIBILITY)
        visibility = get_profile_visibility(self.profile, self.user, self.VISIBILITY_CONFIGURATION)
        assert visibility == ALL_USERS_VISIBILITY
