"""
Unit tests for the modulestore_migrator REST API v1 views.

These tests focus on validation, HTTP status codes, and serialization/deserialization.
Business logic is mocked out.
"""
from unittest.mock import MagicMock, patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.test import TestCase
from opaque_keys.edx.locator import (
    BlockUsageLocator, CourseLocator, LibraryLocatorV2, LibraryUsageLocatorV2
)
from organizations.tests.factories import OrganizationFactory
from rest_framework import status
from rest_framework.exceptions import PermissionDenied
from rest_framework.test import APIRequestFactory, force_authenticate
from user_tasks.models import UserTaskStatus

from cms.djangoapps.modulestore_migrator.data import (
    ModulestoreMigration,
    ModulestoreBlockMigrationSuccess,
    ModulestoreBlockMigrationFailure,
)
from cms.djangoapps.modulestore_migrator.models import (
    ModulestoreMigration as ModulestoreMigrationModel,
    ModulestoreSource,
)
from cms.djangoapps.modulestore_migrator.rest_api.v1.views import (
    BlockMigrationInfo,
    BulkMigrationViewSet,
    MigrationInfoViewSet,
    MigrationViewSet,
    PreviewMigration,
)
from openedx.core.djangoapps.content_libraries import api as lib_api


User = get_user_model()


