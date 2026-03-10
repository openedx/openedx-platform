"""
Tests for schedules resolvers
"""


import datetime
from unittest.mock import Mock

import crum
import ddt
from zoneinfo import ZoneInfo
from django.test import TestCase
from django.test.client import RequestFactory
from django.test.utils import override_settings
from edx_toggles.toggles.testutils import override_waffle_switch
from testfixtures import LogCapture
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase
from xmodule.modulestore.tests.factories import CourseFactory, BlockFactory

from common.djangoapps.student.models import CourseEnrollment
from common.djangoapps.student.tests.factories import CourseEnrollmentFactory, UserFactory
from lms.djangoapps.experiments.testutils import override_experiment_waffle_flag
from openedx.core.djangoapps.content.course_overviews.tests.factories import CourseOverviewFactory
from openedx.core.djangoapps.schedules.config import (
    _EXTERNAL_COURSE_UPDATES_FLAG,
    COURSE_UPDATE_SHOW_UNSUBSCRIBE_WAFFLE_SWITCH,
)
from openedx.core.djangoapps.schedules.models import Schedule
from openedx.core.djangoapps.schedules.resolvers import (
    LOG,
    BinnedSchedulesBaseResolver,
    CourseNextSectionUpdate,
    CourseUpdateResolver,
)
from openedx.core.djangoapps.schedules.tests.factories import ScheduleConfigFactory
from openedx.core.djangoapps.site_configuration.tests.factories import SiteConfigurationFactory, SiteFactory
from openedx.core.djangolib.testing.utils import CacheIsolationMixin, skip_unless_lms


class SchedulesResolverTestMixin(CacheIsolationMixin):
    """
    Base class for the resolver tests.
    """
    def setUp(self):
        super().setUp()
        self.site = SiteFactory.create()
        self.site_config = SiteConfigurationFactory(site=self.site)
        self.schedule_config = ScheduleConfigFactory.create(site=self.site)


@ddt.ddt
@skip_unless_lms
class TestBinnedSchedulesBaseResolver(SchedulesResolverTestMixin, TestCase):
    """
    Tests the BinnedSchedulesBaseResolver.
    """
    def setUp(self):
        super().setUp()

        self.resolver = BinnedSchedulesBaseResolver(
            async_send_task=Mock(name='async_send_task'),
            site=self.site,
            target_datetime=datetime.datetime.now(),
            day_offset=3,
            bin_num=2,
        )

    @ddt.data(
        'course1'
    )
    def test_get_course_org_filter_equal(self, course_org_filter):
        self.site_config.site_values['course_org_filter'] = course_org_filter
        self.site_config.save()
        mock_query = Mock()
        result = self.resolver.filter_by_org(mock_query)
        assert result == mock_query.filter.return_value
        mock_query.filter.assert_called_once_with(enrollment__course__org=course_org_filter)

    @ddt.unpack
    @ddt.data(
        (['course1', 'course2'], ['course1', 'course2'])
    )
    def test_get_course_org_filter_include__in(self, course_org_filter, expected_org_list):
        self.site_config.site_values['course_org_filter'] = course_org_filter
        self.site_config.save()
        mock_query = Mock()
        result = self.resolver.filter_by_org(mock_query)
        assert result == mock_query.filter.return_value
        mock_query.filter.assert_called_once_with(enrollment__course__org__in=expected_org_list)

    @ddt.unpack
    @ddt.data(
        (None, set()),
        ('course1', {'course1'}),
        (['course1', 'course2'], {'course1', 'course2'})
    )
    def test_get_course_org_filter_exclude__in(self, course_org_filter, expected_org_list):
        SiteConfigurationFactory.create(
            site_values={'course_org_filter': course_org_filter}
        )
        mock_query = Mock()
        result = self.resolver.filter_by_org(mock_query)
        mock_query.exclude.assert_called_once_with(enrollment__course__org__in=expected_org_list)
        assert result == mock_query.exclude.return_value

    @ddt.data(0, 1)
    def test_external_course_updates(self, bucket):
        """Confirm that we exclude enrollments in the external course updates experiment"""
        user = UserFactory()
        overview1 = CourseOverviewFactory(has_highlights=False)  # set has_highlights just to avoid a modulestore lookup
        overview2 = CourseOverviewFactory(has_highlights=False)

        # We need to enroll with a request, because our specific experiment code expects it
        self.addCleanup(crum.set_current_request, None)
        request = RequestFactory().get(self.site)
        request.user = user
        crum.set_current_request(request)
        enrollment1 = CourseEnrollment.enroll(user, overview1.id)
        with override_experiment_waffle_flag(_EXTERNAL_COURSE_UPDATES_FLAG, bucket=bucket):
            enrollment2 = CourseEnrollment.enroll(user, overview2.id)

        # OK, at this point, we'd expect course1 to be returned, but course2's enrollment to be excluded by the
        # experiment. Note that the experiment waffle is currently inactive, but they should still be excluded because
        # they were bucketed at enrollment time.
        bin_num = BinnedSchedulesBaseResolver.bin_num_for_user_id(user.id)
        resolver = BinnedSchedulesBaseResolver(None, self.site, datetime.datetime.now(ZoneInfo("UTC")), 0, bin_num)
        resolver.schedule_date_field = 'created'
        schedules = resolver.get_schedules_with_target_date_by_bin_and_orgs()

        if bucket == 1:
            assert len(schedules) == 1
            assert schedules[0].enrollment == enrollment1
        else:
            assert len(schedules) == 2
            assert {s.enrollment for s in schedules} == {enrollment1, enrollment2}


