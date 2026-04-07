"""
Unit tests for instructor API enrollment list endpoints with search and pagination.
"""
import json

from django.urls import reverse

from common.djangoapps.student.roles import CourseBetaTesterRole
from common.djangoapps.student.tests.factories import CourseEnrollmentFactory, InstructorFactory, UserFactory
from lms.djangoapps.courseware.tests.helpers import LoginEnrollmentTestCase
from xmodule.modulestore.tests.django_utils import SharedModuleStoreTestCase
from xmodule.modulestore.tests.factories import CourseFactory


class TestListCourseRoleMembersWithPagination(SharedModuleStoreTestCase, LoginEnrollmentTestCase):
    """
    Test the list_course_role_members endpoint with search and pagination functionality.
    """
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course = CourseFactory.create()

    def setUp(self):
        super().setUp()
        self.instructor = InstructorFactory(course_key=self.course.id)
        self.client.login(username=self.instructor.username, password=self.TEST_PASSWORD)
        self.url = reverse('list_course_role_members', kwargs={'course_id': str(self.course.id)})

        # Create beta testers for testing
        self.beta_testers = []
        beta_role = CourseBetaTesterRole(self.course.id)
        for i in range(25):
            user = UserFactory(
                username=f'beta_user_{i}',
                email=f'beta{i}@example.com',
                first_name=f'Beta{i}',
                last_name=f'Tester{i}'
            )
            beta_role.add_users(user)
            self.beta_testers.append(user)

    def test_list_beta_testers_without_pagination(self):
        """Test listing beta testers without pagination parameters (backward compatibility)."""
        response = self.client.post(self.url, {'rolename': 'beta'})
        assert response.status_code == 200

        res_json = json.loads(response.content.decode('utf-8'))
        assert res_json['course_id'] == str(self.course.id)
        assert 'beta' in res_json
        assert res_json['count'] == 25
        assert res_json['num_pages'] == 2  # 25 items with default page_size of 20
        assert res_json['current_page'] == 1
        assert len(res_json['beta']) == 20  # First page with default page_size

    def test_list_beta_testers_with_pagination(self):
        """Test listing beta testers with pagination."""
        response = self.client.post(self.url, {
            'rolename': 'beta',
            'page': 1,
            'page_size': 10
        })
        assert response.status_code == 200

        res_json = json.loads(response.content.decode('utf-8'))
        assert res_json['count'] == 25
        assert res_json['num_pages'] == 3  # 25 items / 10 per page
        assert res_json['current_page'] == 1
        assert len(res_json['beta']) == 10

    def test_list_beta_testers_second_page(self):
        """Test listing beta testers on second page."""
        response = self.client.post(self.url, {
            'rolename': 'beta',
            'page': 2,
            'page_size': 10
        })
        assert response.status_code == 200

        res_json = json.loads(response.content.decode('utf-8'))
        assert res_json['current_page'] == 2
        assert len(res_json['beta']) == 10

    def test_list_beta_testers_last_page(self):
        """Test listing beta testers on last page with partial results."""
        response = self.client.post(self.url, {
            'rolename': 'beta',
            'page': 3,
            'page_size': 10
        })
        assert response.status_code == 200

        res_json = json.loads(response.content.decode('utf-8'))
        assert res_json['current_page'] == 3
        assert len(res_json['beta']) == 5  # Last page has 5 items

    def test_list_beta_testers_beyond_last_page(self):
        """Test requesting a page beyond the last page returns empty results."""
        response = self.client.post(self.url, {
            'rolename': 'beta',
            'page': 10,
            'page_size': 10
        })
        assert response.status_code == 200

        res_json = json.loads(response.content.decode('utf-8'))
        assert res_json['current_page'] == 10
        assert len(res_json['beta']) == 0

    def test_list_beta_testers_search_by_username(self):
        """Test searching beta testers by username."""
        response = self.client.post(self.url, {
            'rolename': 'beta',
            'search': 'beta_user_1',
            'page': 1,
            'page_size': 10
        })
        assert response.status_code == 200

        res_json = json.loads(response.content.decode('utf-8'))
        # Should match beta_user_1, beta_user_10-19 (11 total)
        assert res_json['count'] == 11
        assert len(res_json['beta']) == 10  # First page
        for user in res_json['beta']:
            assert 'beta_user_1' in user['username']

    def test_list_beta_testers_search_by_email(self):
        """Test searching beta testers by email."""
        response = self.client.post(self.url, {
            'rolename': 'beta',
            'search': 'beta5@example.com',
            'page': 1,
            'page_size': 10
        })
        assert response.status_code == 200

        res_json = json.loads(response.content.decode('utf-8'))
        assert res_json['count'] == 1
        assert len(res_json['beta']) == 1
        assert res_json['beta'][0]['email'] == 'beta5@example.com'

    def test_list_beta_testers_search_by_first_name(self):
        """Test searching beta testers by first name."""
        response = self.client.post(self.url, {
            'rolename': 'beta',
            'search': 'Beta2',
            'page': 1,
            'page_size': 10
        })
        assert response.status_code == 200

        res_json = json.loads(response.content.decode('utf-8'))
        # Should match Beta2, Beta20-24 (6 total)
        assert res_json['count'] == 6
        for user in res_json['beta']:
            assert 'Beta2' in user['first_name']

    def test_list_beta_testers_search_case_insensitive(self):
        """Test that search is case-insensitive."""
        response = self.client.post(self.url, {
            'rolename': 'beta',
            'search': 'BETA_USER_3',
            'page': 1,
            'page_size': 10
        })
        assert response.status_code == 200

        res_json = json.loads(response.content.decode('utf-8'))
        assert res_json['count'] == 1
        assert res_json['beta'][0]['username'] == 'beta_user_3'

    def test_list_beta_testers_search_no_results(self):
        """Test searching with no matching results."""
        response = self.client.post(self.url, {
            'rolename': 'beta',
            'search': 'nonexistent',
            'page': 1,
            'page_size': 10
        })
        assert response.status_code == 200

        res_json = json.loads(response.content.decode('utf-8'))
        assert res_json['count'] == 0
        assert len(res_json['beta']) == 0

    def test_list_beta_testers_empty_search(self):
        """Test that empty search returns all results."""
        response = self.client.post(self.url, {
            'rolename': 'beta',
            'search': '',
            'page': 1,
            'page_size': 10
        })
        assert response.status_code == 200

        res_json = json.loads(response.content.decode('utf-8'))
        assert res_json['count'] == 25

    def test_list_beta_testers_max_page_size(self):
        """Test that page_size is capped at maximum."""
        response = self.client.post(self.url, {
            'rolename': 'beta',
            'page': 1,
            'page_size': 100
        })
        assert response.status_code == 200

        res_json = json.loads(response.content.decode('utf-8'))
        assert len(res_json['beta']) == 25  # All results fit in max page size

    def test_list_beta_testers_invalid_page_size(self):
        """Test with invalid page_size (should fail validation)."""
        response = self.client.post(self.url, {
            'rolename': 'beta',
            'page': 1,
            'page_size': 0
        })
        assert response.status_code == 400

    def test_list_beta_testers_invalid_page(self):
        """Test with invalid page number (should fail validation)."""
        response = self.client.post(self.url, {
            'rolename': 'beta',
            'page': 0,
            'page_size': 10
        })
        assert response.status_code == 400