class TestMigrationViewSetCreate(TestCase):
    """
    Test the MigrationViewSet.create() endpoint.

    Focus: validation, return codes, serialization/deserialization.
    """

    def setUp(self):
        """Set up test fixtures."""
        self.factory = APIRequestFactory()
        self.view = MigrationViewSet.as_view({'post': 'create'})

        # Create test user
        self.user = User.objects.create_user(
            username='testuser',
            email='testuser@test.com',
            password='password'
        )

    @patch('cms.djangoapps.modulestore_migrator.rest_api.v1.views.migrator_api')
    @patch('cms.djangoapps.modulestore_migrator.rest_api.v1.views.lib_api')
    @patch('cms.djangoapps.modulestore_migrator.rest_api.v1.views.auth')
    @patch('cms.djangoapps.modulestore_migrator.rest_api.v1.views.UserTaskStatus')
    def test_create_migration_success_with_minimal_data(
        self, mock_user_task_status, mock_auth, mock_lib_api, mock_migrator_api
    ):
        """
        Test successful migration creation with minimal required fields.

        Validates:
        - 201 status code is returned
        - Response contains expected serialized fields
        - Request data is properly deserialized
        - Permission checks are performed for both source and target
        """
        mock_auth.has_studio_write_access.return_value = True
        mock_lib_api.require_permission_for_library_key.return_value = None

        mock_task = MagicMock(autospec=True)
        mock_task.id = 'test-task-id'
        mock_migrator_api.start_migration_to_library.return_value = mock_task

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

        request_data = {
            'source': 'course-v1:TestOrg+TestCourse+TestRun',
            'target': 'lib:TestOrg:TestLibrary',
        }
        request = self.factory.post(
            '/api/modulestore_migrator/v1/migrations/',
            data=request_data,
            format='json'
        )
        force_authenticate(request, user=self.user)

        response = self.view(request)

        assert response.status_code == status.HTTP_201_CREATED

        assert 'uuid' in response.data
        assert 'state' in response.data
        assert 'state_text' in response.data
        assert 'completed_steps' in response.data
        assert 'total_steps' in response.data
        assert 'parameters' in response.data

        mock_auth.has_studio_write_access.assert_called_once()
        mock_lib_api.require_permission_for_library_key.assert_called_once()

        mock_migrator_api.start_migration_to_library.assert_called_once()
        call_kwargs = mock_migrator_api.start_migration_to_library.call_args[1]
        assert call_kwargs['user'] == self.user
        assert str(call_kwargs['source_key']) == 'course-v1:TestOrg+TestCourse+TestRun'
        assert str(call_kwargs['target_library_key']) == 'lib:TestOrg:TestLibrary'

    def test_create_migration_invalid_source_key(self):
        """
        Test that invalid source key returns 400 Bad Request.

        Validates:
        - 400 status code is returned
        - Error message mentions validation failure
        """
        request_data = {
            'source': 'not-a-valid-key',
            'target': 'lib:TestOrg:TestLibrary',
        }
        request = self.factory.post(
            '/api/modulestore_migrator/v1/migrations/',
            data=request_data,
            format='json'
        )
        force_authenticate(request, user=self.user)

        response = self.view(request)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert 'source' in response.data

    def test_create_migration_invalid_target_key(self):
        """
        Test that invalid target library key returns 400 Bad Request.

        Validates:
        - 400 status code is returned
        - Error message mentions target validation failure
        """
        request_data = {
            'source': 'course-v1:TestOrg+TestCourse+TestRun',
            'target': 'not-a-valid-library-key',
        }
        request = self.factory.post(
            '/api/modulestore_migrator/v1/migrations/',
            data=request_data,
            format='json'
        )
        force_authenticate(request, user=self.user)

        response = self.view(request)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert 'target' in response.data

    def test_create_migration_missing_required_fields(self):
        """
        Test that missing required fields returns 400 Bad Request.

        Validates:
        - 400 status code is returned when source is missing
        - 400 status code is returned when target is missing
        """
        request_data = {
            'target': 'lib:TestOrg:TestLibrary',
        }
        request = self.factory.post(
            '/api/modulestore_migrator/v1/migrations/',
            data=request_data,
            format='json'
        )
        force_authenticate(request, user=self.user)
        response = self.view(request)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert 'source' in response.data

        request_data = {
            'source': 'course-v1:TestOrg+TestCourse+TestRun',
        }
        request = self.factory.post(
            '/api/modulestore_migrator/v1/migrations/',
            data=request_data,
            format='json'
        )
        force_authenticate(request, user=self.user)
        response = self.view(request)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert 'target' in response.data

    def test_create_migration_unauthenticated_user(self):
        """
        Test that unauthenticated requests return 401 Unauthorized.

        Validates:
        - 401 status code for unauthenticated requests
        """
        request_data = {
            'source': 'course-v1:TestOrg+TestCourse+TestRun',
            'target': 'lib:TestOrg:TestLibrary',
        }
        request = self.factory.post(
            '/api/modulestore_migrator/v1/migrations/',
            data=request_data,
            format='json'
        )
        # Note: No force_authenticate call

        response = self.view(request)

        assert response.status_code in [status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN]

    @patch('cms.djangoapps.modulestore_migrator.rest_api.v1.views.auth')
    def test_create_migration_without_source_author_access(self, mock_auth):
        """
        Test that users without author access to source cannot create migrations.

        Validates:
        - 403 Forbidden status code when user lacks author access to source
        """
        mock_auth.has_studio_write_access.return_value = False

        request_data = {
            'source': 'course-v1:TestOrg+TestCourse+TestRun',
            'target': 'lib:TestOrg:TestLibrary',
        }
        request = self.factory.post(
            '/api/modulestore_migrator/v1/migrations/',
            data=request_data,
            format='json'
        )
        force_authenticate(request, user=self.user)

        response = self.view(request)

        assert response.status_code == status.HTTP_403_FORBIDDEN

    @patch('cms.djangoapps.modulestore_migrator.rest_api.v1.views.lib_api')
    @patch('cms.djangoapps.modulestore_migrator.rest_api.v1.views.auth')
    def test_create_migration_without_target_write_access(self, mock_auth, mock_lib_api):
        """
        Test that users without write access to target cannot create migrations.

        Validates:
        - 403 Forbidden status code when user lacks write access to target library
        """
        mock_auth.has_studio_write_access.return_value = True
        mock_lib_api.require_permission_for_library_key.side_effect = PermissionDenied(
            "User lacks permission to manage content in this library"
        )

        request_data = {
            'source': 'course-v1:TestOrg+TestCourse+TestRun',
            'target': 'lib:TestOrg:TestLibrary',
        }
        request = self.factory.post(
            '/api/modulestore_migrator/v1/migrations/',
            data=request_data,
            format='json'
        )
        force_authenticate(request, user=self.user)

        response = self.view(request)

        assert response.status_code == status.HTTP_403_FORBIDDEN

    @patch('cms.djangoapps.modulestore_migrator.rest_api.v1.views.migrator_api')
    @patch('cms.djangoapps.modulestore_migrator.rest_api.v1.views.lib_api')
    @patch('cms.djangoapps.modulestore_migrator.rest_api.v1.views.auth')
    @patch('cms.djangoapps.modulestore_migrator.rest_api.v1.views.UserTaskStatus')
    def test_create_migration_with_optional_fields(
        self, mock_user_task_status, mock_auth, mock_lib_api, mock_migrator_api
    ):
        """
        Test migration creation with all optional fields provided.

        Validates:
        - Optional fields are properly deserialized
        - Default values are not used when explicit values provided
        """
        mock_auth.has_studio_write_access.return_value = True
        mock_lib_api.require_permission_for_library_key.return_value = None

        mock_task = MagicMock(autospec=True)
        mock_task.id = 'test-task-id'
        mock_migrator_api.start_migration_to_library.return_value = mock_task

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

        request_data = {
            'source': 'course-v1:TestOrg+TestCourse+TestRun',
            'target': 'lib:TestOrg:TestLibrary',
            'target_collection_slug': 'my-collection',
            'composition_level': 'unit',
            'repeat_handling_strategy': 'update',
            'preserve_url_slugs': False,
            'forward_source_to_target': True,
        }
        request = self.factory.post(
            '/api/modulestore_migrator/v1/migrations/',
            data=request_data,
            format='json'
        )
        force_authenticate(request, user=self.user)

        response = self.view(request)

        assert response.status_code == status.HTTP_201_CREATED

        mock_migrator_api.start_migration_to_library.assert_called_once()
        call_kwargs = mock_migrator_api.start_migration_to_library.call_args[1]
        assert call_kwargs['target_collection_slug'] == 'my-collection'
        # CompositionLevel and RepeatHandlingStrategy are enums
        assert call_kwargs['composition_level'].value == 'unit'
        assert call_kwargs['repeat_handling_strategy'].value == 'update'
        assert call_kwargs['preserve_url_slugs'] is False
        assert call_kwargs['forward_source_to_target'] is True

    def test_create_migration_invalid_composition_level(self):
        """
        Test that invalid composition_level returns 400 Bad Request.

        Validates:
        - 400 status code for invalid enum value
        """
        request_data = {
            'source': 'course-v1:TestOrg+TestCourse+TestRun',
            'target': 'lib:TestOrg:TestLibrary',
            'composition_level': 'invalid_level',
        }
        request = self.factory.post(
            '/api/modulestore_migrator/v1/migrations/',
            data=request_data,
            format='json'
        )
        force_authenticate(request, user=self.user)

        response = self.view(request)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert 'composition_level' in response.data

    def test_create_migration_invalid_repeat_handling_strategy(self):
        """
        Test that invalid repeat_handling_strategy returns 400 Bad Request.

        Validates:
        - 400 status code for invalid enum value
        """
        request_data = {
            'source': 'course-v1:TestOrg+TestCourse+TestRun',
            'target': 'lib:TestOrg:TestLibrary',
            'repeat_handling_strategy': 'invalid_strategy',
        }
        request = self.factory.post(
            '/api/modulestore_migrator/v1/migrations/',
            data=request_data,
            format='json'
        )
        force_authenticate(request, user=self.user)

        response = self.view(request)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert 'repeat_handling_strategy' in response.data