@skip_unless_lms
class TestCourseUpdateResolver(SchedulesResolverTestMixin, ModuleStoreTestCase):
    """
    Tests the CourseUpdateResolver.
    """
    def setUp(self):
        super().setUp()
        self.course = CourseFactory.create(highlights_enabled_for_messaging=True)
        with self.store.bulk_operations(self.course.id):
            BlockFactory.create(parent=self.course, category='chapter', highlights=['good stuff'])

    def create_resolver(self):
        """
        Creates a CourseUpdateResolver with an enrollment to schedule.
        """
        enrollment = CourseEnrollmentFactory(course_id=self.course.id, user=self.user, mode='audit')

        return CourseUpdateResolver(
            async_send_task=Mock(name='async_send_task'),
            site=self.site_config.site,
            target_datetime=enrollment.schedule.start_date,
            day_offset=-7,
            bin_num=CourseUpdateResolver.bin_num_for_user_id(self.user.id),
        )

    @override_settings(CONTACT_MAILING_ADDRESS='123 Sesame Street')
    @override_settings(LOGO_URL_PNG='https://www.logo.png')
    def test_schedule_context(self):
        resolver = self.create_resolver()
        schedules = list(resolver.schedules_for_bin())
        apple_logo_url = 'http://email-media.s3.amazonaws.com/edX/2021/store_apple_229x78.jpg'
        google_logo_url = 'http://email-media.s3.amazonaws.com/edX/2021/store_google_253x78.jpg'
        apple_store_url = 'https://itunes.apple.com/us/app/edx/id945480667?mt=8'
        google_store_url = 'https://play.google.com/store/apps/details?id=org.edx.mobile'
        facebook_url = 'http://www.facebook.com/EdxOnline'
        linkedin_url = 'http://www.linkedin.com/company/edx'
        twitter_url = 'https://twitter.com/edXOnline'
        reddit_url = 'http://www.reddit.com/r/edx'
        facebook_logo_url = 'http://email-media.s3.amazonaws.com/edX/2021/social_1_fb.png'
        linkedin_logo_url = 'http://email-media.s3.amazonaws.com/edX/2021/social_3_linkedin.png'
        twitter_logo_url = 'http://email-media.s3.amazonaws.com/edX/2021/social_2_twitter.png'
        reddit_logo_url = 'http://email-media.s3.amazonaws.com/edX/2021/social_5_reddit.png'
        expected_context = {
            'contact_email': 'info@example.com',
            'contact_mailing_address': '123 Sesame Street',
            'course_ids': [str(self.course.id)],
            'course_name': self.course.display_name,
            'course_url': f'http://learning-mfe/course/{self.course.id}/home',
            'dashboard_url': '/dashboard',
            'homepage_url': '/',
            'mobile_store_logo_urls': {'apple': apple_logo_url,
                                       'google': google_logo_url},
            'mobile_store_urls': {'apple': apple_store_url,
                                  'google': google_store_url},
            'logo_url': 'https://www.logo.png',
            'platform_name': '\xe9dX',
            'show_upsell': False,
            'site_configuration_values': {},
            'social_media_logo_urls': {'facebook': facebook_logo_url,
                                       'linkedin': linkedin_logo_url,
                                       'reddit': reddit_logo_url,
                                       'twitter': twitter_logo_url},
            'social_media_urls': {'facebook': facebook_url,
                                  'linkedin': linkedin_url,
                                  'reddit': reddit_url,
                                  'twitter': twitter_url},
            'template_revision': 'release',
            'unsubscribe_url': None,
            'week_highlights': ['good stuff'],
            'week_num': 1,
        }
        assert schedules == [(self.user, None, expected_context)]

    @override_waffle_switch(COURSE_UPDATE_SHOW_UNSUBSCRIBE_WAFFLE_SWITCH, True)
    def test_schedule_context_show_unsubscribe(self):
        resolver = self.create_resolver()
        schedules = list(resolver.schedules_for_bin())
        assert 'optout' in schedules[0][2]['unsubscribe_url']

    def test_get_schedules_with_target_date_by_bin_and_orgs_filter_inactive_users(self):
        """Tests that schedules of inactive users are excluded"""
        resolver = self.create_resolver()
        schedules = resolver.get_schedules_with_target_date_by_bin_and_orgs()

        assert schedules.count() == 1
        self.user.is_active = False
        self.user.save()
        schedules = resolver.get_schedules_with_target_date_by_bin_and_orgs()
        assert schedules.count() == 0


