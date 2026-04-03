"""
Tests for send_upgrade_reminder management command.
"""


import logging
from unittest import skipUnless
from unittest.mock import patch

import ddt
from django.conf import settings
from edx_ace import Message
from edx_ace.utils.date import serialize
from opaque_keys.edx.locator import CourseLocator

from common.djangoapps.course_modes.models import CourseMode
from common.djangoapps.student.tests.factories import UserFactory
from openedx.core.djangoapps.schedules import resolvers, tasks
from openedx.core.djangoapps.schedules.management.commands import send_upgrade_reminder as reminder
from openedx.core.djangoapps.schedules.management.commands.tests.send_email_base import (
    ExperienceTest,
    ScheduleSendEmailTestMixin,
)
from openedx.core.djangoapps.schedules.models import ScheduleExperience
from openedx.core.djangolib.testing.utils import CacheIsolationTestCase, skip_unless_lms

LOG = logging.getLogger(__name__)


@ddt.ddt
@skip_unless_lms
@skipUnless('openedx.core.djangoapps.schedules.apps.SchedulesConfig' in settings.INSTALLED_APPS,
            "Can't test schedules if the app isn't installed")
class TestUpgradeReminder(ScheduleSendEmailTestMixin, CacheIsolationTestCase):  # lint-amnesty, pylint: disable=missing-class-docstring
    __test__ = True

    resolver = resolvers.UpgradeReminderResolver
    task = tasks.ScheduleUpgradeReminder
    deliver_task = tasks._upgrade_reminder_schedule_send  # lint-amnesty, pylint: disable=protected-access
    command = reminder.Command
    deliver_config = 'deliver_upgrade_reminder'
    enqueue_config = 'enqueue_upgrade_reminder'
    expected_offsets = (2,)

    queries_deadline_for_each_course = True
    consolidates_emails_for_learner = True

    @ddt.data(True, False)
    @patch.object(tasks, 'ace')
    def test_verified_learner(self, is_verified, mock_ace):
        current_day, offset, target_day, upgrade_deadline = self._get_dates()  # lint-amnesty, pylint: disable=unused-variable
        schedule = self._schedule_factory(
            enrollment__mode=CourseMode.VERIFIED if is_verified else CourseMode.AUDIT,
        )

        self.task().apply(kwargs=dict(
            site_id=self.site_config.site.id, target_day_str=serialize(target_day), day_offset=offset,
            bin_num=self._calculate_bin_for_user(schedule.enrollment.user),
        ))

        assert mock_ace.send.called == (not is_verified)

    def test_filter_out_verified_schedules(self):
        current_day, offset, target_day, upgrade_deadline = self._get_dates()  # lint-amnesty, pylint: disable=unused-variable

        user = UserFactory.create()
        schedules = [
            self._schedule_factory(
                enrollment__user=user,
                enrollment__course__id=CourseLocator('edX', 'toy', f'Course{i}'),
                enrollment__mode=CourseMode.VERIFIED if i in (0, 3) else CourseMode.AUDIT,
            )
            for i in range(5)
        ]

        sent_messages = []
        with patch.object(self.task, 'async_send_task') as mock_schedule_send:
            mock_schedule_send.apply_async = lambda args, *_a, **_kw: sent_messages.append(args[1])

            self.task().apply(kwargs=dict(
                site_id=self.site_config.site.id, target_day_str=serialize(target_day), day_offset=offset,
                bin_num=self._calculate_bin_for_user(user),
            ))

            messages = [Message.from_string(m) for m in sent_messages]
            assert len(messages) == 1
            message = messages[0]
            self.assertCountEqual(
                message.context['course_ids'],
                [str(schedules[i].enrollment.course.id) for i in (1, 2, 4)]
            )

    @patch.object(tasks, 'ace')
    def test_course_without_verified_mode(self, mock_ace):
        current_day, offset, target_day, upgrade_deadline = self._get_dates()  # lint-amnesty, pylint: disable=unused-variable
        schedule = self._schedule_factory()
        schedule.enrollment.course.modes.filter(mode_slug=CourseMode.VERIFIED).delete()

        self.task().apply(kwargs=dict(
            site_id=self.site_config.site.id, target_day_str=serialize(target_day), day_offset=offset,
            bin_num=self._calculate_bin_for_user(schedule.enrollment.user),
        ))
        assert mock_ace.send.called is False

    @ddt.data(
        ExperienceTest(experience=ScheduleExperience.EXPERIENCES.default, offset=expected_offsets[0], email_sent=True),
        ExperienceTest(experience=ScheduleExperience.EXPERIENCES.course_updates, offset=expected_offsets[0], email_sent=False),  # lint-amnesty, pylint: disable=line-too-long
        ExperienceTest(experience=None, offset=expected_offsets[0], email_sent=True),
    )
    def test_upgrade_reminder_experience(self, test_config):
        self._check_if_email_sent_for_experience(test_config)