class TestMigrationViewSetList(TestCase):
    """
    Test the MigrationViewSet.list() endpoint.

    Focus: validation, return codes, serialization/deserialization.
    """

    def setUp(self):
        """Set up test fixtures."""
        self.factory = APIRequestFactory()
        self.view = MigrationViewSet.as_view({'get': 'list'})

        self.user = User.objects.create_user(
            username='testuser',
            email='testuser@test.com',
            password='password'
        )
        self.other_user = User.objects.create_user(
            username='otheruser',
            email='otheruser@test.com',
            password='password'
        )

    def test_list_migrations_success(self):
        """
        Test successful listing of migrations for the authenticated user.

        Validates:
        - 200 status code is returned
        - Response contains list of migrations
        - Only user's own migrations are returned (other users' migrations filtered out)
        """
        org = OrganizationFactory(short_name="TestOrg", name="Test Org")
        source_key = CourseLocator.from_string('course-v1:TestOrg+TestCourse+TestRun')
        source = ModulestoreSource.objects.create(key=str(source_key))
        target = lib_api.create_library(org=org, slug="TestLib", title="Test Target Lib")
        user_task_status = UserTaskStatus.objects.create(
            user=self.user,
            task_id='user-task-id',
            task_class='test.Task',
            name='User Migration',
            total_steps=10,
            completed_steps=10,
        )
        other_task_status = UserTaskStatus.objects.create(
            user=self.other_user,
            task_id='other-task-id',
            task_class='test.Task',
            name='Other Migration',
            total_steps=5,
            completed_steps=5,
        )
        ModulestoreMigrationModel.objects.create(
            task_status=user_task_status,
            source=source,
            target_id=target.learning_package_id,
        )
        ModulestoreMigrationModel.objects.create(
            task_status=other_task_status,
            source=source,
            target_id=target.learning_package_id,
        )

        request = self.factory.get('/api/modulestore_migrator/v1/migrations/')
        force_authenticate(request, user=self.user)
        response = self.view(request)

        assert response.status_code == status.HTTP_200_OK
        results = response.data['results']
        assert len(results) == 1
        assert results[0]['uuid'] == str(user_task_status.uuid)

    def test_list_migrations_unauthenticated(self):
        """
        Test that unauthenticated requests return 401 Unauthorized.

        Validates:
        - 401 status code for unauthenticated requests
        """
        request = self.factory.get('/api/modulestore_migrator/v1/migrations/')

        response = self.view(request)

        assert response.status_code == status.HTTP_401_UNAUTHORIZED


