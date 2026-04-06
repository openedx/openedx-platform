"""
Tests for send_course_update management command.
"""


from unittest import skipUnless
from unittest.mock import patch

import ddt
from django.conf import settings
from django.core import mail
from edx_ace.utils.date import serialize

from common.djangoapps.student.tests.factories import CourseEnrollmentFactory
from openedx.core.djangoapps.schedules import resolvers, tasks
from openedx.core.djangoapps.schedules.management.commands import send_course_update as nudge
from openedx.core.djangoapps.schedules.management.commands.tests.send_email_base import (
    ExperienceTest,
    ScheduleSendEmailTestMixin,
)
from openedx.core.djangoapps.schedules.management.commands.tests.upsell_base import ScheduleUpsellTestMixin
from openedx.core.djangoapps.schedules.models import ScheduleExperience
from openedx.core.djangolib.testing.utils import skip_unless_lms
from xmodule.modulestore.tests.django_utils import (
    ModuleStoreTestCase,  # lint-amnesty, pylint: disable=wrong-import-order
)
from xmodule.modulestore.tests.factories import (  # lint-amnesty, pylint: disable=wrong-import-order
    BlockFactory,
    CourseFactory,
)


@ddt.ddt
@skip_unless_lms
@skipUnless(
    'openedx.core.djangoapps.schedules.apps.SchedulesConfig' in settings.INSTALLED_APPS,
    "Can't test schedules if the app isn't installed",
)
class TestSendCourseUpdate(ScheduleUpsellTestMixin, ScheduleSendEmailTestMixin, ModuleStoreTestCase):
    """
    Tests for django management command 'send_course_update'
    """
    __test__ = True

    # pylint: disable=protected-access
    resolver = resolvers.CourseUpdateResolver
    task = tasks.ScheduleCourseUpdate
    deliver_task = tasks._course_update_schedule_send
    command = nudge.Command
    deliver_config = 'deliver_course_update'
    enqueue_config = 'enqueue_course_update'
    expected_offsets = list(range(-7, -77, -7))
    experience_type = ScheduleExperience.EXPERIENCES.course_updates

    queries_deadline_for_each_course = True

    def setUp(self):
        super().setUp()
        self.highlights_patcher = patch('openedx.core.djangoapps.schedules.resolvers.get_week_highlights')
        mock_highlights = self.highlights_patcher.start()
        mock_highlights.return_value = [f'Highlight {num + 1}' for num in range(3)]
        self.addCleanup(self.stop_highlights_patcher)

    def prepare_course_data(self, is_self_paced=True):
        """
        Prepare course data with highlights
        """
        self.highlights_patcher.stop()
        self.highlights_patcher = None

        course = CourseFactory(highlights_enabled_for_messaging=True, self_paced=is_self_paced)
        with self.store.bulk_operations(course.id):
            BlockFactory.create(parent=course, category='chapter', highlights=['highlights'])

        enrollment = CourseEnrollmentFactory(course_id=course.id, user=self.user, mode='audit')
        assert enrollment.schedule.get_experience_type() == ScheduleExperience.EXPERIENCES.course_updates

        _, offset, target_day, _ = self._get_dates(offset=self.expected_offsets[0])
        enrollment.schedule.start_date = target_day
        enrollment.schedule.save()

        return offset, target_day, enrollment

    def stop_highlights_patcher(self):
        """
        Stops the patcher for the get_week_highlights method
        if the patch is still in progress.
        """
        if self.highlights_patcher is not None:
            self.highlights_patcher.stop()

    @ddt.data(
        ExperienceTest(experience=ScheduleExperience.EXPERIENCES.default, offset=expected_offsets[0], email_sent=False),
        ExperienceTest(experience=ScheduleExperience.EXPERIENCES.course_updates, offset=expected_offsets[0], email_sent=True),  # lint-amnesty, pylint: disable=line-too-long
        ExperienceTest(experience=None, offset=expected_offsets[0], email_sent=False),
    )
    def test_schedule_in_different_experience(self, test_config):
        self._check_if_email_sent_for_experience(test_config)

    def test_with_course_data(self):
        offset, target_day, enrollment = self.prepare_course_data()

        with patch.object(tasks, 'ace') as mock_ace:
            self.task().apply(kwargs=dict(
                site_id=self.site_config.site.id,
                target_day_str=serialize(target_day),
                day_offset=offset,
                bin_num=self._calculate_bin_for_user(enrollment.user),
            ))

            assert mock_ace.send.called

    def test_template_for_instructor_led_courses(self):
        """
        Test that InstructorLedCourseUpdate template is picked for instructor led
        courses
        """
        offset, target_day, enrollment = self.prepare_course_data(is_self_paced=False)

        self.task().apply(kwargs=dict(
            site_id=self.site_config.site.id,
            target_day_str=serialize(target_day),
            day_offset=offset,
            bin_num=self._calculate_bin_for_user(enrollment.user),
        ))
        assert f'{enrollment.course.display_name} Weekly Update' == mail.outbox[0].subject
