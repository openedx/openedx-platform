"""
Tests for the course import API views
"""
from datetime import datetime
from unittest.mock import MagicMock, patch
from uuid import uuid4

import ddt
import factory
from django.conf import settings
from django.contrib.auth import get_user_model
from django.test.utils import override_settings
from django.urls import reverse
from openedx_authz.constants.roles import COURSE_DATA_RESEARCHER, COURSE_STAFF
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from cms.djangoapps.contentstore.api.tests.base import BaseCourseViewTest
from common.djangoapps.course_modes.models import CourseMode
from common.djangoapps.course_modes.tests.factories import CourseModeFactory
from common.djangoapps.student.tests.factories import StaffFactory, UserFactory
from openedx.core.djangoapps.authz.tests.mixins import CourseAuthzTestMixin
from xmodule.modulestore.tests.django_utils import SharedModuleStoreTestCase
from xmodule.modulestore.tests.factories import BlockFactory, CourseFactory

User = get_user_model()


@ddt.ddt
@override_settings(
    PROCTORING_BACKENDS={
        "DEFAULT": "test_proctoring_provider",
        "test_proctoring_provider": {"requires_escalation_email": True},
    }
)
class CourseValidationViewTest(SharedModuleStoreTestCase, APITestCase):
    """
    Test course validation view via a RESTful API
    """
    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        cls.course = CourseFactory.create(
            display_name='test course',
            run="Testing_course",
            proctoring_provider='test_proctoring_provider',
            proctoring_escalation_email='test@example.com',
        )
        cls.course_key = cls.course.id

        cls.password = 'test'
        cls.student = UserFactory(username='dummy', password=cls.password)
        cls.staff = StaffFactory(course_key=cls.course.id, password=cls.password)

        cls.initialize_course(cls.course)

    @classmethod
    def initialize_course(cls, course):
        """
        Sets up test course structure.
        """
        course.start = datetime.now()
        course.self_paced = True
        cls.store.update_item(course, cls.staff.id)

        update_key = course.id.make_usage_key('course_info', 'updates')
        cls.store.create_item(
            cls.staff.id,
            update_key.course_key,
            update_key.block_type,
            block_id=update_key.block_id,
            fields=dict(data="<ol><li><h2>Date</h2>Hello world!</li></ol>"),
        )

        section = BlockFactory.create(
            parent_location=course.location,
            category="chapter",
        )
        BlockFactory.create(
            parent_location=section.location,
            category="sequential",
        )

    def get_url(self, course_id):
        """
        Helper function to create the url
        """
        return reverse(
            'courses_api:course_validation',
            kwargs={
                'course_id': course_id,
            }
        )

    def test_student_fails(self):
        self.client.login(username=self.student.username, password=self.password)
        resp = self.client.get(self.get_url(self.course_key))
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    @ddt.data(
        (False, False),
        (True, False),
        (False, True),
        (True, True),
    )
    @ddt.unpack
    def test_staff_succeeds(self, certs_html_view, with_modes):
        features = dict(settings.FEATURES, CERTIFICATES_HTML_VIEW=certs_html_view)
        with override_settings(FEATURES=features):
            if with_modes:
                CourseModeFactory.create_batch(
                    2,
                    course_id=self.course.id,
                    mode_slug=factory.Iterator([CourseMode.AUDIT, CourseMode.VERIFIED]),
                )
            self.client.login(username=self.staff.username, password=self.password)
            resp = self.client.get(self.get_url(self.course_key), {'all': 'true'})
            self.assertEqual(resp.status_code, status.HTTP_200_OK)
            expected_data = {
                'assignments': {
                    'total_number': 1,
                    'total_visible': 1,
                    'assignments_with_dates_before_start': [],
                    'assignments_with_dates_after_end': [],
                    'assignments_with_ora_dates_after_end': [],
                    'assignments_with_ora_dates_before_start': [],
                },
                'dates': {
                    'has_start_date': True,
                    'has_end_date': False,
                },
                'updates': {
                    'has_update': True,
                },
                'certificates': {
                    'is_enabled': with_modes,
                    'is_activated': False,
                    'has_certificate': False,
                },
                'grades': {
                    'has_grading_policy': False,
                    'sum_of_weights': 1.0,
                },
                'proctoring': {
                    'needs_proctoring_escalation_email': True,
                    'has_proctoring_escalation_email': True,
                },
                'is_self_paced': True,
            }
            self.assertDictEqual(resp.data, expected_data)