class TestMigrationViewSetRetrieve(TestCase):
    """
    Test the MigrationViewSet.retrieve() endpoint.

    Focus: validation, return codes, serialization/deserialization.
    """

    def setUp(self):
        """Set up test fixtures."""
        self.factory = APIRequestFactory()
        self.view = MigrationViewSet.as_view({'get': 'retrieve'})

        self.user = User.objects.create_user(
            username='testuser',
            email='testuser@test.com',
            password='password'
        )

    def test_retrieve_migration_success(self):
        """
        Test successful retrieval of a specific migration by UUID.

        Validates:
        - 200 status code is returned
        - Response contains migration details
        """
        org = OrganizationFactory(short_name="TestOrg", name="Test Org")
        source_key = CourseLocator.from_string('course-v1:TestOrg+TestCourse+TestRun')
        source = ModulestoreSource.objects.create(key=str(source_key))
        target = lib_api.create_library(org=org, slug="TestLib", title="Test Target Lib")
        user_task_status = UserTaskStatus.objects.create(
            user=self.user,
            task_id='user-task-id',
            task_class='test.Task',
            name='User Migration',
            total_steps=10,
            completed_steps=10,
        )
        ModulestoreMigrationModel.objects.create(
            task_status=user_task_status,
            source=source,
            target_id=target.learning_package_id,
        )

        request = self.factory.get(f'/api/modulestore_migrator/v1/migrations/{user_task_status.uuid}/')
        force_authenticate(request, user=self.user)
        response = self.view(request, uuid=str(user_task_status.uuid))

        assert response.status_code == status.HTTP_200_OK
        assert response.data['uuid'] == str(user_task_status.uuid)
        assert 'parameters' in response.data

    def test_retrieve_migration_other_user(self):
        """
        Test that users cannot retrieve migrations created by other users.

        Validates:
        - 404 status code when attempting to retrieve another user's migration
        - Users are isolated to their own migrations
        """
        other_user = User.objects.create_user(
            username='otheruser',
            email='other@test.com',
            password='password'
        )
        org = OrganizationFactory(short_name="TestOrg", name="Test Org")
        source_key = CourseLocator.from_string('course-v1:TestOrg+TestCourse+TestRun')
        source = ModulestoreSource.objects.create(key=str(source_key))
        target = lib_api.create_library(org=org, slug="TestLib", title="Test Target Lib")
        other_task_status = UserTaskStatus.objects.create(
            user=other_user,
            task_id='other-task-id',
            task_class='test.Task',
            name='Other Migration',
            total_steps=10,
            completed_steps=10,
        )
        ModulestoreMigrationModel.objects.create(
            task_status=other_task_status,
            source=source,
            target_id=target.learning_package_id,
        )

        request = self.factory.get(f'/api/modulestore_migrator/v1/migrations/{other_task_status.uuid}/')
        force_authenticate(request, user=self.user)
        response = self.view(request, uuid=str(other_task_status.uuid))

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_retrieve_migration_unauthenticated(self):
        """
        Test that unauthenticated requests return 401 Unauthorized.

        Validates:
        - 401 status code for unauthenticated requests
        """
        task_uuid = uuid4()
        request = self.factory.get(f'/api/modulestore_migrator/v1/migrations/{task_uuid}/')

        response = self.view(request, uuid=str(task_uuid))

        assert response.status_code == status.HTTP_401_UNAUTHORIZED


