"""
Tests for `send_segment_events_for_failed_learners` management command.
"""

import random
from datetime import timedelta
from unittest import mock
from unittest.mock import patch

import ddt
from django.core.management import call_command
from django.utils import timezone

from common.djangoapps.student.models import CourseEnrollment
from common.djangoapps.student.tests.factories import UserFactory
from lms.djangoapps.grades.management.commands import send_segment_events_for_failed_learners
from lms.djangoapps.grades.management.commands.send_segment_events_for_failed_learners import (
    EVENT_NAME,
    PAID_ENROLLMENT_MODES,
)
from lms.djangoapps.grades.models import PersistentCourseGrade
from openedx.core.djangoapps.content.course_overviews.tests.factories import CourseOverviewFactory
from xmodule.modulestore.tests.django_utils import (
    SharedModuleStoreTestCase,  # lint-amnesty, pylint: disable=wrong-import-order
)


@ddt.ddt
class TestSendSegmentEventsForFailedLearnersCommand(SharedModuleStoreTestCase):
    """
    Tests `send_segment_events_for_failed_learners` management command.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.command = send_segment_events_for_failed_learners.Command()
        # we will create enrollments for paid modes plus `audit` mode
        enrollment_modes = PAID_ENROLLMENT_MODES + ['audit']

        cls.course_end = timezone.now() - timedelta(days=31)
        cls.course_overviews = CourseOverviewFactory.create_batch(4, end=cls.course_end)

        # set end date for a course 100 days ago from the current date
        course = cls.course_overviews[2]
        course.end = timezone.now() - timedelta(days=100)
        course.save()

        # set end date to None
        course = cls.course_overviews[3]
        course.end = None
        course.save()

        cls.course_keys = [str(course_overview.id) for course_overview in cls.course_overviews]
        cls.users = [UserFactory.create(username=f'user{idx}') for idx in range(5)]

        for user in cls.users:
            for course_overview in cls.course_overviews:
                CourseEnrollment.enroll(user, course_overview.id, mode=random.choice(enrollment_modes))
                params = [
                    {
                        "user_id": user.id,
                        "course_id": course_overview.id,
                        "course_version": "Alice",
                        "percent_grade": 0.0,
                        "letter_grade": "",
                        "passed_timestamp": None,
                    },
                    {
                        "user_id": user.id,
                        "course_id": course_overview.id,
                        "course_version": "Bob",
                        "percent_grade": 77.7,
                        "letter_grade": "Great job",
                        "passed_timestamp": timezone.now() - timedelta(days=1),
                    },
                ]
                # randomly create passed and failed grades
                PersistentCourseGrade.objects.create(**random.choice(params))

    def construct_event_call_data(self):
        """
        Construct segment event call data for verification.
        """
        event_call_data = []
        for course in self.command.get_courses():
            for enrollment_and_user_ids in self.command.get_failed_enrollment_and_user_ids(course):
                for failed_enrollment_id, failed_user_id in enrollment_and_user_ids:
                    event_call_data.append([
                        failed_user_id,
                        EVENT_NAME,
                        {
                            'LMS_ENROLLMENT_ID': failed_enrollment_id,
                            'COURSE_TITLE': course.display_name,
                            'COURSE_ORG_NAME': course.org,
                        }
                    ])
        return event_call_data

    def test_get_courses(self):
        """
        Verify that `get_courses` method returns correct courses.
        """
        courses = self.command.get_courses()
        assert len(courses) == 2
        for index in range(2):
            assert courses[index].id == self.course_overviews[index].id
            assert self.course_end.date() == courses[index].end.date() == self.course_overviews[index].end.date()

    def test_get_course_failed_user_ids(self):
        """
        Verify that `get_failed_enrollment_and_user_ids` method returns correct user ids.

        * user id must have a paid enrollment
        * user id must have a failed grade
        """
        for course in self.course_overviews:
            for enrollment_and_user_ids in self.command.get_failed_enrollment_and_user_ids(course):
                for enrollment_id, user_id in enrollment_and_user_ids:
                    # user id must have a paid enrollment
                    assert CourseEnrollment.objects.filter(
                        id=enrollment_id,
                        course_id=course.id,
                        user_id=user_id,
                        mode__in=PAID_ENROLLMENT_MODES,
                        is_active=True
                    ).exists()

                    # user id must have a failed grade
                    assert PersistentCourseGrade.objects.filter(
                        passed_timestamp__isnull=True,
                        course_id=course.id,
                        user_id=user_id,
                    ).exists()

    @patch('lms.djangoapps.grades.management.commands.send_segment_events_for_failed_learners.segment.track')
    def test_command_dry_run(self, segment_track_mock):
        """
        Verify that management command does not fire any segment event in dry run mode.
        """
        call_command(self.command, '--dry-run')
        segment_track_mock.assert_has_calls([])

    @patch('lms.djangoapps.grades.management.commands.send_segment_events_for_failed_learners.segment.track')
    def test_command(self, segment_track_mock):
        """
        Verify that management command fires segment events with correct data.

        * Event should be fired for failed learners only.
        * Event should be fired for paid enrollments only.
        """
        call_command(self.command)
        expected_segment_event_calls = [mock.call(*event_data) for event_data in self.construct_event_call_data()]
        segment_track_mock.assert_has_calls(expected_segment_event_calls)
