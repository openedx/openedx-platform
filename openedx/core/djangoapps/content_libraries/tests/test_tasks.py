"""
Unit tests for content libraries Celery tasks
"""
from unittest import mock

from django.test import override_settings
from ..models import ContentLibrary
from .base import ContentLibrariesRestApiTest

from openedx.core.djangoapps.content_libraries.tasks import backup_library
from user_tasks.models import UserTaskArtifact


class ContentLibraryBackupTaskTest(ContentLibrariesRestApiTest):
    """
    Tests for Content Library export task.
    """
    SEND_TASK_COMPLETE_FN = 'cms.djangoapps.cms_user_tasks.tasks.send_task_complete_email.delay'

    def setUp(self) -> None:
        super().setUp()

        # Create Content Libraries
        self._create_library("test-lib-task-1", "Test Library Task 1")

        # Fetch the created ContentLibrary objects so we can access their learning_package.id
        self.lib1 = ContentLibrary.objects.get(slug="test-lib-task-1")
        self.wrong_task_id = '11111111-1111-1111-1111-111111111111'

    def test_backup_task_returns_task_id(self):
        result = backup_library.delay(self.user.id, str(self.lib1.library_key))
        assert result.task_id is not None

    @override_settings(CMS_BASE="test.com")
    def test_backup_task_success(self):
        with mock.patch(self.SEND_TASK_COMPLETE_FN) as send_task_complete_email:
            result = backup_library.delay(self.user.id, str(self.lib1.library_key))
        send_task_complete_email.assert_not_called()
        assert result.state == 'SUCCESS'
        # Ensure an artifact was created with the output file
        artifact = UserTaskArtifact.objects.filter(status__task_id=result.task_id, name='Output').first()
        assert artifact is not None
        assert artifact.file.name.endswith('.zip')
        # test artifact content
        with artifact.file.open('rb') as f:
            content = f.read()
            assert b'created_by_email = "bob@example.com"' in content
            assert b'origin_server = "test.com"' in content

    def test_backup_task_failure(self):
        with mock.patch(self.SEND_TASK_COMPLETE_FN) as send_task_complete_email:
            result = backup_library.delay(self.user.id, self.wrong_task_id)
        send_task_complete_email.assert_not_called()
        assert result.state == 'FAILURE'
        # Ensure an error artifact was created
        artifact = UserTaskArtifact.objects.filter(status__task_id=result.task_id, name='Error').first()
        assert artifact is not None
        assert artifact.text is not None
