"""
Unit tests for checking default forum role "Student" of a user when he creates a course or
after deleting it creates same course again
"""

from unittest import skip

from cms.djangoapps.contentstore.tests.utils import AjaxEnabledTestClient
from cms.djangoapps.contentstore.utils import delete_course, reverse_url
from common.djangoapps.student.models import CourseEnrollment
from common.djangoapps.student.tests.factories import UserFactory
from xmodule.modulestore.tests.django_utils import TEST_DATA_SPLIT_MODULESTORE, ModuleStoreTestCase


class TestUsersDefaultRole(ModuleStoreTestCase):
    """
    Unit tests for checking enrollment and default forum role "Student" of a logged in user
    """
    MODULESTORE = TEST_DATA_SPLIT_MODULESTORE

    def setUp(self):
        """
        Add a user and a course
        """
        super().setUp()
        # create and log in a staff user.
        self.user = UserFactory(is_staff=True)
        self.client = AjaxEnabledTestClient()
        self.client.login(username=self.user.username, password=self.TEST_PASSWORD)

        # create a course via the view handler to create course
        self.course_key = self.store.make_course_key('Org_1', 'Course_1', 'Run_1')
        self._create_course_with_given_location(self.course_key)

    def _create_course_with_given_location(self, course_key):
        """
        Create course at provided location
        """
        resp = self.client.ajax_post(
            reverse_url('course_handler'),
            {
                'org': course_key.org,
                'number': course_key.course,
                'display_name': 'test course',
                'run': course_key.run,
            }
        )
        return resp

    def tearDown(self):
        """
        Reverse the setup
        """
        self.client.logout()
        super().tearDown()

    def test_user_forum_default_role_on_course_deletion(self):
        """
        Test that a user enrolls and gets "Student" forum role for that course which he creates and remains
        enrolled even the course is deleted and keeps its "Student" forum role for that course
        """
        # check that user has enrollment for this course
        self.assertTrue(CourseEnrollment.is_enrolled(self.user, self.course_key))

        # check that user has his default "Student" forum role for this course
        self.assertTrue(self.user.roles.filter(name="Student", course_id=self.course_key))

        delete_course(self.course_key, self.user.id)

        # check that user's enrollment for this course is not deleted
        self.assertTrue(CourseEnrollment.is_enrolled(self.user, self.course_key))

        # check that user has forum role for this course even after deleting it
        self.assertTrue(self.user.roles.filter(name="Student", course_id=self.course_key))

    def test_user_role_on_course_recreate(self):
        """
        Test that creating same course again after deleting it gives user his default
        forum role "Student" for that course
        """
        # check that user has enrollment and his default "Student" forum role for this course
        self.assertTrue(CourseEnrollment.is_enrolled(self.user, self.course_key))
        self.assertTrue(self.user.roles.filter(name="Student", course_id=self.course_key))

        # delete this course and recreate this course with same user
        delete_course(self.course_key, self.user.id)
        resp = self._create_course_with_given_location(self.course_key)
        self.assertEqual(resp.status_code, 200)

        # check that user has his enrollment for this course
        self.assertTrue(CourseEnrollment.is_enrolled(self.user, self.course_key))

        # check that user has his default "Student" forum role for this course
        self.assertTrue(self.user.roles.filter(name="Student", course_id=self.course_key))

    @skip("OldMongo Deprecation")
    # Issue with case-insensitive course keys
    def test_user_role_on_course_recreate_with_change_name_case(self):
        """
        Test that creating same course again with different name case after deleting it gives user
        his default forum role "Student" for that course
        """
        # check that user has enrollment and his default "Student" forum role for this course
        self.assertTrue(CourseEnrollment.is_enrolled(self.user, self.course_key))
        # delete this course and recreate this course with same user
        delete_course(self.course_key, self.user.id)

        # now create same course with different name case ('uppercase')
        new_course_key = self.course_key.replace(course=self.course_key.course.upper())
        resp = self._create_course_with_given_location(new_course_key)
        self.assertEqual(resp.status_code, 200)

        # check that user has his default "Student" forum role again for this course (with changed name case)
        self.assertTrue(
            self.user.roles.filter(name="Student", course_id=new_course_key)
        )

        # Disabled due to case-sensitive test db (sqlite3)
        # # check that there user has only one "Student" forum role (with new updated course_id)
        # self.assertEqual(self.user.roles.filter(name='Student').count(), 1)
        # self.assertEqual(self.user.roles.filter(name='Student')[0].course_id, new_course_location.course_key)