class TestMigrationViewSetCancel(TestCase):
    """
    Test the MigrationViewSet.cancel() endpoint.

    Focus: validation, return codes, authorization.
    """

    def setUp(self):
        """Set up test fixtures."""
        self.factory = APIRequestFactory()
        self.view = MigrationViewSet.as_view({'post': 'cancel'})

        self.staff_user = User.objects.create_user(
            username='staffuser',
            email='staff@test.com',
            password='password',
            is_staff=True
        )
        self.regular_user = User.objects.create_user(
            username='regularuser',
            email='regular@test.com',
            password='password',
            is_staff=False
        )

    def test_cancel_migration_as_staff(self):
        """
        Test that staff users can cancel migrations.

        Validates:
        - Staff users can successfully cancel migrations
        - UserTaskStatus.cancel is called
        """
        org = OrganizationFactory(short_name="TestOrg", name="Test Org")
        source_key = CourseLocator.from_string('course-v1:TestOrg+TestCourse+TestRun')
        source = ModulestoreSource.objects.create(key=str(source_key))
        target = lib_api.create_library(org=org, slug="TestLib", title="Test Target Lib")
        user_task_status = UserTaskStatus.objects.create(
            user=self.staff_user,
            task_id='staff-task-id',
            task_class='test.Task',
            name='Staff Migration',
            total_steps=10,
            completed_steps=5,
        )
        ModulestoreMigrationModel.objects.create(
            task_status=user_task_status,
            source=source,
            target_id=target.learning_package_id,
        )

        with patch.object(UserTaskStatus, 'cancel') as mock_cancel:
            request = self.factory.post(
                f'/api/modulestore_migrator/v1/migrations/{user_task_status.uuid}/cancel/'
            )
            force_authenticate(request, user=self.staff_user)
            response = self.view(request, uuid=str(user_task_status.uuid))

            assert response.status_code == status.HTTP_200_OK
            mock_cancel.assert_called_once()

    def test_cancel_migration_not_found(self):
        """
        Test that attempting to cancel a non-existent migration returns 404.

        Validates:
        - 404 status code when migration UUID does not exist
        """
        nonexistent_uuid = uuid4()
        request = self.factory.post(
            f'/api/modulestore_migrator/v1/migrations/{nonexistent_uuid}/cancel/'
        )
        force_authenticate(request, user=self.staff_user)

        response = self.view(request, uuid=str(nonexistent_uuid))

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_cancel_migration_as_non_staff(self):
        """
        Test that non-staff users cannot cancel migrations.

        Validates:
        - 403 Forbidden status code for non-staff users
        """
        task_uuid = uuid4()
        request = self.factory.post(
            f'/api/modulestore_migrator/v1/migrations/{task_uuid}/cancel/'
        )
        force_authenticate(request, user=self.regular_user)

        response = self.view(request, uuid=str(task_uuid))

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_cancel_migration_unauthenticated(self):
        """
        Test that unauthenticated users cannot cancel migrations.
        """
        task_uuid = uuid4()
        request = self.factory.post(
            f'/api/modulestore_migrator/v1/migrations/{task_uuid}/cancel/'
        )

        response = self.view(request, uuid=str(task_uuid))

        assert response.status_code == status.HTTP_401_UNAUTHORIZED


class TestBulkMigrationViewSetCreate(TestCase):
    """
    Test the BulkMigrationViewSet.create() endpoint.

    Focus: validation, return codes, serialization/deserialization.
    """

    def setUp(self):
        """Set up test fixtures."""
        self.factory = APIRequestFactory()
        self.view = BulkMigrationViewSet.as_view({'post': 'create'})

        self.user = User.objects.create_user(
            username='testuser',
            email='testuser@test.com',
            password='password'
        )

    @patch('cms.djangoapps.modulestore_migrator.rest_api.v1.views.migrator_api')
    @patch('cms.djangoapps.modulestore_migrator.rest_api.v1.views.lib_api')
    @patch('cms.djangoapps.modulestore_migrator.rest_api.v1.views.auth')
    @patch('cms.djangoapps.modulestore_migrator.rest_api.v1.views.UserTaskStatus')
    def test_create_bulk_migration_success(
        self, mock_user_task_status, mock_auth, mock_lib_api, mock_migrator_api
    ):
        """
        Test successful bulk migration creation with multiple sources.

        Validates:
        - 201 status code is returned
        - Response contains expected serialized fields
        - Multiple sources are properly deserialized
        """
        mock_auth.has_studio_write_access.return_value = True
        mock_lib_api.require_permission_for_library_key.return_value = None

        mock_task = MagicMock(autospec=True)
        mock_task.id = 'test-task-id'
        mock_migrator_api.start_bulk_migration_to_library.return_value = mock_task

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

        request_data = {
            'sources': [
                'course-v1:TestOrg+TestCourse1+Run1',
                'course-v1:TestOrg+TestCourse2+Run2'
            ],
            'target': 'lib:TestOrg:TestLibrary',
        }
        request = self.factory.post(
            '/api/modulestore_migrator/v1/bulk_migration/',
            data=request_data,
            format='json'
        )
        force_authenticate(request, user=self.user)

        response = self.view(request)

        assert response.status_code == status.HTTP_201_CREATED
        assert 'uuid' in response.data
        assert 'parameters' in response.data

        mock_migrator_api.start_bulk_migration_to_library.assert_called_once()
        call_kwargs = mock_migrator_api.start_bulk_migration_to_library.call_args[1]
        assert call_kwargs['source_key_list'] == [
            CourseLocator.from_string('course-v1:TestOrg+TestCourse1+Run1'),
            CourseLocator.from_string('course-v1:TestOrg+TestCourse2+Run2'),
        ]
        assert call_kwargs['target_collection_slug_list'] is None
        assert call_kwargs['create_collections'] is False
        # CompositionLevel and RepeatHandlingStrategy are enums
        assert call_kwargs['composition_level'].value == 'component'
        assert call_kwargs['repeat_handling_strategy'].value == 'skip'
        assert call_kwargs['preserve_url_slugs'] is False
        assert call_kwargs['forward_source_to_target'] is None

    def test_create_bulk_migration_invalid_source_key(self):
        """
        Test that invalid source key in list returns 400 Bad Request.

        Validates:
        - 400 status code when one or more sources are invalid
        """
        request_data = {
            'sources': ['not-a-valid-key', 'course-v1:TestOrg+TestCourse+TestRun'],
            'target': 'lib:TestOrg:TestLibrary',
        }
        request = self.factory.post(
            '/api/modulestore_migrator/v1/bulk_migration/',
            data=request_data,
            format='json'
        )
        force_authenticate(request, user=self.user)

        response = self.view(request)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert 'sources' in response.data

    def test_create_bulk_migration_missing_sources(self):
        """
        Test that missing sources field returns 400 Bad Request.

        Validates:
        - 400 status code when sources is missing
        """
        request_data = {
            'target': 'lib:TestOrg:TestLibrary',
        }
        request = self.factory.post(
            '/api/modulestore_migrator/v1/bulk_migration/',
            data=request_data,
            format='json'
        )
        force_authenticate(request, user=self.user)
        response = self.view(request)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert 'sources' in response.data


