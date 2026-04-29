"""
Base file for testing schedules with upsell
"""


import datetime
import itertools
from collections import namedtuple
from unittest.mock import PropertyMock, patch

import ddt
from edx_ace.message import Message
from edx_ace.utils.date import serialize
from freezegun import freeze_time

from lms.djangoapps.courseware.models import DynamicUpgradeDeadlineConfiguration


@ddt.ddt
@freeze_time('2017-08-01 00:00:00', tz_offset=0, tick=True)
class ScheduleUpsellTestMixin:  # pylint: disable=missing-class-docstring
    UpsellTestCase = namedtuple('UpsellTestCase', 'set_deadline, deadline_offset, expect_upsell')

    def _setup_schedule_and_dates(self, set_deadline=True, deadline_offset=7):
        """
        Creates and returns a schedule according to the provided upsell deadline values.
        Also returns the offset and target_day as computed for messaging.
        """
        current_day, offset, target_day, _ = self._get_dates()

        upgrade_deadline = None
        if set_deadline:
            upgrade_deadline = current_day + datetime.timedelta(days=deadline_offset)

        schedule = self._schedule_factory(
            upgrade_deadline=upgrade_deadline
        )
        return schedule, offset, target_day

    def _send_message_task(self, schedule, offset, target_day):
        """
        Calls the task for sending a message to the given schedule and for the given
        offset and target_day. Returns the message that would have been sent.
        """
        sent_messages = []
        with patch.object(self.task, 'async_send_task') as mock_schedule_send:
            mock_schedule_send.apply_async = lambda args, *_a, **_kw: sent_messages.append(args[1])
            self.task().apply(kwargs=dict(
                site_id=self.site_config.site.id, target_day_str=serialize(target_day), day_offset=offset,
                bin_num=self._calculate_bin_for_user(schedule.enrollment.user),
            ))
        assert len(sent_messages) == 1
        return Message.from_string(sent_messages[0])

    def _contains_upsell(self, message):
        """
        Returns whether the given message would contain upsell text.
        """
        return message.context["show_upsell"]

    @ddt.data(
        *itertools.product(
            (True, False),  # enable DynamicUpgradeDeadlineConfiguration
            (
                UpsellTestCase(set_deadline=False, deadline_offset=None, expect_upsell=False),  # no deadline
                UpsellTestCase(set_deadline=True, deadline_offset=-7, expect_upsell=False),  # deadline expired
                UpsellTestCase(set_deadline=True, deadline_offset=7, expect_upsell=True),  # deadline in future
            )
        )
    )
    @ddt.unpack
    def test_upsell(self, enable_config, testcase):
        # Make sure the new entry in the config model has a time
        # later than the frozen time for it to be effective.
        with freeze_time('2017-08-01 01:00:00'):
            DynamicUpgradeDeadlineConfiguration.objects.create(enabled=enable_config)

        schedule, offset, target_day = self._setup_schedule_and_dates(
            set_deadline=testcase.set_deadline,
            deadline_offset=testcase.deadline_offset,
        )
        message = self._send_message_task(schedule, offset, target_day)

        found_upsell = self._contains_upsell(message)
        expect_upsell = enable_config and testcase.expect_upsell
        assert found_upsell == expect_upsell

    @ddt.data('es', 'es-es', 'es-419')
    def test_upsell_translated(self, course_language):
        schedule, offset, target_day = self._setup_schedule_and_dates()

        with patch(
                'openedx.core.djangoapps.content.course_overviews.models.CourseOverview.closest_released_language',
                new_callable=PropertyMock
        ) as mock_course_language:
            mock_course_language.return_value = course_language
            message = self._send_message_task(schedule, offset, target_day)

        assert message.context['user_schedule_upgrade_deadline_time'] == '8 de agosto de 2017'
