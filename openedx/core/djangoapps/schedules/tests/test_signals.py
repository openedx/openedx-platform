"""
Tests for schedules signals
"""


import datetime
from unittest.mock import patch

import ddt
import pytest
from zoneinfo import ZoneInfo

from common.djangoapps.course_modes.models import CourseMode
from common.djangoapps.course_modes.tests.factories import CourseModeFactory
from lms.djangoapps.courseware.models import DynamicUpgradeDeadlineConfiguration
from openedx.core.djangoapps.content.course_overviews.models import CourseOverview
from openedx.core.djangoapps.schedules.models import ScheduleExperience
from openedx.core.djangoapps.site_configuration.tests.factories import SiteFactory
from openedx.core.djangolib.testing.utils import skip_unless_lms
from common.djangoapps.student.models import CourseEnrollment
from common.djangoapps.student.tests.factories import CourseEnrollmentFactory
from xmodule.modulestore import ModuleStoreEnum  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase, SharedModuleStoreTestCase  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.modulestore.tests.factories import CourseFactory  # lint-amnesty, pylint: disable=wrong-import-order

from ..models import Schedule
from ..tests.factories import ScheduleConfigFactory


@ddt.ddt
@skip_unless_lms
class CreateScheduleTests(SharedModuleStoreTestCase):  # lint-amnesty, pylint: disable=missing-class-docstring

    def assert_schedule_created(self, is_self_paced=True, experience_type=ScheduleExperience.EXPERIENCES.default):
        """
        Checks whether schedule is created and that it is created with the correct
        experience type
        """
        course = _create_course_run(self_paced=is_self_paced)
        enrollment = CourseEnrollmentFactory(
            course_id=course.id,
            mode=CourseMode.AUDIT,
        )
        assert enrollment.schedule is not None
        assert enrollment.schedule.upgrade_deadline is None
        assert enrollment.schedule.experience.experience_type == experience_type

    def assert_schedule_not_created(self):  # lint-amnesty, pylint: disable=missing-function-docstring
        course = _create_course_run(self_paced=True)
        enrollment = CourseEnrollmentFactory(
            course_id=course.id,
            mode=CourseMode.AUDIT,
        )
        with pytest.raises(Schedule.DoesNotExist):
            enrollment.schedule  # lint-amnesty, pylint: disable=pointless-statement

    def test_create_schedule(self):
        self.assert_schedule_created()

    @patch.object(CourseOverview, '_get_course_has_highlights', return_value=True)
    def test_schedule_config_creation_enabled_instructor_paced(self, _mock_highlights):
        self.assert_schedule_created(is_self_paced=False, experience_type=ScheduleExperience.EXPERIENCES.course_updates)

    @patch.object(CourseOverview, '_get_course_has_highlights', return_value=True)
    def test_create_schedule_course_updates_experience(self, _mock_highlights):
        self.assert_schedule_created(experience_type=ScheduleExperience.EXPERIENCES.course_updates)

    @patch('openedx.core.djangoapps.schedules.signals.log.exception')
    @patch('openedx.core.djangoapps.schedules.signals.Schedule.objects.create')
    def test_create_schedule_error(self, mock_create_schedule, mock_log):
        mock_create_schedule.side_effect = ValueError('Fake error')
        self.assert_schedule_not_created()
        mock_log.assert_called_once()
        assert 'Encountered error in creating a Schedule for CourseEnrollment' in mock_log.call_args[0][0]

    def test_course_start_date_in_future(self):
        """
        Test that the schedule start date will be set to course's start date
        if course starts after enrollment
        """
        course = _create_course_run(self_paced=True, start_day_offset=5)  # course starts in future
        enrollment = CourseEnrollmentFactory(course_id=course.id, mode=CourseMode.AUDIT)
        assert _strip_secs(enrollment.schedule.start_date) == _strip_secs(course.start)

    def test_course_already_started(self):
        """
        Test that the schedule start date will be set to the date enrollment was
        created if course has already started
        """
        course = _create_course_run(self_paced=True, start_day_offset=-5)  # course already started
        enrollment = CourseEnrollmentFactory(course_id=course.id, mode=CourseMode.AUDIT)
        assert _strip_secs(enrollment.schedule.start_date) == _strip_secs(enrollment.created)