class TestMigrationViewSetCreate(SharedModuleStoreTestCase, APITestCase):
    """
    Test the MigrationViewSet.create() endpoint.

    Focus: validation, return codes, serialization/deserialization.
    """
    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        cls.course = CourseFactory.create(
            display_name='test course',
            run="Testing_course",
            proctoring_provider='test_proctoring_provider',
            proctoring_escalation_email='test@example.com',
        )
        cls.course_key = cls.course.id

        cls.password = 'test'
        cls.student = UserFactory(username='dummy', password=cls.password)
        cls.staff = StaffFactory(course_key=cls.course.id, password=cls.password)

        cls.initialize_course(cls.course)

    @classmethod
    def initialize_course(cls, course):
        """
        Sets up test course structure.
        """
        section = BlockFactory.create(
            parent_location=course.location,
            category="chapter",
        )
        subsection = BlockFactory.create(
            parent_location=section.location,
            category="sequential",
        )
        unit = BlockFactory.create(
            parent_location=subsection.location,
            category="vertical",
        )
        cls.block1 = BlockFactory.create(
            parent_location=unit.location,
            category="library_content",
        )
        cls.block2 = BlockFactory.create(
            parent_location=unit.location,
            category="library_content",
        )

    @patch('cms.djangoapps.contentstore.api.views.utils.has_course_author_access')
    @patch('cms.djangoapps.contentstore.api.views.course_validation.UserTaskStatus')
    @patch('xmodule.library_content_block.LegacyLibraryContentBlock.is_ready_to_migrate_to_v2')
    def test_create_update_reference_success(self, mock_block, mock_user_task_status, mock_auth):
        """
        Test successful migration creation with minimal required fields.

        Validates:
        - 201 status code is returned
        - Response contains expected serialized fields
        - Request data is properly deserialized
        - Permission checks are performed for both source and target
        """
        mock_auth.return_value = True

        mock_task_status = MagicMock(autospec=True)
        mock_task_status.uuid = uuid4()
        mock_task_status.state = 'Pending'
        mock_task_status.state_text = 'Pending'
        mock_task_status.completed_steps = 0
        mock_task_status.total_steps = 10
        mock_task_status.attempts = 1
        mock_task_status.created = '2025-01-01T00:00:00Z'
        mock_task_status.modified = '2025-01-01T00:00:00Z'
        mock_task_status.artifacts = []
        mock_task_status.migrations.all.return_value = []

        mock_user_task_status.objects.get.return_value = mock_task_status

        mock_block.return_value = True

        self.client.login(username=self.staff.username, password=self.password)
        response = self.client.post(
            f'/api/courses/v1/migrate_legacy_content_blocks/{self.course_key}/',
        )

        assert response.status_code == status.HTTP_201_CREATED

        assert 'uuid' in response.data
        assert 'state' in response.data
        assert 'state_text' in response.data
        assert 'completed_steps' in response.data
        assert 'total_steps' in response.data

        mock_auth.assert_called_once()

    @patch('cms.djangoapps.contentstore.api.views.utils.has_course_author_access')
    @patch('xmodule.library_content_block.LegacyLibraryContentBlock.is_ready_to_migrate_to_v2')
    def test_list_ready_to_update_reference_success(self, mock_block, mock_auth):
        """
        Test successful migration creation with minimal required fields.

        Validates:
        - 201 status code is returned
        - Response contains expected serialized fields
        - Request data is properly deserialized
        - Permission checks are performed for both source and target
        """
        mock_auth.return_value = True
        mock_block.return_value = True

        self.client.login(username=self.staff.username, password=self.password)
        response = self.client.get(
            f'/api/courses/v1/migrate_legacy_content_blocks/{self.course_key}/',
        )

        assert response.status_code == status.HTTP_200_OK

        data = response.json()
        self.assertListEqual(data, [
            {'usage_key': str(self.block1.location)},
            {'usage_key': str(self.block2.location)},
        ])
        mock_auth.assert_called_once()


class CourseValidationAuthzTest(CourseAuthzTestMixin, BaseCourseViewTest):
    """
    Tests Course Validation API authorization using openedx-authz.
    The endpoint uses COURSES_VIEW_COURSE permission.
    """

    view_name = "courses_api:course_validation"
    authz_roles_to_assign = [COURSE_STAFF.external_key]

    def test_authorized_user_can_access(self):
        """
        User with COURSE_STAFF role should be allowed via AuthZ.
        """
        resp = self.authorized_client.get(self.get_url(self.course_key))

        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    def test_unauthorized_user_cannot_access(self):
        """
        User without permissions should be denied.
        """
        resp = self.unauthorized_client.get(self.get_url(self.course_key))

        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_role_scoped_to_course(self):
        """
        Authorization should only apply to the assigned course scope.
        """
        other_course = self.store.create_course(
            "OtherOrg",
            "OtherCourse",
            "Run",
            self.staff.id,
        )

        resp = self.authorized_client.get(self.get_url(other_course.id))

        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_staff_user_allowed_via_legacy(self):
        """
        Course staff should pass through legacy fallback when AuthZ denies.
        """
        self.client.login(username=self.staff.username, password=self.password)

        resp = self.client.get(self.get_url(self.course_key))

        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    def test_superuser_allowed(self):
        """
        Superusers should always be allowed through legacy fallback.
        """
        superuser = UserFactory(is_superuser=True)

        client = APIClient()
        client.force_authenticate(user=superuser)

        resp = client.get(self.get_url(self.course_key))

        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    def test_non_staff_user_cannot_access(self):
        """
        User without permissions should be denied.
        This case validates that a non-staff user cannot access even
        if they have course author access to the course.
        """
        non_staff_user = UserFactory()
        non_staff_client = APIClient()
        self.add_user_to_role(non_staff_user, COURSE_DATA_RESEARCHER.external_key)
        non_staff_client.force_authenticate(user=non_staff_user)

        resp = non_staff_client.get(self.get_url(self.course_key))
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