@skip_unless_lms
class TestCourseNextSectionUpdateResolver(SchedulesResolverTestMixin, ModuleStoreTestCase):
    """
    Tests the TestCourseNextSectionUpdateResolver.
    """
    ENABLED_SIGNALS = ['course_published']

    def setUp(self):
        super().setUp()
        self.today = datetime.datetime.now(ZoneInfo("UTC"))
        self.yesterday = self.today - datetime.timedelta(days=1)
        self.course = CourseFactory.create(
            highlights_enabled_for_messaging=True, self_paced=True,
            # putting it in the past so the schedule can be later than the start
            start=self.today - datetime.timedelta(days=30)
        )

        with self.store.bulk_operations(self.course.id):
            BlockFactory.create(parent=self.course, category='chapter', highlights=['good stuff 1'])
            BlockFactory.create(parent=self.course, category='chapter', highlights=['good stuff 2'])
            BlockFactory.create(parent=self.course, category='chapter', highlights=['good stuff 3'])
            BlockFactory.create(parent=self.course, category='chapter', highlights=['good stuff 4'])

    def create_resolver(self, user_start_date_offset=8):
        """
        Creates a CourseNextSectionUpdateResolver with an enrollment to schedule.
        """
        CourseEnrollmentFactory(course_id=self.course.id, user=self.user, mode='audit')

        # Need to update the user's schedule so the due date for the chapter we want
        # matches with the user's schedule and the target date. The numbers are based on the
        # course having the default course duration of 28 days.
        user_schedule = Schedule.objects.first()
        user_schedule.start_date = self.today - datetime.timedelta(days=user_start_date_offset)
        user_schedule.save()

        return CourseNextSectionUpdate(
            async_send_task=Mock(name='async_send_task'),
            site=self.site_config.site,
            target_datetime=self.yesterday,
            course_id=self.course.id,
        )

    @override_settings(CONTACT_MAILING_ADDRESS='123 Sesame Street')
    @override_settings(LOGO_URL_PNG='https://www.logo.png')
    @override_settings(SELF_PACED_BANNER_URL='')
    @override_settings(SELF_PACED_CLOUD_URL='')
    def test_schedule_context(self):
        resolver = self.create_resolver()
        # using this to make sure the select_related stays intact
        with self.assertNumQueries(26):
            sc = resolver.get_schedules()
            schedules = list(sc)
        apple_logo_url = 'http://email-media.s3.amazonaws.com/edX/2021/store_apple_229x78.jpg'
        google_logo_url = 'http://email-media.s3.amazonaws.com/edX/2021/store_google_253x78.jpg'
        apple_store_url = 'https://itunes.apple.com/us/app/edx/id945480667?mt=8'
        google_store_url = 'https://play.google.com/store/apps/details?id=org.edx.mobile'
        facebook_url = 'http://www.facebook.com/EdxOnline'
        linkedin_url = 'http://www.linkedin.com/company/edx'
        twitter_url = 'https://twitter.com/edXOnline'
        reddit_url = 'http://www.reddit.com/r/edx'
        facebook_logo_url = 'http://email-media.s3.amazonaws.com/edX/2021/social_1_fb.png'
        linkedin_logo_url = 'http://email-media.s3.amazonaws.com/edX/2021/social_3_linkedin.png'
        twitter_logo_url = 'http://email-media.s3.amazonaws.com/edX/2021/social_2_twitter.png'
        reddit_logo_url = 'http://email-media.s3.amazonaws.com/edX/2021/social_5_reddit.png'
        expected_context = {
            'contact_email': 'info@example.com',
            'contact_mailing_address': '123 Sesame Street',
            'course_ids': [str(self.course.id)],
            'course_id': str(self.course.id),
            'course_name': self.course.display_name,
            'course_url': f'http://learning-mfe/course/{self.course.id}/home',
            'dashboard_url': '/dashboard',
            'homepage_url': '/',
            'mobile_store_logo_urls': {'apple': apple_logo_url,
                                       'google': google_logo_url},
            'mobile_store_urls': {'apple': apple_store_url,
                                  'google': google_store_url},
            'logo_url': 'https://www.logo.png',
            'platform_name': '\xe9dX',
            'show_upsell': False,
            'site_configuration_values': {},
            'social_media_logo_urls': {'facebook': facebook_logo_url,
                                       'linkedin': linkedin_logo_url,
                                       'reddit': reddit_logo_url,
                                       'twitter': twitter_logo_url},
            'social_media_urls': {'facebook': facebook_url,
                                  'linkedin': linkedin_url,
                                  'reddit': reddit_url,
                                  'twitter': twitter_url},
            'template_revision': 'release',
            'unsubscribe_url': None,
            'self_paced_banner_url': '',
            'self_paced_cloud_url': '',
            'week_highlights': ['good stuff 2'],
            'week_num': 2,
        }
        assert schedules == [(self.user, None, expected_context)]

    @override_waffle_switch(COURSE_UPDATE_SHOW_UNSUBSCRIBE_WAFFLE_SWITCH, True)
    def test_schedule_context_show_unsubscribe(self):
        resolver = self.create_resolver()
        schedules = list(resolver.get_schedules())
        assert 'optout' in schedules[0][2]['unsubscribe_url']

    def test_schedule_context_error(self):
        resolver = self.create_resolver(user_start_date_offset=29)
        with LogCapture(LOG.name) as log_capture:
            list(resolver.get_schedules())
            log_message = ('Next Section Course Update: Last section was reached. '
                           'There are no more highlights for {}'.format(self.course.id))
            log_capture.check_present((LOG.name, 'WARNING', log_message))

    def test_no_updates_if_course_ended(self):
        self.course.end = self.yesterday
        self.course = self.update_course(self.course, self.user.id)
        resolver = self.create_resolver()
        schedules = list(resolver.get_schedules())
        self.assertListEqual(schedules, [])
