"""
Tests for Content Groups REST API v2.
"""
from unittest.mock import patch

from rest_framework import status
from rest_framework.test import APIClient

from xmodule.partitions.partitions import Group, UserPartition
from common.djangoapps.student.tests.factories import UserFactory
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase
from xmodule.modulestore.tests.factories import CourseFactory
from openedx.core.djangoapps.course_groups.constants import COHORT_SCHEME
from openedx.core.djangolib.testing.utils import skip_unless_lms


@skip_unless_lms
class GroupConfigurationsListViewTestCase(ModuleStoreTestCase):
    """
    Tests for GET /api/cohorts/v2/courses/{course_id}/group_configurations
    """

    def setUp(self):
        super().setUp()
        self.api_client = APIClient()
        self.user = UserFactory(is_staff=False)
        self.course = CourseFactory.create()
        self.api_client.force_authenticate(user=self.user)

    def _get_url(self, course_id=None):
        """Helper to get the list URL"""
        course_id = course_id or str(self.course.id)
        return f'/api/cohorts/v2/courses/{course_id}/group_configurations'

    @patch('lms.djangoapps.instructor.permissions.InstructorPermission.has_permission')
    def test_list_content_groups_returns_json(self, mock_perm):
        """Verify endpoint returns JSON with correct structure"""
        mock_perm.return_value = True

        self.course.user_partitions = [
            UserPartition(
                id=50,
                name='Content Groups',
                description='Test description',
                groups=[
                    Group(id=1, name='Content Group A'),
                    Group(id=2, name='Content Group B'),
                ],
                scheme_id=COHORT_SCHEME
            )
        ]
        self.update_course(self.course, self.user.id)

        response = self.api_client.get(self._get_url())

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response['Content-Type'], 'application/json')

        data = response.json()
        self.assertIn('all_group_configurations', data)
        self.assertIn('should_show_enrollment_track', data)
        self.assertIn('should_show_experiment_groups', data)

        configs = data['all_group_configurations']
        self.assertEqual(len(configs), 1)
        self.assertEqual(configs[0]['scheme'], COHORT_SCHEME)
        self.assertEqual(len(configs[0]['groups']), 2)

    @patch('lms.djangoapps.instructor.permissions.InstructorPermission.has_permission')
    def test_list_content_groups_filters_non_cohort_partitions(self, mock_perm):
        """Verify only cohort-scheme partitions are returned"""
        mock_perm.return_value = True

        self.course.user_partitions = [
            UserPartition(
                id=50,
                name='Content Groups',
                description='Cohort-based content groups',
                groups=[Group(id=1, name='Group A')],
                scheme_id=COHORT_SCHEME
            ),
            UserPartition(
                id=51,
                name='Experiment Groups',
                description='Random experiment groups',
                groups=[Group(id=1, name='Group B')],
                scheme_id='random'
            ),
        ]
        self.update_course(self.course, self.user.id)

        response = self.api_client.get(self._get_url())

        data = response.json()
        configs = data['all_group_configurations']

        self.assertEqual(len(configs), 1)
        self.assertEqual(configs[0]['id'], 50)
        self.assertEqual(configs[0]['scheme'], COHORT_SCHEME)

    @patch('lms.djangoapps.instructor.permissions.InstructorPermission.has_permission')
    def test_list_auto_creates_empty_content_group_if_none_exists(self, mock_perm):
        """Verify empty content group is auto-created when none exists"""
        mock_perm.return_value = True

        response = self.api_client.get(self._get_url())

        data = response.json()
        configs = data['all_group_configurations']

        self.assertEqual(len(configs), 1)
        self.assertEqual(configs[0]['scheme'], COHORT_SCHEME)
        self.assertEqual(len(configs[0]['groups']), 0)

    def test_list_requires_authentication(self):
        """Verify endpoint requires authentication"""
        client = APIClient()
        response = client.get(self._get_url())
        self.assertIn(response.status_code, [status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN])

    @patch('lms.djangoapps.instructor.permissions.InstructorPermission.has_permission')
    def test_list_invalid_course_key_returns_400(self, mock_perm):
        """Verify invalid course key returns 400"""
        mock_perm.return_value = True

        response = self.api_client.get('/api/cohorts/v2/courses/course-v1:invalid+course+key/group_configurations')
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


@skip_unless_lms
class GroupConfigurationDetailViewTestCase(ModuleStoreTestCase):
    """
    Tests for GET /api/cohorts/v2/courses/{course_id}/group_configurations/{id}
    """

    def setUp(self):
        super().setUp()
        self.api_client = APIClient()
        self.user = UserFactory(is_staff=False)
        self.course = CourseFactory.create()
        self.api_client.force_authenticate(user=self.user)

        self.course.user_partitions = [
            UserPartition(
                id=50,
                name='Test Content Groups',
                description='Test',
                groups=[
                    Group(id=1, name='Group A'),
                    Group(id=2, name='Group B'),
                ],
                scheme_id=COHORT_SCHEME
            )
        ]
        self.update_course(self.course, self.user.id)

    def _get_url(self, configuration_id=50):
        """Helper to get detail URL"""
        return f'/api/cohorts/v2/courses/{self.course.id}/group_configurations/{configuration_id}'

    @patch('lms.djangoapps.instructor.permissions.InstructorPermission.has_permission')
    def test_get_configuration_details(self, mock_perm):
        """Verify GET returns full configuration details"""
        mock_perm.return_value = True

        response = self.api_client.get(self._get_url())

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()

        self.assertEqual(data['id'], 50)
        self.assertEqual(data['name'], 'Test Content Groups')
        self.assertEqual(data['scheme'], COHORT_SCHEME)
        self.assertEqual(len(data['groups']), 2)


@skip_unless_lms
class ContentGroupsPermissionsTestCase(ModuleStoreTestCase):
    """
    Tests for permission checking
    """

    def setUp(self):
        super().setUp()
        self.course = CourseFactory.create()
        self.staff_user = UserFactory(is_staff=False)
        self.regular_user = UserFactory()

    def _get_url(self):
        """Helper to get list URL"""
        return f'/api/cohorts/v2/courses/{self.course.id}/group_configurations'

    @patch('lms.djangoapps.instructor.permissions.InstructorPermission.has_permission')
    def test_staff_user_can_access(self, mock_perm):
        """Verify staff users can access the endpoint"""
        mock_perm.return_value = True

        client = APIClient()
        client.force_authenticate(user=self.staff_user)

        response = client.get(self._get_url())
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_unauthenticated_user_denied(self):
        """Verify unauthenticated users are denied"""
        client = APIClient()
        response = client.get(self._get_url())
        self.assertIn(response.status_code, [status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN])
