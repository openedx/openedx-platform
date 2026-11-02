"""
Base file for testing email sending functionality
"""


import datetime
import logging
from collections import namedtuple
from copy import deepcopy
from unittest.mock import Mock, patch

import attr
import ddt
from zoneinfo import ZoneInfo
from django.conf import settings
from django.contrib.auth.models import User  # lint-amnesty, pylint: disable=imported-auth-user
from django.db.models import Max
from edx_ace.channel import ChannelMap, ChannelType
from edx_ace.test_utils import StubPolicy, patch_policies
from edx_ace.utils.date import serialize
from freezegun import freeze_time
from opaque_keys.edx.keys import CourseKey

from common.djangoapps.course_modes.models import CourseMode
from common.djangoapps.course_modes.tests.factories import CourseModeFactory
from lms.djangoapps.courseware.models import DynamicUpgradeDeadlineConfiguration
from lms.djangoapps.commerce.models import CommerceConfiguration
from openedx.core.djangoapps.schedules import resolvers, tasks
from openedx.core.djangoapps.schedules.resolvers import _get_datetime_beginning_of_day
from openedx.core.djangoapps.schedules.tests.factories import ScheduleConfigFactory, ScheduleFactory
from openedx.core.djangoapps.site_configuration.tests.factories import SiteConfigurationFactory, SiteFactory
from openedx.core.djangoapps.theming.tests.test_util import with_comprehensive_theme
from openedx.core.djangoapps.waffle_utils.testutils import WAFFLE_TABLES
from openedx.core.djangolib.testing.utils import FilteredQueryCountMixin
from common.djangoapps.student.models import CourseEnrollment
from common.djangoapps.student.tests.factories import UserFactory

SITE_QUERY = 1  # django_site
SITE_CONFIG_QUERY = 1  # site_configuration_siteconfiguration

SCHEDULES_QUERY = 1  # schedules_schedule
COURSE_MODES_QUERY = 1  # course_modes_coursemode

GLOBAL_DEADLINE_QUERY = 1  # courseware_dynamicupgradedeadlineconfiguration
ORG_DEADLINE_QUERY = 1  # courseware_orgdynamicupgradedeadlineconfiguration
COURSE_DEADLINE_QUERY = 1  # courseware_coursedynamicupgradedeadlineconfiguration
COMMERCE_CONFIG_QUERY = 1  # commerce_commerceconfiguration

USER_QUERY = 1  # auth_user
THEME_PREVIEW_QUERY = 1
THEME_QUERY = 1  # theming_sitetheme
SCHEDULE_CONFIG_QUERY = 1  # schedules_scheduleconfig

NUM_QUERIES_SITE_SCHEDULES = (
    SITE_QUERY +
    SITE_CONFIG_QUERY +
    THEME_QUERY +
    SCHEDULES_QUERY
)

NUM_QUERIES_FIRST_MATCH = (
    NUM_QUERIES_SITE_SCHEDULES
    + GLOBAL_DEADLINE_QUERY
    + ORG_DEADLINE_QUERY
    + COURSE_DEADLINE_QUERY
    + COMMERCE_CONFIG_QUERY
)

NUM_QUERIES_PER_MESSAGE_DELIVERY = (
    SITE_QUERY +
    SCHEDULE_CONFIG_QUERY +
    USER_QUERY +
    THEME_PREVIEW_QUERY +
    THEME_QUERY
)

LOG = logging.getLogger(__name__)


ExperienceTest = namedtuple('ExperienceTest', 'experience offset email_sent')


