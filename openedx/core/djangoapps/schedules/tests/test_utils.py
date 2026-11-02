"""
Tests for schedules utils
"""

import datetime

import ddt
from zoneinfo import ZoneInfo
from xmodule.modulestore.tests.django_utils import SharedModuleStoreTestCase
from xmodule.modulestore.tests.factories import CourseFactory

from common.djangoapps.course_modes.models import CourseMode
from openedx.core.djangoapps.schedules.models import Schedule
from openedx.core.djangoapps.schedules.tests.factories import ScheduleConfigFactory
from openedx.core.djangoapps.schedules.utils import reset_self_paced_schedule
from openedx.core.djangolib.testing.utils import skip_unless_lms
from common.djangoapps.student.tests.factories import CourseEnrollmentFactory


@ddt.ddt
@skip_unless_lms
class ResetSelfPacedScheduleTests(SharedModuleStoreTestCase):
    """Tests for reset_self_paced_schedule"""
    def create_schedule(self, enrollment_offset=0, course_start_offset=-100):
        """Makes a course, schedule, and enrollment ready to test"""
        # pylint: disable=attribute-defined-outside-init
        self.config = ScheduleConfigFactory()

        start = datetime.datetime.now(ZoneInfo("UTC")) + datetime.timedelta(days=course_start_offset)
        self.course = CourseFactory.create(start=start, self_paced=True)

        self.enrollment = CourseEnrollmentFactory(
            course_id=self.course.id,
            mode=CourseMode.AUDIT,
        )
        self.enrollment.created = start + datetime.timedelta(days=enrollment_offset)
        self.enrollment.save()

        self.schedule = self.enrollment.schedule
        self.schedule.start_date = self.enrollment.created
        self.schedule.save()

        self.user = self.enrollment.user

    def test_reset_to_now(self):
        self.create_schedule()
        original_start = self.schedule.start_date

        with self.assertNumQueries(3):
            reset_self_paced_schedule(self.user, self.course.id, use_enrollment_date=False)

        self.schedule.refresh_from_db()
        assert self.schedule.start_date > original_start

    @ddt.data(
        (-1, 0),  # enrolled before course started (will reset to start date)
        (1, 1),   # enrolled after course started (will reset to enroll date)
    )
    @ddt.unpack
    def test_reset_to_start_date(self, offset, expected_offset):
        self.create_schedule(enrollment_offset=offset)
        expected_start = self.course.start + datetime.timedelta(days=expected_offset)

        with self.assertNumQueries(3):
            reset_self_paced_schedule(self.user, self.course.id, use_enrollment_date=True)

        self.schedule.refresh_from_db()
        assert self.schedule.start_date.replace(microsecond=0) == expected_start.replace(microsecond=0)

    def test_safe_without_schedule(self):
        """ Just ensure that we don't raise exceptions or create any schedules """
        self.create_schedule()
        self.schedule.delete()

        reset_self_paced_schedule(self.user, self.course.id, use_enrollment_date=False)
        reset_self_paced_schedule(self.user, self.course.id, use_enrollment_date=True)

        assert Schedule.objects.count() == 0

    @ddt.data(True, False)
    def test_reset_before_course_starts(self, use_enrollment_date):
        self.create_schedule(enrollment_offset=-5, course_start_offset=5)  # starts in 5 days, enrollment is now

        reset_self_paced_schedule(self.user, self.course.id, use_enrollment_date=use_enrollment_date)

        self.schedule.refresh_from_db()
        assert self.schedule.start_date == self.enrollment.course.start
