"""
Tests for the recently enrolled messaging within the Dashboard.
"""

import datetime
from zoneinfo import ZoneInfo

import ddt
from django.urls import reverse
from django.utils.timezone import now
from opaque_keys.edx import locator

from common.test.utils import XssTestMixin
from common.djangoapps.student.models import CourseEnrollment, DashboardConfiguration
from common.djangoapps.student.tests.factories import UserFactory
from common.djangoapps.student.views import get_course_enrollments
from common.djangoapps.student.views.dashboard import _get_recently_enrolled_courses
from openedx.core.djangolib.testing.utils import skip_unless_lms
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.modulestore.tests.factories import CourseFactory  # lint-amnesty, pylint: disable=wrong-import-order


@skip_unless_lms
@ddt.ddt
class TestRecentEnrollments(ModuleStoreTestCase, XssTestMixin):
    """
    Unit tests for getting the list of courses for a logged in user
    """
    PASSWORD = 'test'

    def setUp(self):
        """
        Add a student
        """
        super().setUp()
        self.student = UserFactory()
        self.student.set_password(self.PASSWORD)
        self.student.save()

        # Old Course
        old_course_location = locator.CourseLocator('Org0', 'Course0', 'Run0')
        __, enrollment = self._create_course_and_enrollment(old_course_location)
        enrollment.created = datetime.datetime(1900, 12, 31, 0, 0, 0, 0, tzinfo=ZoneInfo("UTC"))
        enrollment.save()

        # New Course
        course_location = locator.CourseLocator('Org1', 'Course1', 'Run1')
        self.course, self.enrollment = self._create_course_and_enrollment(course_location)

    def _create_course_and_enrollment(self, course_location):
        """ Creates a course and associated enrollment. """
        course = CourseFactory.create(
            org=course_location.org,
            number=course_location.course,
            run=course_location.run
        )
        enrollment = CourseEnrollment.enroll(self.student, course.id)
        return course, enrollment

    def _configure_message_timeout(self, timeout):
        """Configure the amount of time the enrollment message will be displayed. """
        config = DashboardConfiguration(recent_enrollment_time_delta=timeout)
        config.save()

    def test_recently_enrolled_courses(self):
        """
        Test if the function for filtering recent enrollments works appropriately.
        """
        self._configure_message_timeout(60)

        # get courses through iterating all courses
        courses_list = list(get_course_enrollments(self.student, None, []))
        assert len(courses_list) == 2

        recent_course_list = _get_recently_enrolled_courses(courses_list)
        assert len(recent_course_list) == 1

    def test_zero_second_delta(self):
        """
        Tests that the recent enrollment list is empty if configured to zero seconds.
        """
        self._configure_message_timeout(0)
        courses_list = list(get_course_enrollments(self.student, None, []))
        assert len(courses_list) == 2

        recent_course_list = _get_recently_enrolled_courses(courses_list)
        assert len(recent_course_list) == 0

    def test_enrollments_sorted_most_recent(self):
        """
        Test that the list of newly created courses are properly sorted to show the most
        recent enrollments first.
        Also test recent enrollment message rendered appropriately for more than two courses.
        """
        self._configure_message_timeout(600)

        # Create a number of new enrollments and courses, and force their creation behind
        # the first enrollment
        courses = []
        for idx, seconds_past in zip(list(range(2, 6)), [5, 10, 15, 20]):
            course_location = locator.CourseLocator(
                f'Org{idx}',
                f'Course{idx}',
                f'Run{idx}'
            )
            course, enrollment = self._create_course_and_enrollment(course_location)
            enrollment.created = now() - datetime.timedelta(seconds=seconds_past)
            enrollment.save()
            courses.append(course)

        courses_list = list(get_course_enrollments(self.student, None, []))
        assert len(courses_list) == 6

        recent_course_list = _get_recently_enrolled_courses(courses_list)
        assert len(recent_course_list) == 5

        assert recent_course_list[1].course.id == courses[0].id
        assert recent_course_list[2].course.id == courses[1].id
        assert recent_course_list[3].course.id == courses[2].id
        assert recent_course_list[4].course.id == courses[3].id

        self.client.login(username=self.student.username, password=self.PASSWORD)
        response = self.client.get(reverse("dashboard"))

        # verify recent enrollment message
        self.assertContains(response, 'Thank you for enrolling in:')
        self.assertContains(
            response,
            ', '.join(enrollment.course.display_name for enrollment in recent_course_list)
        )

    def test_dashboard_rendering_with_single_course(self):
        """
        Tests that the dashboard renders the recent enrollment message appropriately for single course.
        """
        self._configure_message_timeout(600)
        self.client.login(username=self.student.username, password=self.PASSWORD)
        response = self.client.get(reverse("dashboard"))
        self.assertContains(
            response,
            f"Thank you for enrolling in {self.course.display_name}"
        )

    def test_dashboard_rendering_with_two_courses(self):
        """
        Tests that the dashboard renders the recent enrollment message appropriately for two courses.
        """
        self._configure_message_timeout(600)
        course_location = locator.CourseLocator(
            'Org2',
            'Course2',
            'Run2'
        )
        course, _ = self._create_course_and_enrollment(course_location)  # lint-amnesty, pylint: disable=unused-variable

        self.client.login(username=self.student.username, password=self.PASSWORD)
        response = self.client.get(reverse("dashboard"))

        courses_enrollments = list(get_course_enrollments(self.student, None, []))
        courses_enrollments.sort(key=lambda x: x.created, reverse=True)
        assert len(courses_enrollments) == 3

        recent_course_enrollments = _get_recently_enrolled_courses(courses_enrollments)
        assert len(recent_course_enrollments) == 2

        self.assertContains(
            response,
            "Thank you for enrolling in:"
        )
        self.assertContains(
            response,
            ' and '.join(enrollment.course.display_name for enrollment in recent_course_enrollments)
        )

    def test_dashboard_escaped_rendering(self):
        """
        Tests that the dashboard renders the escaped recent enrollment messages appropriately.
        """
        self._configure_message_timeout(600)
        self.client.login(username=self.student.username, password=self.PASSWORD)

        # New Course
        course_location = locator.CourseLocator('TestOrg', 'TestCourse', 'TestRun')
        xss_content = "<script>alert('XSS')</script>"
        course = CourseFactory.create(
            org=course_location.org,
            number=course_location.course,
            run=course_location.run,
            display_name=xss_content
        )
        CourseEnrollment.enroll(self.student, course.id)

        response = self.client.get(reverse("dashboard"))
        self.assertContains(response, "Thank you for enrolling in")

        # Check if response is escaped
        self.assert_no_xss(response, xss_content)
