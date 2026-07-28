"""
Test for survey report commands.
"""

from datetime import datetime, timedelta

from common.djangoapps.student.tests.factories import CourseEnrollmentFactory, UserFactory
from lms.djangoapps.grades.models import PersistentCourseGrade
from openedx.core.djangoapps.content.course_overviews.tests.factories import CourseOverviewFactory
from openedx.features.survey_report.queries import (
    get_course_enrollments,
    get_generated_certificates,
    get_recently_active_users,
    get_registered_learners,
    get_unique_courses_offered,
)
from xmodule.modulestore.django import modulestore
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase
from xmodule.modulestore.tests.factories import CourseFactory  # pylint: disable=wrong-import-order


class TestSurveyReportCommands(ModuleStoreTestCase):
    """
    Test for survey report query methods.
    """

    def setUp(self):
        """
        Setup for users and courses.
        """
        super().setUp()
        self.store = modulestore()  # pylint: disable=protected-access
        self.first_course = CourseFactory.create(
            org="test", course="course1", display_name="run1"
        )
        self.user = UserFactory.create(username='test_user', email='test@example.com', password='password')
        self.user1 = UserFactory.create(username='test_user1', email='test1@example.com', password='password')
        self.user2 = UserFactory.create(username='test_user2', email='test2@example.com', password='password')
        self.user3 = UserFactory.create(username='test_user3', email='test3@example.com', password='password')
        self.user4 = UserFactory.create(username='test_user4', email='test4@example.com', password='password')
        self.user5 = UserFactory.create(username='test_user5', email='test5@example.com', password='password')

    def test_get_unique_courses_offered(self):
        """
        Test that get_unique_courses_offered returns the correct number of courses.
        """
        course_overview = CourseOverviewFactory.create(id=self.first_course.id, start="2019-01-01", end="9999-01-01")
        CourseEnrollmentFactory.create(user=self.user, course_id=course_overview.id)
        CourseEnrollmentFactory.create(user=self.user1, course_id=course_overview.id)
        CourseEnrollmentFactory.create(user=self.user2, course_id=course_overview.id)
        CourseEnrollmentFactory.create(user=self.user3, course_id=course_overview.id)
        CourseEnrollmentFactory.create(user=self.user4, course_id=course_overview.id)
        CourseEnrollmentFactory.create(user=self.user5, course_id=course_overview.id)
        assert get_unique_courses_offered() == 1

    def test_get_recently_active_users(self):
        """
        Test that get_currently_learners returns the correct number of learners.
        """
        self.user.last_login = datetime.now() - timedelta(days=1)
        self.user.save()
        self.user1.last_login = datetime.now() - timedelta(weeks=2)
        self.user1.save()
        self.user2.last_login = datetime.now() - timedelta(weeks=4)
        self.user2.save()
        assert get_recently_active_users(weeks=3) == 2

    def test_get_learners_registered(self):
        """
        Test that get_learners_registered returns the correct number of learners.
        """
        assert get_registered_learners() == 7

    def test_get_generated_certificates(self):
        """
        Test that get_generated_certificates returns the correct number of certificates.
        """
        course_grade_params = {
            "user_id": self.user.id,
            "course_id": self.first_course.id,
            "percent_grade": 77.7,
            "letter_grade": "pass",
            "passed": True,
            "passed_timestamp": datetime.now(),
        }
        PersistentCourseGrade.update_or_create(**course_grade_params)
        assert get_generated_certificates() == 1

    def test_get_course_enrollments(self):
        """
        Test that get_course_enrollments returns the correct number of enrollments.
        """
        self.user.is_superuser = True
        self.user.save()
        self.user1.is_staff = True
        self.user1.save()
        course_overview = CourseOverviewFactory.create(id=self.first_course.id, start="2019-01-01", end="2024-01-01")
        CourseEnrollmentFactory.create(user=self.user, course_id=course_overview.id)
        CourseEnrollmentFactory.create(user=self.user1, course_id=course_overview.id)
        CourseEnrollmentFactory.create(user=self.user2, course_id=course_overview.id)
        CourseEnrollmentFactory.create(user=self.user3, course_id=course_overview.id)
        CourseEnrollmentFactory.create(user=self.user4, course_id=course_overview.id)
        assert get_course_enrollments() == 3
