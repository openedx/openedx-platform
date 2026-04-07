"""
Unit tests for user activity methods.
"""

from datetime import datetime, timedelta
from unittest.mock import Mock, patch

import ddt
from django.contrib.auth import get_user_model
from django.test.client import RequestFactory
from django.urls import reverse
from edx_django_utils.cache import TieredCache
from edx_toggles.toggles.testutils import override_waffle_flag
from freezegun import freeze_time

from common.djangoapps.edxmako.shortcuts import render_to_response
from common.djangoapps.student.models import CourseEnrollment
from common.djangoapps.student.tests.factories import UserFactory
from common.djangoapps.util.testing import UrlResetMixin
from lms.djangoapps.course_goals.models import UserActivity
from openedx.features.course_experience import ENABLE_COURSE_GOALS
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase
from xmodule.modulestore.tests.factories import BlockFactory, CourseFactory

User = get_user_model()


@ddt.ddt
@override_waffle_flag(ENABLE_COURSE_GOALS, active=True)
class UserActivityTests(UrlResetMixin, ModuleStoreTestCase):
    """
    Testing Course Goals User Activity
    """
    @patch.dict("django.conf.settings.FEATURES", {"ENABLE_DISCUSSION_SERVICE": True})
    def setUp(self):
        super().setUp()
        self.course = CourseFactory.create(
            start=datetime(2020, 1, 1),
            end=datetime(2028, 1, 1),
            enrollment_start=datetime(2020, 1, 1),
            enrollment_end=datetime(2028, 1, 1),
            emit_signals=True,
            modulestore=self.store,
            discussion_topics={"Test Topic": {"id": "test_topic"}},
        )
        chapter = BlockFactory(parent=self.course, category='chapter')
        BlockFactory(parent=chapter, category='sequential')

        self.client.login(username=self.user.username, password=self.user_password)
        CourseEnrollment.enroll(self.user, self.course.id)

        self.request = RequestFactory().get('foo')
        self.request.user = self.user

    def test_mfe_tabs_call_user_activity(self):
        '''
        New style tabs call one of two metadata endpoints
        These in turn call get_course_tab_list, which records user activity
        '''
        url = reverse('course-home:course-metadata', args=[self.course.id])
        with patch.object(UserActivity, 'record_user_activity') as record_user_activity_mock:
            self.client.get(url)
            record_user_activity_mock.assert_called_once()

        with patch.object(UserActivity, 'record_user_activity') as record_user_activity_mock:
            url = f'/api/courseware/course/{self.course.id}'
            self.client.get(url)
            record_user_activity_mock.assert_called_once()

    def test_non_mfe_tabs_call_user_activity(self):
        '''
        Tabs that are not yet part of the learning microfrontend all include the course_navigation.html file
        This file calls the get_course_tab_list function, which records user activity
        '''
        with patch.object(UserActivity, 'record_user_activity') as record_user_activity_mock:
            render_to_response('courseware/course_navigation.html', {'course': self.course, 'request': self.request})
            record_user_activity_mock.assert_called_once()

    def test_when_record_user_activity_does_not_perform_updates(self):
        '''
        Ensure that record user activity is not called when:
            1. user or course are not defined
            2. we have already recorded user activity for this user/course on this date
            and have a record in the cache
        '''
        with patch.object(TieredCache, 'set_all_tiers', wraps=TieredCache.set_all_tiers) as activity_cache_set:
            UserActivity.record_user_activity(self.user, None)
            activity_cache_set.assert_not_called()

            UserActivity.record_user_activity(None, self.course.id)
            activity_cache_set.assert_not_called()

        cache_key = 'goals_user_activity_{}_{}_{}'.format(
            str(self.user.id), str(self.course.id), str(datetime.now().date())
        )
        TieredCache.set_all_tiers(cache_key, 'test', 3600)

        with patch.object(TieredCache, 'set_all_tiers', wraps=TieredCache.set_all_tiers) as activity_cache_set:
            UserActivity.record_user_activity(self.user, self.course.id)
            activity_cache_set.assert_not_called()

            # Test that the happy path works to ensure that the measurement in this test isn't broken
            user2 = UserFactory()
            UserActivity.record_user_activity(user2, self.course.id)
            activity_cache_set.assert_called_once()

    def test_that_user_activity_cache_works_properly(self):
        '''
        Ensure that the cache for user activity works properly
            1. user or course are not defined
            2. we have already recorded user activity for this user/course on this date
            and have a record in the cache
        '''
        with patch.object(TieredCache, 'set_all_tiers', wraps=TieredCache.set_all_tiers) as activity_cache_set:
            UserActivity.record_user_activity(self.user, self.course.id)
            activity_cache_set.assert_called_once()

        with patch.object(TieredCache, 'set_all_tiers', wraps=TieredCache.set_all_tiers) as activity_cache_set:
            UserActivity.record_user_activity(self.user, self.course.id)
            activity_cache_set.assert_not_called()

            now_plus_1_day = datetime.now() + timedelta(days=1)
            with freeze_time(now_plus_1_day):
                UserActivity.record_user_activity(self.user, self.course.id)
                activity_cache_set.assert_called_once()

    def test_mobile_argument(self):
        '''
        Method only records activity if the request is coming from the mobile app
        when the only_if_mobile_app argument is true
        '''
        with patch.object(TieredCache, 'set_all_tiers', wraps=TieredCache.set_all_tiers) as activity_cache_set:
            UserActivity.record_user_activity(
                self.user, self.course.id, request=self.request, only_if_mobile_app=True
            )
            activity_cache_set.assert_not_called()

            with patch('lms.djangoapps.course_goals.models.is_request_from_mobile_app', return_value=True):
                UserActivity.record_user_activity(
                    self.user, self.course.id, request=self.request, only_if_mobile_app=True
                )
                activity_cache_set.assert_called_once()

    def test_masquerading(self):
        '''
        Method only records activity if the user is not masquerading
        '''
        with patch.object(TieredCache, 'set_all_tiers', wraps=TieredCache.set_all_tiers) as activity_cache_set:
            UserActivity.record_user_activity(self.user, self.course.id)
            activity_cache_set.assert_called_once()

        with patch.object(TieredCache, 'set_all_tiers', wraps=TieredCache.set_all_tiers) as activity_cache_set:
            with patch('lms.djangoapps.course_goals.models.is_masquerading', return_value=True):
                UserActivity.record_user_activity(self.user, self.course.id)
                activity_cache_set.assert_not_called()

    @ddt.data(
        '/api/course_home/v1/dates/{COURSE_ID}',
        '/api/mobile/v0.5/course_info/{COURSE_ID}/handouts',
        '/api/mobile/v0.5/course_info/{COURSE_ID}/updates',
        '/api/course_experience/v1/course_deadlines_info/{COURSE_ID}',
        '/api/course_home/v1/dates/{COURSE_ID}',
        '/api/courseware/course/{COURSE_ID}',
        '/api/discussion/v1/courses/{COURSE_ID}/',
        '/api/discussion/v1/course_topics/{COURSE_ID}',
    )
    @patch('lms.djangoapps.discussion.rest_api.api.get_course_commentable_counts', Mock(return_value={}))
    def test_mobile_app_user_activity_calls(self, url):
        url = url.replace('{COURSE_ID}', str(self.course.id))
        with patch.object(UserActivity, 'record_user_activity') as record_user_activity_mock:
            with patch.dict("django.conf.settings.FEATURES", {"ENABLE_DISCUSSION_SERVICE": True}):
                self.client.get(url)
                record_user_activity_mock.assert_called_once()

    def test_mobile_app_user_activity_other_calls(self):
        # thread view call
        with patch.object(UserActivity, 'record_user_activity') as record_user_activity_mock:
            try:
                self.client.get(reverse("thread-list"), {'course_id': str(self.course.id)})
            except:  # pylint: disable=bare-except
                pass
            record_user_activity_mock.assert_called_once()

        # blocks call
        with patch.object(UserActivity, 'record_user_activity') as record_user_activity_mock:
            url = '/api/courses/v2/blocks/'
            self.client.get(url, {'course_id': str(self.course.id), 'username': self.user.username})
            record_user_activity_mock.assert_called_once()

        # xblock call
        with patch.object(UserActivity, 'record_user_activity') as record_user_activity_mock:
            url = '/xblock/' + str(self.course.scope_ids.usage_id)
            try:
                self.client.get(url)
            except:  # pylint: disable=bare-except
                pass
            record_user_activity_mock.assert_called_once()