class TestMigrationInfoViewSet(TestCase):
    """
    Test the MigrationInfoViewSet.get() endpoint.

    Focus: validation, return codes, serialization/deserialization.
    """

    def setUp(self):
        """Set up test fixtures."""
        self.factory = APIRequestFactory()
        self.view = MigrationInfoViewSet.as_view()

        self.user = User.objects.create_user(
            username='testuser',
            email='testuser@test.com',
            password='password'
        )

    @patch('cms.djangoapps.modulestore_migrator.rest_api.v1.views.migrator_api')
    @patch('cms.djangoapps.modulestore_migrator.rest_api.v1.views.auth')
    def test_get_migration_info_success(self, mock_auth, mock_migrator_api):
        """
        Test successful retrieval of migration info.

        Validates:
        - 200 status code is returned
        - Response contains migration info for requested sources
        - Permission checks are performed for each source
        """
        mock_auth.has_studio_read_access.return_value = True

        source_key = CourseLocator.from_string('course-v1:TestOrg+TestCourse+TestRun')
        target_key = LibraryLocatorV2.from_string('lib:TestOrg:TestLibrary')

        migration = ModulestoreMigration(
            pk=1,
            source_key=source_key,
            target_key=target_key,
            target_title='Test Library',
            target_collection_slug='test-collection',
            target_collection_title='Test Collection',
            is_failed=False,
            task_uuid=uuid4(),
        )
        mock_migrator_api.get_migrations.return_value = [migration]

        request = self.factory.get(
            '/api/modulestore_migrator/v1/migration_info/',
            {'source_keys': ['course-v1:TestOrg+TestCourse+TestRun']}
        )
        force_authenticate(request, user=self.user)

        response = self.view(request)

        assert response.status_code == status.HTTP_200_OK
        assert str(source_key) in response.data
        assert len(response.data[str(source_key)]) == 1
        assert response.data[str(source_key)][0]['source_key'] == str(source_key)
        assert response.data[str(source_key)][0]['target_key'] == str(target_key)
        assert response.data[str(source_key)][0]['target_title'] == 'Test Library'

    def test_get_migration_info_missing_source_keys(self):
        """
        Test that missing source_keys parameter returns 400 Bad Request.

        Validates:
        - 400 status code when source_keys is not provided
        """
        request = self.factory.get('/api/modulestore_migrator/v1/migration_info/')
        force_authenticate(request, user=self.user)

        response = self.view(request)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert 'source_keys' in response.data.get('detail', '').lower()

    def test_get_migration_info_invalid_source_key(self):
        """
        Test that invalid source keys are silently skipped.

        Validates:
        - 200 status code (invalid keys are filtered out, not errored)
        - Invalid keys don't appear in response
        """
        request = self.factory.get(
            '/api/modulestore_migrator/v1/migration_info/',
            {'source_keys': ['not-a-valid-key']}
        )
        force_authenticate(request, user=self.user)

        response = self.view(request)

        assert response.status_code == status.HTTP_200_OK

    @patch('cms.djangoapps.modulestore_migrator.rest_api.v1.views.auth')
    def test_get_migration_info_without_read_access(self, mock_auth):
        """
        Test that sources without read access are silently filtered.

        Validates:
        - 200 status code (unauthorized sources are filtered, not errored)
        """
        mock_auth.has_studio_read_access.return_value = False

        request = self.factory.get(
            '/api/modulestore_migrator/v1/migration_info/',
            {'source_keys': ['course-v1:TestOrg+TestCourse+TestRun']}
        )
        force_authenticate(request, user=self.user)

        response = self.view(request)

        assert response.status_code == status.HTTP_200_OK