@ddt.ddt
@freeze_time('2017-08-01 00:00:00', tz_offset=0, tick=True)
class ScheduleSendEmailTestMixin(FilteredQueryCountMixin):  # lint-amnesty, pylint: disable=missing-class-docstring

    __test__ = False

    ENABLED_CACHES = ['default']

    queries_deadline_for_each_course = False
    consolidates_emails_for_learner = False

    def setUp(self):
        super().setUp()

        site = SiteFactory.create()
        self.site_config = SiteConfigurationFactory.create(site=site)
        ScheduleConfigFactory.create(site=self.site_config.site)

        DynamicUpgradeDeadlineConfiguration.objects.create(enabled=True)
        CommerceConfiguration.objects.create(checkout_on_ecommerce_service=True)

        self._courses_with_verified_modes = set()

    def _calculate_bin_for_user(self, user):
        return user.id % self.task.num_bins

    def _next_user_id(self):
        """
        Get the next user ID which is a multiple of the bin count and greater
        than the current largest user ID.  Avoids intermittent ID collisions
        with the user created in ModuleStoreTestCase.setUp().
        """
        max_user_id = User.objects.aggregate(Max('id'))['id__max']
        if max_user_id is None:
            max_user_id = 0
        num_bins = self.task.num_bins
        return max_user_id + num_bins - (max_user_id % num_bins)

    def _get_dates(self, offset=None):  # lint-amnesty, pylint: disable=missing-function-docstring
        current_day = _get_datetime_beginning_of_day(datetime.datetime.now(ZoneInfo("UTC")))
        offset = offset or self.expected_offsets[0]
        target_day = current_day + datetime.timedelta(days=offset)
        if self.resolver.schedule_date_field == 'upgrade_deadline':
            upgrade_deadline = target_day
        else:
            upgrade_deadline = current_day + datetime.timedelta(days=7)
        return current_day, offset, target_day, upgrade_deadline

    def _get_template_overrides(self):
        templates_override = deepcopy(settings.TEMPLATES)
        templates_override[0]['OPTIONS']['string_if_invalid'] = "TEMPLATE WARNING - MISSING VARIABLE [%s]"
        return templates_override

    def _schedule_factory(self, offset=None, **factory_kwargs):  # lint-amnesty, pylint: disable=missing-function-docstring
        _, _, target_day, upgrade_deadline = self._get_dates(offset=offset)
        factory_kwargs.setdefault('start_date', target_day)
        factory_kwargs.setdefault('upgrade_deadline', upgrade_deadline)
        factory_kwargs.setdefault('enrollment__course__self_paced', True)
        # Make all schedules in the same course
        factory_kwargs.setdefault('enrollment__course__run', '2012_Fall')
        if hasattr(self, 'experience_type'):
            factory_kwargs.setdefault('experience__experience_type', self.experience_type)
        schedule = ScheduleFactory(**factory_kwargs)
        course_id = schedule.enrollment.course_id
        if course_id not in self._courses_with_verified_modes:
            CourseModeFactory(
                course_id=course_id,
                mode_slug=CourseMode.VERIFIED,
                expiration_datetime=datetime.datetime.now(ZoneInfo("UTC")) + datetime.timedelta(days=30),
            )
            self._courses_with_verified_modes.add(course_id)
        return schedule

    def _update_schedule_config(self, schedule_config_kwargs):
        """
        Updates the schedule config model by making sure the new entry
        has a later timestamp.
        """
        later_time = datetime.datetime.now(ZoneInfo("UTC")) + datetime.timedelta(minutes=1)
        with freeze_time(later_time):
            ScheduleConfigFactory.create(**schedule_config_kwargs)

    def test_command_task_binding(self):
        assert self.command.async_send_task == self.task

    def test_handle(self):
        with patch.object(self.command, 'async_send_task') as mock_send:
            test_day = datetime.datetime(2017, 8, 1, tzinfo=ZoneInfo("UTC"))
            self.command().handle(date='2017-08-01', site_domain_name=self.site_config.site.domain)

            for offset in self.expected_offsets:
                mock_send.enqueue.assert_any_call(
                    self.site_config.site,
                    test_day,
                    offset,
                    None
                )

    @patch.object(tasks, 'ace')
    def test_resolver_send(self, mock_ace):
        current_day, offset, target_day, _ = self._get_dates()
        with patch.object(self.task, 'apply_async') as mock_apply_async:
            self.task.enqueue(self.site_config.site, current_day, offset)
        mock_apply_async.assert_any_call(
            (self.site_config.site.id, serialize(target_day), offset, 0, None),
            retry=False,
        )
        mock_apply_async.assert_any_call(
            (self.site_config.site.id, serialize(target_day), offset, self.task.num_bins - 1, None),
            retry=False,
        )
        assert not mock_ace.send.called

    @ddt.data(1, 10, 100)
    @patch.object(tasks, 'ace')
    @patch.object(resolvers, 'set_custom_attribute')
    def test_schedule_bin(self, schedule_count, mock_attribute, mock_ace):
        with patch.object(self.task, 'async_send_task') as mock_schedule_send:
            current_day, offset, target_day, upgrade_deadline = self._get_dates()  # lint-amnesty, pylint: disable=unused-variable
            schedules = [
                self._schedule_factory() for _ in range(schedule_count)
            ]

            bins_in_use = frozenset((self._calculate_bin_for_user(s.enrollment.user)) for s in schedules)
            is_first_match = True
            target_day_str = serialize(target_day)

            for b in range(self.task.num_bins):
                LOG.debug('Checking bin %d', b)
                expected_queries = NUM_QUERIES_SITE_SCHEDULES
                if b in bins_in_use:
                    if is_first_match:
                        expected_queries = (
                            # Since this is the first match, we need to cache all of the config models, so we run a
                            # query for each of those...
                            NUM_QUERIES_FIRST_MATCH
                            + COURSE_MODES_QUERY  # to cache the course modes for this course
                        )
                        is_first_match = False

                with self.assertNumQueries(expected_queries, table_ignorelist=WAFFLE_TABLES):
                    self.task().apply(kwargs=dict(
                        site_id=self.site_config.site.id, target_day_str=target_day_str, day_offset=offset, bin_num=b,
                    ))

                num_schedules = mock_attribute.call_args[0][1]
                if b in bins_in_use:
                    assert num_schedules > 0
                else:
                    assert num_schedules == 0

            assert mock_schedule_send.apply_async.call_count == schedule_count
            assert not mock_ace.send.called

    def test_no_course_overview(self):
        current_day, offset, target_day, upgrade_deadline = self._get_dates()  # lint-amnesty, pylint: disable=unused-variable
        # Don't use CourseEnrollmentFactory since it creates a course overview
        enrollment = CourseEnrollment.objects.create(
            course_id=CourseKey.from_string('edX/toy/Not_2012_Fall'),
            user=UserFactory.create(),
        )
        self._schedule_factory(enrollment=enrollment)

        with patch.object(self.task, 'async_send_task') as mock_schedule_send:
            for bin_num in range(self.task().num_bins):
                self.task().apply(kwargs=dict(
                    site_id=self.site_config.site.id,
                    target_day_str=serialize(target_day),
                    day_offset=offset,
                    bin_num=bin_num,
                ))

        # There is no database constraint that enforces that enrollment.course_id points
        # to a valid CourseOverview object. However, in that case, schedules isn't going
        # to attempt to address it, and will instead simply skip those users.
        # This happens 'transparently' because django generates an inner-join between
        # enrollment and course_overview, and thus will skip any rows where course_overview
        # is null.
        assert mock_schedule_send.apply_async.call_count == 0

    @ddt.data(True, False)
    @patch.object(tasks, 'ace')
    @patch.object(tasks, 'Message')
    def test_deliver_config(self, is_enabled, mock_message, mock_ace):
        user = UserFactory.create()
        schedule_config_kwargs = {
            'site': self.site_config.site,
            self.deliver_config: is_enabled,
        }
        self._update_schedule_config(schedule_config_kwargs)

        mock_message.from_string.return_value.recipient.lms_user_id = user.id
        mock_msg = Mock()
        self.deliver_task(self.site_config.site.id, mock_msg)
        if is_enabled:
            assert mock_ace.send.called
        else:
            assert not mock_ace.send.called

    @ddt.data(True, False)
    def test_enqueue_config(self, is_enabled):
        schedule_config_kwargs = {
            'site': self.site_config.site,
            self.enqueue_config: is_enabled,
        }
        self._update_schedule_config(schedule_config_kwargs)

        current_datetime = datetime.datetime(2017, 8, 1, tzinfo=ZoneInfo("UTC"))
        with patch.object(self.task, 'apply_async') as mock_apply_async:
            self.task.enqueue(self.site_config.site, current_datetime, 3)

        if is_enabled:
            assert mock_apply_async.called
        else:
            assert not mock_apply_async.called

    @patch.object(tasks, 'ace')
    @ddt.data(
        ((['filtered_org'], [], 1)),
        (([], ['filtered_org'], 2))
    )
    @ddt.unpack
    def test_site_config(self, this_org_list, other_org_list, expected_message_count, mock_ace):
        filtered_org = 'filtered_org'
        unfiltered_org = 'unfiltered_org'
        this_config = SiteConfigurationFactory.create(
            site_values={'course_org_filter': this_org_list}
        )
        other_config = SiteConfigurationFactory.create(
            site_values={'course_org_filter': other_org_list}
        )

        for config in (this_config, other_config):
            ScheduleConfigFactory.create(site=config.site)

        user1 = UserFactory.create(id=self._next_user_id())
        user2 = UserFactory.create(id=user1.id + self.task.num_bins)
        current_day, offset, target_day, upgrade_deadline = self._get_dates()  # lint-amnesty, pylint: disable=unused-variable

        self._schedule_factory(
            enrollment__course__org=filtered_org,
            enrollment__user=user1,
        )
        self._schedule_factory(
            enrollment__course__org=unfiltered_org,
            enrollment__user=user1,
        )
        self._schedule_factory(
            enrollment__course__org=unfiltered_org,
            enrollment__user=user2,
        )

        with patch.object(self.task, 'async_send_task') as mock_schedule_send:
            self.task().apply(kwargs=dict(
                site_id=this_config.site.id, target_day_str=serialize(target_day), day_offset=offset, bin_num=0
            ))

        assert mock_schedule_send.apply_async.call_count == expected_message_count
        assert not mock_ace.send.called

    @ddt.data(True, False)
    def test_course_end(self, has_course_ended):
        user1 = UserFactory.create(id=self._next_user_id())
        current_day, offset, target_day, upgrade_deadline = self._get_dates()  # lint-amnesty, pylint: disable=unused-variable

        end_date_offset = -2 if has_course_ended else 2
        self._schedule_factory(
            enrollment__user=user1,
            enrollment__course__start=current_day - datetime.timedelta(days=30),
            enrollment__course__end=current_day + datetime.timedelta(days=end_date_offset)
        )

        with patch.object(self.task, 'async_send_task') as mock_schedule_send:
            self.task().apply(kwargs=dict(
                site_id=self.site_config.site.id, target_day_str=serialize(target_day), day_offset=offset, bin_num=0,
            ))

        if has_course_ended:
            assert not mock_schedule_send.apply_async.called
        else:
            assert mock_schedule_send.apply_async.called

    @patch.object(tasks, 'ace')
    def test_multiple_target_schedules(self, mock_ace):
        user = UserFactory.create()
        current_day, offset, target_day, upgrade_deadline = self._get_dates()  # lint-amnesty, pylint: disable=unused-variable
        num_courses = 3
        for course_index in range(num_courses):
            self._schedule_factory(
                enrollment__user=user,
                enrollment__course__id=CourseKey.from_string(f'edX/toy/course{course_index}')
            )

        # 2 queries per course, one for the course opt out and one for the course modes
        # one query for course modes for the first schedule if we aren't checking the deadline for each course
        additional_course_queries = (num_courses * 2) - 1 if self.queries_deadline_for_each_course else 1
        expected_query_count = NUM_QUERIES_FIRST_MATCH + additional_course_queries
        with self.assertNumQueries(expected_query_count, table_ignorelist=WAFFLE_TABLES):
            with patch.object(self.task, 'async_send_task') as mock_schedule_send:
                self.task().apply(kwargs=dict(
                    site_id=self.site_config.site.id, target_day_str=serialize(target_day), day_offset=offset,
                    bin_num=self._calculate_bin_for_user(user),
                ))

        expected_call_count = 1 if self.consolidates_emails_for_learner else num_courses
        assert mock_schedule_send.apply_async.call_count == expected_call_count
        assert not mock_ace.send.called

    @ddt.data(
        1, 10
    )
    def test_templates(self, message_count):
        for offset in self.expected_offsets:
            self._assert_template_for_offset(offset, message_count)
            self.clear_caches()

    def _assert_template_for_offset(self, offset, message_count):  # lint-amnesty, pylint: disable=missing-function-docstring
        current_day, offset, target_day, upgrade_deadline = self._get_dates(offset)  # lint-amnesty, pylint: disable=unused-variable

        user = UserFactory.create()
        for course_index in range(message_count):
            self._schedule_factory(
                offset=offset,
                enrollment__user=user,
                enrollment__course__id=CourseKey.from_string(f'edX/toy/course{course_index}')
            )

        patch_policies(self, [StubPolicy([ChannelType.PUSH])])

        mock_channel = Mock(
            channel_type=ChannelType.EMAIL,
            action_links=[],
            tracker_image_sources=[],
        )

        channel_map = ChannelMap([
            ['sailthru', mock_channel],
        ])

        sent_messages = []
        with self.settings(TEMPLATES=self._get_template_overrides()):
            with patch.object(self.task, 'async_send_task') as mock_schedule_send:
                mock_schedule_send.apply_async = lambda args, *_a, **_kw: sent_messages.append(args)

                num_expected_queries = NUM_QUERIES_FIRST_MATCH
                if self.queries_deadline_for_each_course:
                    # one query per course for opt-out and one for course modes
                    num_expected_queries += (message_count * 2) - 1
                else:
                    num_expected_queries += 1

                with self.assertNumQueries(num_expected_queries, table_ignorelist=WAFFLE_TABLES):
                    self.task().apply(kwargs=dict(
                        site_id=self.site_config.site.id, target_day_str=serialize(target_day), day_offset=offset,
                        bin_num=self._calculate_bin_for_user(user),
                    ))
            num_expected_messages = 1 if self.consolidates_emails_for_learner else message_count
            assert len(sent_messages) == num_expected_messages

            with self.assertNumQueries(NUM_QUERIES_PER_MESSAGE_DELIVERY):
                with patch('openedx.core.djangoapps.schedules.tasks.segment.track') as mock_segment_track:
                    with patch('edx_ace.channel.channels', return_value=channel_map):
                        self.deliver_task(*sent_messages[0])
                        assert mock_segment_track.call_count == 1

            assert mock_channel.deliver.call_count == 1
            for (_name, (_msg, email), _kwargs) in mock_channel.deliver.mock_calls:
                for template in attr.astuple(email):
                    assert 'TEMPLATE WARNING' not in template
                    assert '{{' not in template
                    assert '}}' not in template

            return mock_channel.deliver.mock_calls

    def _check_if_email_sent_for_experience(self, test_config):  # lint-amnesty, pylint: disable=missing-function-docstring
        current_day, offset, target_day, _ = self._get_dates(offset=test_config.offset)  # lint-amnesty, pylint: disable=unused-variable

        kwargs = {
            'offset': offset
        }
        if test_config.experience is None:
            kwargs['experience'] = None
        else:
            kwargs['experience__experience_type'] = test_config.experience
        schedule = self._schedule_factory(**kwargs)

        with patch.object(tasks, 'ace') as mock_ace:
            self.task().apply(kwargs=dict(
                site_id=self.site_config.site.id, target_day_str=serialize(target_day), day_offset=offset,
                bin_num=self._calculate_bin_for_user(schedule.enrollment.user),
            ))

            assert mock_ace.send.called == test_config.email_sent

    @with_comprehensive_theme('red-theme')
    def test_templates_with_theme(self):
        calls_to_deliver = self._assert_template_for_offset(self.expected_offsets[0], 1)

        _name, (_msg, email), _kwargs = calls_to_deliver[0]
        assert 'TEST RED THEME MARKER' in email.body_html