@ddt.ddt
@skip_unless_lms
class UpdateScheduleTests(ModuleStoreTestCase):  # lint-amnesty, pylint: disable=missing-class-docstring
    ENABLED_SIGNALS = ['course_published']
    VERIFICATION_DEADLINE_DAYS = 14

    def setUp(self):
        super().setUp()
        self.site = SiteFactory.create()
        ScheduleConfigFactory.create(site=self.site)
        DynamicUpgradeDeadlineConfiguration.objects.create(enabled=True, deadline_days=self.VERIFICATION_DEADLINE_DAYS)

    def assert_schedule_dates(self, schedule, expected_start):
        assert _strip_secs(schedule.start_date) == _strip_secs(expected_start)
        deadline_delta = datetime.timedelta(days=self.VERIFICATION_DEADLINE_DAYS)
        assert _strip_secs(schedule.upgrade_deadline) == _strip_secs(expected_start) + deadline_delta

    def test_updated_when_course_not_started(self):
        course = _create_course_run(self_paced=True, start_day_offset=5)  # course starts in future
        enrollment = CourseEnrollmentFactory(course_id=course.id, mode=CourseMode.AUDIT)
        self.assert_schedule_dates(enrollment.schedule, enrollment.course.start)

        course.start = course.start + datetime.timedelta(days=3)  # new course start changes to another future date
        self.update_course(course, ModuleStoreEnum.UserID.test)
        enrollment = CourseEnrollment.objects.get(id=enrollment.id)
        self.assert_schedule_dates(enrollment.schedule, course.start)  # start set to new course start

    def test_updated_when_course_already_started(self):
        course = _create_course_run(self_paced=True, start_day_offset=-5)  # course starts in past
        enrollment = CourseEnrollmentFactory(course_id=course.id, mode=CourseMode.AUDIT)
        self.assert_schedule_dates(enrollment.schedule, enrollment.created)

        course.start = course.start + datetime.timedelta(days=3)  # new course start changes to another future date
        self.update_course(course, ModuleStoreEnum.UserID.test)
        enrollment = CourseEnrollment.objects.get(id=enrollment.id)
        self.assert_schedule_dates(enrollment.schedule, course.start)  # start set to new course start

    def test_updated_when_new_start_in_past(self):
        course = _create_course_run(self_paced=True, start_day_offset=5)  # course starts in future
        enrollment = CourseEnrollmentFactory(course_id=course.id, mode=CourseMode.AUDIT)
        previous_start = enrollment.course.start
        self.assert_schedule_dates(enrollment.schedule, previous_start)

        course.start = course.start + datetime.timedelta(days=-10)  # new course start changes to a past date
        self.update_course(course, ModuleStoreEnum.UserID.test)
        enrollment = CourseEnrollment.objects.get(id=enrollment.id)
        self.assert_schedule_dates(enrollment.schedule, course.start)  # start set to new course start


@skip_unless_lms
class ResetScheduleTests(SharedModuleStoreTestCase):  # lint-amnesty, pylint: disable=missing-class-docstring
    def setUp(self):
        super().setUp()

        self.config = ScheduleConfigFactory()
        self.course = _create_course_run(self_paced=True)
        self.enrollment = CourseEnrollmentFactory(
            course_id=self.course.id,
            mode=CourseMode.AUDIT,
            is_active=False,
        )
        self.schedule = self.enrollment.schedule
        self.user = self.enrollment.user

    def test_schedule_is_reset_after_enrollment_change(self):
        """ Test that an update in enrollment causes a schedule reset. """
        original_start = self.schedule.start_date

        CourseEnrollment.enroll(self.user, self.course.id, mode=CourseMode.VERIFIED)

        self.schedule.refresh_from_db()
        assert self.schedule.start_date > original_start
        # should have been reset to current time

    def test_schedule_is_reset_to_availability_date(self):
        """ Test that a switch to audit enrollment resets to the availability date, not current time. """
        original_start = self.schedule.start_date

        # Switch to verified, confirm we change start date
        CourseEnrollment.enroll(self.user, self.course.id, mode=CourseMode.VERIFIED)
        self.schedule.refresh_from_db()
        assert self.schedule.start_date != original_start

        CourseEnrollment.unenroll(self.user, self.course.id)

        # Switch back to audit, confirm we change back to original availability date
        CourseEnrollment.enroll(self.user, self.course.id, mode=CourseMode.AUDIT)
        self.schedule.refresh_from_db()
        assert self.schedule.start_date == original_start


def _create_course_run(self_paced=True, start_day_offset=-1):
    """ Create a new course run and course modes.

    Both audit and verified `CourseMode` objects will be created for the course run.
    """
    now = datetime.datetime.now(ZoneInfo("UTC"))
    start = now + datetime.timedelta(days=start_day_offset)
    course = CourseFactory.create(start=start, self_paced=self_paced)

    CourseModeFactory(
        course_id=course.id,
        mode_slug=CourseMode.AUDIT
    )
    CourseModeFactory(
        course_id=course.id,
        mode_slug=CourseMode.VERIFIED,
        expiration_datetime=now + datetime.timedelta(days=100)
    )

    return course


def _strip_secs(timestamp):
    return timestamp.replace(second=0, microsecond=0)