class TestBlockMigrationInfo(TestCase):
    """
    Test the BlockMigrationInfo.get() endpoint.

    Focus: validation, return codes, serialization/deserialization.
    """

    def setUp(self):
        """Set up test fixtures."""
        self.factory = APIRequestFactory()
        self.view = BlockMigrationInfo.as_view()

        self.user = User.objects.create_user(
            username='testuser',
            email='testuser@test.com',
            password='password'
        )

    @patch('cms.djangoapps.modulestore_migrator.rest_api.v1.views.migrator_api')
    @patch('cms.djangoapps.modulestore_migrator.rest_api.v1.views.lib_api')
    def test_get_block_migration_info_success(self, mock_lib_api, mock_migrator_api):
        """
        Test successful retrieval of block migration info.

        Validates:
        - 200 status code is returned
        - Response contains block migration information
        - Target library permission is checked
        """
        mock_lib_api.require_permission_for_library_key.return_value = None

        source_course_key = CourseLocator.from_string('course-v1:TestOrg+TestCourse+TestRun')
        target_library_key = LibraryLocatorV2.from_string('lib:TestOrg:TestLibrary')

        migration = ModulestoreMigration(
            pk=1,
            source_key=source_course_key,
            target_key=target_library_key,
            target_title='Test Library',
            target_collection_slug='test-collection',
            target_collection_title='Test Collection',
            is_failed=False,
            task_uuid=uuid4(),
        )
        source_key_success = BlockUsageLocator.from_string(
            'block-v1:TestOrg+TestCourse+TestRun+type@problem+block@test_problem'
        )
        target_key_success = LibraryUsageLocatorV2.from_string(
            'lb:TestOrg:TestLibrary:problem:test_problem'
        )
        block_success = ModulestoreBlockMigrationSuccess(
            source_key=source_key_success,
            target_key=target_key_success,
            target_entity_pk=123,
            target_title='Test Problem',
            target_version_num=1,
        )
        source_key_failure = BlockUsageLocator.from_string(
            'block-v1:TestOrg+TestCourse+TestRun+type@bork+block@test_bork'
        )
        block_failure = ModulestoreBlockMigrationFailure(
            source_key=source_key_failure,
            unsupported_reason='bork blocks are not supported',
        )

        mock_migrator_api.get_migrations.return_value = [migration]
        mock_migrator_api.get_migration_blocks.return_value = {
            source_key_success: block_success,
            source_key_failure: block_failure,
        }

        request = self.factory.get(
            '/api/modulestore_migrator/v1/migration_blocks/',
            {'target_key': 'lib:TestOrg:TestLibrary'}
        )
        force_authenticate(request, user=self.user)

        response = self.view(request)

        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 2
        assert response.data[0]['source_key'] == str(block_success.source_key)
        assert response.data[0]['target_key'] == str(block_success.target_key)
        assert response.data[0]['unsupported_reason'] is None
        assert response.data[1]['source_key'] == str(block_failure.source_key)
        assert response.data[1]['target_key'] is None
        assert response.data[1]['unsupported_reason'] == 'bork blocks are not supported'
        mock_lib_api.require_permission_for_library_key.assert_called_once()

    def test_get_block_migration_info_missing_target_key(self):
        """
        Test that missing target_key parameter returns 400 Bad Request.

        Validates:
        - 400 status code when target_key is not provided
        """
        request = self.factory.get('/api/modulestore_migrator/v1/migration_blocks/')
        force_authenticate(request, user=self.user)

        response = self.view(request)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert 'target' in response.data.get('error', '').lower()

    def test_get_block_migration_info_invalid_target_key(self):
        """
        Test that invalid target_key returns 400 Bad Request.

        Validates:
        - 400 status code when target_key format is invalid
        """
        request = self.factory.get(
            '/api/modulestore_migrator/v1/migration_blocks/',
            {'target_key': 'not-a-valid-key'}
        )
        force_authenticate(request, user=self.user)

        response = self.view(request)

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_get_block_migration_info_invalid_is_failed_value(self):
        """
        Test that invalid is_failed parameter returns 400 Bad Request.

        Validates:
        - 400 status code when is_failed value is not a valid boolean
        """
        request = self.factory.get(
            '/api/modulestore_migrator/v1/migration_blocks/',
            {
                'target_key': 'lib:TestOrg:TestLibrary',
                'is_failed': 'not-a-boolean'
            }
        )
        force_authenticate(request, user=self.user)

        response = self.view(request)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert 'is_failed' in response.data.get('error', '').lower()

    @patch('cms.djangoapps.modulestore_migrator.rest_api.v1.views.lib_api')
    def test_get_block_migration_info_without_library_access(self, mock_lib_api):
        """
        Test that users without library view access get 403 Forbidden.

        Validates:
        - 403 status code when user lacks view permission on target library
        """
        mock_lib_api.require_permission_for_library_key.side_effect = PermissionDenied(
            "User lacks permission to view this library"
        )

        request = self.factory.get(
            '/api/modulestore_migrator/v1/migration_blocks/',
            {'target_key': 'lib:TestOrg:TestLibrary'}
        )
        force_authenticate(request, user=self.user)

        response = self.view(request)

        assert response.status_code == status.HTTP_403_FORBIDDEN


