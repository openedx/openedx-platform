"""
Unit tests for courseware context_processor
"""

from unittest.mock import Mock, patch  # lint-amnesty, pylint: disable=wrong-import-order

from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from pytz import timezone

from common.djangoapps.student.tests.factories import UserFactory
from lms.djangoapps.courseware.context_processor import (
    get_user_timezone_or_last_seen_timezone_or_utc,
    user_timezone_locale_prefs,
)
from openedx.core.djangoapps.user_api.preferences.api import set_user_preference
from xmodule.modulestore.tests.django_utils import (
    ModuleStoreTestCase,  # lint-amnesty, pylint: disable=wrong-import-order
)
from xmodule.modulestore.tests.factories import CourseFactory  # lint-amnesty, pylint: disable=wrong-import-order


class UserPrefContextProcessorUnitTest(ModuleStoreTestCase):
    """
    Unit test for courseware context_processor
    """

    def setUp(self):
        super().setUp()

        self.user = UserFactory.create(password='foo')
        self.request = Mock()
        self.request.user = self.user

    def test_anonymous_user(self):
        self.request.user = AnonymousUser()
        context = user_timezone_locale_prefs(self.request)
        assert context['user_timezone'] is None
        assert context['user_language'] == settings.LANGUAGE_CODE

    def test_no_timezone_preference(self):
        set_user_preference(self.user, 'pref-lang', 'en')
        context = user_timezone_locale_prefs(self.request)
        assert context['user_timezone'] is None
        assert context['user_language'] is not None
        assert context['user_language'] == 'en'

    def test_no_language_preference(self):
        set_user_preference(self.user, 'time_zone', 'Asia/Tokyo')
        context = user_timezone_locale_prefs(self.request)
        assert context['user_language'] is None
        assert context['user_timezone'] is not None
        assert context['user_timezone'] == 'Asia/Tokyo'

    @patch("lms.djangoapps.courseware.context_processor.get_value")
    def test_site_wide_language_set(self, mock_get_value):
        mock_get_value.return_value = 'ar'
        set_user_preference(self.user, 'pref-lang', 'en')
        context = user_timezone_locale_prefs(self.request)
        assert context['user_language'] == 'ar'

    def test_get_user_timezone_or_last_seen_timezone_or_utc(self):
        # We default to UTC
        course = CourseFactory()
        time_zone = get_user_timezone_or_last_seen_timezone_or_utc(self.user)
        assert time_zone == timezone('UTC')

        # We record the timezone when a user hits the courseware api. Also sanitize input test
        self.client.login(username=self.user.username, password='foo')
        self.client.get(f'/api/courseware/course/{course.id}?browser_timezone=America/New_York\x00')
        time_zone = get_user_timezone_or_last_seen_timezone_or_utc(self.user)
        assert time_zone == timezone('America/New_York')

        # If a user has their timezone set, then we use that setting
        set_user_preference(self.user, 'time_zone', 'Asia/Tokyo')
        time_zone = get_user_timezone_or_last_seen_timezone_or_utc(self.user)
        assert time_zone == timezone('Asia/Tokyo')

        # If we do not recognize the user's timezone, we default to UTC
        with patch('lms.djangoapps.courseware.context_processor.get_user_preference', return_value='Unknown/Timezone'):
            time_zone = get_user_timezone_or_last_seen_timezone_or_utc(self.user)
        assert time_zone == timezone('UTC')