class TestListCourseEnrollments(SharedModuleStoreTestCase, LoginEnrollmentTestCase):
    """
    Test the list_course_enrollments endpoint with search and pagination functionality.
    """
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course = CourseFactory.create()

    def setUp(self):
        super().setUp()
        self.instructor = InstructorFactory(course_key=self.course.id)
        self.client.login(username=self.instructor.username, password=self.TEST_PASSWORD)
        self.url = reverse('list_course_enrollments', kwargs={'course_id': str(self.course.id)})

        # Create enrollments for testing
        self.enrolled_users = []
        for i in range(30):
            user = UserFactory(
                username=f'student_{i}',
                email=f'student{i}@example.com',
                first_name=f'Student{i}',
                last_name=f'Learner{i}'
            )
            CourseEnrollmentFactory(
                user=user,
                course_id=self.course.id,
                is_active=True
            )
            self.enrolled_users.append(user)

        # Create some inactive enrollments (should not be included)
        for i in range(5):
            user = UserFactory(
                username=f'inactive_{i}',
                email=f'inactive{i}@example.com'
            )
            CourseEnrollmentFactory(
                user=user,
                course_id=self.course.id,
                is_active=False
            )

    def test_list_enrollments_without_pagination(self):
        """Test listing enrollments without pagination parameters."""
        response = self.client.post(self.url, {})
        assert response.status_code == 200

        res_json = json.loads(response.content.decode('utf-8'))
        assert res_json['course_id'] == str(self.course.id)
        assert 'enrollments' in res_json
        # 30 active student enrollments (InstructorFactory does not create an enrollment)
        assert res_json['count'] == 30
        assert res_json['num_pages'] == 2  # 30 items with default page_size of 20
        assert res_json['current_page'] == 1
        assert len(res_json['enrollments']) == 20

    def test_list_enrollments_with_pagination(self):
        """Test listing enrollments with pagination."""
        response = self.client.post(self.url, {
            'page': 1,
            'page_size': 10
        })
        assert response.status_code == 200

        res_json = json.loads(response.content.decode('utf-8'))
        assert res_json['count'] == 30
        assert res_json['num_pages'] == 3  # 30 items / 10 per page = 3 pages
        assert res_json['current_page'] == 1
        assert len(res_json['enrollments']) == 10

    def test_list_enrollments_second_page(self):
        """Test listing enrollments on second page."""
        response = self.client.post(self.url, {
            'page': 2,
            'page_size': 10
        })
        assert response.status_code == 200

        res_json = json.loads(response.content.decode('utf-8'))
        assert res_json['current_page'] == 2
        assert len(res_json['enrollments']) == 10

    def test_list_enrollments_last_page(self):
        """Test listing enrollments on last page."""
        response = self.client.post(self.url, {
            'page': 3,
            'page_size': 10
        })
        assert response.status_code == 200

        res_json = json.loads(response.content.decode('utf-8'))
        assert res_json['current_page'] == 3
        assert len(res_json['enrollments']) == 10  # Last page has 10 items

    def test_list_enrollments_search_by_username(self):
        """Test searching enrollments by username."""
        response = self.client.post(self.url, {
            'search': 'student_2',
            'page': 1,
            'page_size': 10
        })
        assert response.status_code == 200

        res_json = json.loads(response.content.decode('utf-8'))
        # Should match student_2, student_20-29 (11 total)
        assert res_json['count'] == 11
        for user in res_json['enrollments']:
            assert 'student_2' in user['username']

    def test_list_enrollments_search_by_email(self):
        """Test searching enrollments by email."""
        response = self.client.post(self.url, {
            'search': 'student7@example.com',
            'page': 1,
            'page_size': 10
        })
        assert response.status_code == 200

        res_json = json.loads(response.content.decode('utf-8'))
        assert res_json['count'] == 1
        assert res_json['enrollments'][0]['email'] == 'student7@example.com'

    def test_list_enrollments_search_by_first_name(self):
        """Test searching enrollments by first name."""
        response = self.client.post(self.url, {
            'search': 'Student1',
            'page': 1,
            'page_size': 20
        })
        assert response.status_code == 200

        res_json = json.loads(response.content.decode('utf-8'))
        # Should match Student1, Student10-19 (11 total)
        assert res_json['count'] == 11

    def test_list_enrollments_search_case_insensitive(self):
        """Test that search is case-insensitive."""
        response = self.client.post(self.url, {
            'search': 'STUDENT_5',
            'page': 1,
            'page_size': 10
        })
        assert response.status_code == 200

        res_json = json.loads(response.content.decode('utf-8'))
        assert res_json['count'] == 1
        assert res_json['enrollments'][0]['username'] == 'student_5'

    def test_list_enrollments_search_no_results(self):
        """Test searching with no matching results."""
        response = self.client.post(self.url, {
            'search': 'nonexistent',
            'page': 1,
            'page_size': 10
        })
        assert response.status_code == 200

        res_json = json.loads(response.content.decode('utf-8'))
        assert res_json['count'] == 0
        assert len(res_json['enrollments']) == 0

    def test_list_enrollments_excludes_inactive(self):
        """Test that inactive enrollments are not included."""
        response = self.client.post(self.url, {
            'search': 'inactive',
            'page': 1,
            'page_size': 10
        })
        assert response.status_code == 200

        res_json = json.loads(response.content.decode('utf-8'))
        # Should not find any inactive enrollments
        assert res_json['count'] == 0

    def test_list_enrollments_empty_course(self):
        """Test listing enrollments for a course with no enrollments."""
        empty_course = CourseFactory.create()
        empty_instructor = InstructorFactory(course_key=empty_course.id)
        self.client.login(username=empty_instructor.username, password=self.TEST_PASSWORD)

        url = reverse('list_course_enrollments', kwargs={'course_id': str(empty_course.id)})
        response = self.client.post(url, {})
        assert response.status_code == 200

        res_json = json.loads(response.content.decode('utf-8'))
        # InstructorFactory does not create an enrollment, so empty course has 0
        assert res_json['count'] == 0
        assert len(res_json['enrollments']) == 0

    def test_list_enrollments_max_page_size(self):
        """Test that page_size is capped at maximum."""
        response = self.client.post(self.url, {
            'page': 1,
            'page_size': 100
        })
        assert response.status_code == 200

        res_json = json.loads(response.content.decode('utf-8'))
        assert len(res_json['enrollments']) == 30  # All results fit in max page size

    def test_list_enrollments_invalid_page_size(self):
        """Test with invalid page_size (should fail validation)."""
        response = self.client.post(self.url, {
            'page': 1,
            'page_size': 0
        })
        assert response.status_code == 400

    def test_list_enrollments_invalid_page(self):
        """Test with invalid page number (should fail validation)."""
        response = self.client.post(self.url, {
            'page': -1,
            'page_size': 10
        })
        assert response.status_code == 400

    def test_list_enrollments_permission_required(self):
        """Test that non-instructor users cannot access the endpoint."""
        student = UserFactory()
        self.client.login(username=student.username, password=self.TEST_PASSWORD)

        response = self.client.post(self.url, {})
        assert response.status_code == 403

    def test_list_enrollments_ordered_by_username(self):
        """Test that enrollments are ordered by username."""
        response = self.client.post(self.url, {
            'page': 1,
            'page_size': 5
        })
        assert response.status_code == 200

        res_json = json.loads(response.content.decode('utf-8'))
        usernames = [user['username'] for user in res_json['enrollments']]
        # Check that usernames are in alphabetical order
        assert usernames == sorted(usernames)