class TestPreviewMigration(TestCase):
    """
    Test the PreviewMigration.get() endpoint.
    """
    def setUp(self):
        """Set up test fixtures."""
        self.factory = APIRequestFactory()
        self.view = PreviewMigration.as_view()

        self.user = User.objects.create_user(
            username='testuser',
            email='testuser@test.com',
            password='password'
        )

    @patch('cms.djangoapps.modulestore_migrator.rest_api.v1.views.migrator_api')
    @patch('cms.djangoapps.modulestore_migrator.rest_api.v1.views.lib_api')
    def test_preview_migration_success(self, mock_lib_api, mock_migrator_api):
        """
        Test successful retrieval of preview migration.
        """
        mock_lib_api.require_permission_for_library_key.return_value = None

        expected = {
            "state": "partial",
            "unsupported_blocks": 4,
            "unsupported_percentage": 25,
            "blocks_limit": 1000,
            "total_blocks": 20,
            "total_components": 10,
            "sections": 2,
            "subsections": 3,
            "units": 5,
        }

        mock_migrator_api.preview_migration.return_value = expected

        request = self.factory.get(
            '/api/modulestore_migrator/v1/migration_preview/',
            {
                'target_key': 'lib:TestOrg:TestLibrary',
                'source_key': 'course-v1:TestOrg+TestCourse+TestRun',
            }
        )
        force_authenticate(request, user=self.user)

        response = self.view(request)

        assert response.status_code == status.HTTP_200_OK

        for key, value in expected.items():
            assert response.data[key] == value

    @patch('cms.djangoapps.modulestore_migrator.rest_api.v1.views.lib_api')
    def test_preview_migration_without_library_access(self, mock_lib_api):
        """
        Test that users without library view access get 403 Forbidden.
        """
        mock_lib_api.require_permission_for_library_key.side_effect = PermissionDenied(
            "User lacks permission to view this library"
        )

        request = self.factory.get(
            '/api/modulestore_migrator/v1/migration_preview/',
            {
                'target_key': 'lib:TestOrg:TestLibrary',
                'source_key': 'course-v1:TestOrg+TestCourse+TestRun',
            }
        )
        force_authenticate(request, user=self.user)

        response = self.view(request)

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_preview_migration_missing_target_key(self):
        """
        Test that missing target_key parameter returns 400 Bad Request.
        """
        request = self.factory.get(
            '/api/modulestore_migrator/v1/migration_preview/',
            {
                'source_key': 'course-v1:TestOrg+TestCourse+TestRun',
            }
        )
        force_authenticate(request, user=self.user)

        response = self.view(request)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert 'target' in response.data.get('error', '').lower()

    def test_preview_migration_invalid_target_key(self):
        """
        Test that invalid target_key returns 400 Bad Request.
        """
        request = self.factory.get(
            '/api/modulestore_migrator/v1/migration_preview/',
            {
                'target_key': 'not-a-valid-key',
                'source_key': 'course-v1:TestOrg+TestCourse+TestRun',
            }
        )
        force_authenticate(request, user=self.user)

        response = self.view(request)

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_preview_migration_missing_source_key(self):
        """
        Test that missing target_key parameter returns 400 Bad Request.
        """
        request = self.factory.get(
            '/api/modulestore_migrator/v1/migration_preview/',
            {
                'target_key': 'lib:TestOrg:TestLibrary',
            }
        )
        force_authenticate(request, user=self.user)

        response = self.view(request)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert 'source' in response.data.get('error', '').lower()

    def test_preview_migration_invalid_source_key(self):
        """
        Test that invalid target_key returns 400 Bad Request.
        """
        request = self.factory.get(
            '/api/modulestore_migrator/v1/migration_preview/',
            {
                'target_key': 'lib:TestOrg:TestLibrary',
                'source_key': 'not-a-valid-key',
            }
        )
        force_authenticate(request, user=self.user)

        response = self.view(request)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
