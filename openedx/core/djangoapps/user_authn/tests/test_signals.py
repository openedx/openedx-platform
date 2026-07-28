"""Tests for user_authn signals"""

from unittest.mock import patch

from django.test import TestCase

from common.djangoapps.student.tests.factories import UserFactory, UserProfileFactory
from openedx.core.djangolib.testing.utils import skip_unless_lms


@skip_unless_lms
class UserAuthnSignalTests(TestCase):
    """Tests for signal handlers"""

    def test_identify_call_on_user_change(self):
        user = UserFactory()

        with patch('openedx.core.djangoapps.user_authn.signals.segment') as mock_segment:
            user.email = 'user@example.com'
            user.save()
        assert mock_segment.identify.call_count == 1
        assert mock_segment.identify.call_args[0] == (user.id, {'email': 'user@example.com'})

    def test_identify_call_on_profile_change(self):
        profile = UserProfileFactory(user=UserFactory())

        with patch('openedx.core.djangoapps.user_authn.signals.segment') as mock_segment:
            profile.gender = 'f'
            profile.save()
        assert mock_segment.identify.call_count == 1
        assert mock_segment.identify.call_args[0] == (profile.user_id, {'gender': 'Female'})
