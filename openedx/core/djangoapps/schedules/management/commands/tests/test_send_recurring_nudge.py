"""
Tests for send_recurring_nudge management command.
"""


from unittest import skipUnless

import ddt
from django.conf import settings

from openedx.core.djangoapps.schedules import resolvers, tasks
from openedx.core.djangoapps.schedules.management.commands import send_recurring_nudge as nudge
from openedx.core.djangoapps.schedules.management.commands.tests.send_email_base import (
    ExperienceTest,
    ScheduleSendEmailTestMixin,
)
from openedx.core.djangoapps.schedules.management.commands.tests.upsell_base import ScheduleUpsellTestMixin
from openedx.core.djangoapps.schedules.models import ScheduleExperience
from openedx.core.djangolib.testing.utils import CacheIsolationTestCase, skip_unless_lms


@ddt.ddt
@skip_unless_lms
@skipUnless(
    'openedx.core.djangoapps.schedules.apps.SchedulesConfig' in settings.INSTALLED_APPS,
    "Can't test schedules if the app isn't installed",
)
class TestSendRecurringNudge(ScheduleUpsellTestMixin, ScheduleSendEmailTestMixin, CacheIsolationTestCase):  # lint-amnesty, pylint: disable=missing-class-docstring
    __test__ = True

    # pylint: disable=protected-access
    resolver = resolvers.RecurringNudgeResolver
    task = tasks.ScheduleRecurringNudge
    deliver_task = tasks._recurring_nudge_schedule_send
    command = nudge.Command
    deliver_config = 'deliver_recurring_nudge'
    enqueue_config = 'enqueue_recurring_nudge'
    expected_offsets = (-3, -10)

    consolidates_emails_for_learner = True

    @ddt.data(
        ExperienceTest(experience=ScheduleExperience.EXPERIENCES.default, offset=-3, email_sent=True),
        ExperienceTest(experience=ScheduleExperience.EXPERIENCES.default, offset=-10, email_sent=True),
        ExperienceTest(experience=ScheduleExperience.EXPERIENCES.course_updates, offset=-3, email_sent=True),
        ExperienceTest(experience=ScheduleExperience.EXPERIENCES.course_updates, offset=-10, email_sent=False),
        ExperienceTest(experience=None, offset=-3, email_sent=True),
        ExperienceTest(experience=None, offset=-10, email_sent=True),
    )
    def test_nudge_experience(self, test_config):
        self._check_if_email_sent_for_experience(test_config)
