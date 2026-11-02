"""
Unit tests for getting the list of courses for a user through iterating all courses and
by reversing group name formats.
"""

from unittest import mock
from django.conf import settings
from django.test.client import Client
from milestones.tests.utils import MilestonesTestCaseMixin

from openedx.core.djangoapps.content.course_overviews.models import CourseOverview
from openedx.core.djangolib.testing.utils import skip_unless_lms
from common.djangoapps.student.models import CourseEnrollment  # lint-amnesty, pylint: disable=unused-import
from common.djangoapps.student.roles import GlobalStaff
from common.djangoapps.student.tests.factories import UserFactory
from common.djangoapps.student.views import get_course_enrollments
from common.djangoapps.util.milestones_helpers import get_pre_requisite_courses_not_completed, set_prerequisite_courses
from xmodule.error_block import ErrorBlock  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.modulestore import ModuleStoreEnum  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.modulestore.django import modulestore  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.modulestore.tests.factories import CourseFactory  # lint-amnesty, pylint: disable=wrong-import-order


class TestCourseListing(ModuleStoreTestCase, MilestonesTestCaseMixin):
    """
    Unit tests for getting the list of courses for a logged in user
    """
    ENABLED_SIGNALS = ['course_deleted']

    def setUp(self):
        """
        Add a student & teacher
        """
        super().setUp()

        self.student = UserFactory()
        self.teacher = UserFactory()
        GlobalStaff().add_users(self.teacher)
        self.client = Client()
        self.client.login(username=self.teacher.username, password='test')

    def _create_course_with_access_groups(self, course_location, metadata=None, default_store=None):
        """
        Create dummy course with 'CourseFactory' and enroll the student
        """
        metadata = {} if not metadata else metadata
        course = CourseFactory.create(
            org=course_location.org,
            number=course_location.course,
            run=course_location.run,
            metadata=metadata,
            default_store=default_store
        )

        CourseEnrollment.enroll(self.student, course.id)

        return course

    def tearDown(self):
        """
        Reverse the setup
        """
        self.client.logout()
        super().tearDown()

    @skip_unless_lms
    def test_get_course_list(self):
        """
        Test getting courses
        """
        course_location = self.store.make_course_key('Org1', 'Course1', 'Run1')
        self._create_course_with_access_groups(course_location)

        # get dashboard
        courses_list = list(get_course_enrollments(self.student, None, []))
        assert len(courses_list) == 1
        assert courses_list[0].course_id == course_location

        CourseEnrollment.unenroll(self.student, course_location)
        # get dashboard
        courses_list = list(get_course_enrollments(self.student, None, []))
        assert len(courses_list) == 0

    @skip_unless_lms
    def test_get_limited_number_of_courses_using_config(self):
        course_location = self.store.make_course_key('Org0', 'Course0', 'Run0')
        self._create_course_with_access_groups(course_location)

        course_location = self.store.make_course_key('Org1', 'Course1', 'Run1')
        self._create_course_with_access_groups(course_location)

        # get dashboard
        courses_list = list(get_course_enrollments(self.student, None, []))
        assert len(courses_list) == 2

        with self.settings(DASHBOARD_COURSE_LIMIT=1):
            course_limit = settings.DASHBOARD_COURSE_LIMIT
            courses_list = list(get_course_enrollments(self.student, None, [], course_limit))
            assert len(courses_list) == 1

    def test_errored_course_regular_access(self):
        """
        Test the course list for regular staff when get_course returns an ErrorBlock
        """
        store = modulestore()
        course_key = store.make_course_key('Org1', 'Course1', 'Run1')
        self._create_course_with_access_groups(course_key)

        with mock.patch(
            'xmodule.modulestore.split_mongo.runtime.SplitMongoKVS', mock.Mock(side_effect=Exception)
        ):
            assert isinstance(modulestore().get_course(course_key), ErrorBlock)

            # Invalidate (e.g., delete) the corresponding CourseOverview, forcing get_course to be called.
            CourseOverview.objects.filter(id=course_key).delete()

            courses_list = list(get_course_enrollments(self.student, None, []))
            assert not courses_list

    def test_course_listing_errored_deleted_courses(self):
        """
        Create good courses, courses that won't load, and deleted courses which still have
        roles. Test course listing.
        """
        store = modulestore()

        good_location = store.make_course_key('testOrg', 'testCourse', 'RunBabyRun')
        self._create_course_with_access_groups(good_location)

        course_location = store.make_course_key('testOrg', 'doomedCourse', 'RunBabyRun')
        self._create_course_with_access_groups(course_location)
        store.delete_course(course_location, ModuleStoreEnum.UserID.test)

        courses_list = list(get_course_enrollments(self.student, None, []))
        assert len(courses_list) == 1, courses_list
        assert courses_list[0].course_id == good_location

    @mock.patch.dict("django.conf.settings.FEATURES", {'ENABLE_PREREQUISITE_COURSES': True})
    def test_course_listing_has_pre_requisite_courses(self):
        """
        Creates four courses. Enroll test user in all courses
        Sets two of them as pre-requisites of another course.
        Checks course where pre-requisite course is set has appropriate info.
        """
        course_location2 = self.store.make_course_key('Org1', 'Course2', 'Run2')
        self._create_course_with_access_groups(course_location2)
        pre_requisite_course_location = self.store.make_course_key('Org1', 'Course3', 'Run3')
        self._create_course_with_access_groups(pre_requisite_course_location)
        pre_requisite_course_location2 = self.store.make_course_key('Org1', 'Course4', 'Run4')
        self._create_course_with_access_groups(pre_requisite_course_location2)
        # create a course with pre_requisite_courses
        pre_requisite_courses = [
            str(pre_requisite_course_location),
            str(pre_requisite_course_location2),
        ]
        course_location = self.store.make_course_key('Org1', 'Course1', 'Run1')
        self._create_course_with_access_groups(course_location, {
            'pre_requisite_courses': pre_requisite_courses
        })

        set_prerequisite_courses(course_location, pre_requisite_courses)
        # get dashboard
        course_enrollments = list(get_course_enrollments(self.student, None, []))
        courses_having_prerequisites = frozenset(
            enrollment.course_id for enrollment in course_enrollments
            if enrollment.course_overview.pre_requisite_courses
        )
        courses_requirements_not_met = get_pre_requisite_courses_not_completed(
            self.student,
            courses_having_prerequisites
        )
        assert len(courses_requirements_not_met[course_location]['courses']) == len(pre_requisite_courses)
