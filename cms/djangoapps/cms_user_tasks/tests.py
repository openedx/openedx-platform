"""
Unit tests for integration of the django-user-tasks app and its REST API.
"""
import json
import logging
from unittest import mock
from unittest.mock import patch
from uuid import uuid4

import botocore
import ddt
from django.conf import settings
from django.core import mail
from django.test import override_settings
from django.urls import reverse
from edx_toggles.toggles.testutils import override_waffle_flag
from rest_framework.test import APITestCase
from user_tasks.models import UserTaskArtifact, UserTaskStatus
from user_tasks.serializers import ArtifactSerializer, StatusSerializer

from cms.djangoapps.contentstore.toggles import BYPASS_OLX_FAILURE
from common.djangoapps.student.tests.factories import UserFactory

from .signals import user_task_stopped


class MockLoggingHandler(logging.Handler):
    """
    Mock logging handler to help check for logging statements
    """

    def __init__(self, *args, **kwargs):
        self.reset()
        logging.Handler.__init__(self, *args, **kwargs)

    def emit(self, record):
        """
        Override to catch messages and store them messages in our internal dicts
        """
        self.messages[record.levelname.lower()].append(record.getMessage())

    def reset(self):
        """
        Clear out all messages, also called to initially populate messages dict
        """
        self.messages = {
            'debug': [],
            'info': [],
            'warning': [],
            'error': [],
            'critical': [],
        }


# Helper functions for stuff that pylint complains about without disable comments
def _context(response):
    """
    Get a context dictionary for a serializer appropriate for the given response.
    """
    return {'request': response.wsgi_request}


def _data(response):
    """
    Get the serialized data dictionary from the given REST API test response.
    """
    return response.data


@override_settings(BROKER_URL='memory://localhost/')
class TestUserTasks(APITestCase):
    """
    Tests of the django-user-tasks REST API endpoints.

    Detailed tests of the default authorization rules are in the django-user-tasks code.
    These tests just verify that the API is exposed and functioning.
    """

    @classmethod
    def setUpTestData(cls):  # lint-amnesty, pylint: disable=super-method-not-called
        cls.user = UserFactory.create(username='test_user', email='test@example.com', password='password')
        cls.status = UserTaskStatus.objects.create(
            user=cls.user, task_id=str(uuid4()), task_class='test_rest_api.sample_task', name='SampleTask 2',
            total_steps=5)
        cls.artifact = UserTaskArtifact.objects.create(status=cls.status, text='Lorem ipsum')

    def setUp(self):
        super().setUp()
        self.status.refresh_from_db()
        self.client.force_authenticate(self.user)  # pylint: disable=no-member

    def test_artifact_detail(self):
        """
        Users should be able to access artifacts for tasks they triggered.
        """
        response = self.client.get(reverse('usertaskartifact-detail', args=[self.artifact.uuid]))
        assert response.status_code == 200
        serializer = ArtifactSerializer(self.artifact, context=_context(response))
        assert _data(response) == serializer.data

    def test_artifact_list(self):
        """
        Users should be able to access a list of their tasks' artifacts.
        """
        response = self.client.get(reverse('usertaskartifact-list'))
        assert response.status_code == 200
        serializer = ArtifactSerializer(self.artifact, context=_context(response))
        assert _data(response)['results'] == [serializer.data]

    def test_status_cancel(self):
        """
        Users should be able to cancel tasks they no longer wish to complete.
        """
        response = self.client.post(reverse('usertaskstatus-cancel', args=[self.status.uuid]))
        assert response.status_code == 200
        self.status.refresh_from_db()
        assert self.status.state == UserTaskStatus.CANCELED

    def test_status_delete(self):
        """
        Users should be able to delete their own task status records when they're done with them.
        """
        response = self.client.delete(reverse('usertaskstatus-detail', args=[self.status.uuid]))
        assert response.status_code == 204
        assert not UserTaskStatus.objects.filter(pk=self.status.id).exists()

    def test_status_detail(self):
        """
        Users should be able to access status records for tasks they triggered.
        """
        response = self.client.get(reverse('usertaskstatus-detail', args=[self.status.uuid]))
        assert response.status_code == 200
        serializer = StatusSerializer(self.status, context=_context(response))
        assert _data(response) == serializer.data

    def test_status_list(self):
        """
        Users should be able to access a list of their tasks' status records.
        """
        response = self.client.get(reverse('usertaskstatus-list'))
        assert response.status_code == 200
        serializer = StatusSerializer([self.status], context=_context(response), many=True)
        assert _data(response)['results'] == serializer.data


@override_settings(BROKER_URL='memory://localhost/')
@ddt.ddt
class TestUserTaskStopped(APITestCase):
    """
    Tests of the django-user-tasks signal handling and email integration.
    """

    @classmethod
    def setUpTestData(cls):  # lint-amnesty, pylint: disable=super-method-not-called
        cls.user = UserFactory.create(username='test_user', email='test@example.com', password='password')
        cls.status = UserTaskStatus.objects.create(
            user=cls.user, task_id=str(uuid4()), task_class='test_rest_api.sample_task', name='SampleTask 2',
            total_steps=5)
        cls.olx_validations = {
            'errors': ["ERROR DuplicateURLNameError: foo bar", "ERROR MissingFile: foo bar"],
            'warnings': ["WARNING TagMismatch"],
        }

    def setUp(self):
        super().setUp()
        self.status.refresh_from_db()
        self.client.force_authenticate(self.user)  # pylint: disable=no-member

    def create_olx_validation_artifact(self):
        """Creates an olx validation."""
        return UserTaskArtifact.objects.create(
            status=self.status,
            name="OLX_VALIDATION_ERROR",
            text=json.dumps(self.olx_validations)
        )

    def assert_msg_subject(self, msg):
        """Verify that msg subject is in expected format."""
        subject = "{platform_name} {studio_name}: Task Status Update".format(
            platform_name=settings.PLATFORM_NAME, studio_name=settings.STUDIO_NAME
        )
        self.assertEqual(msg.subject, subject)

    def assert_msg_body_fragments(self, msg, body_fragments):
        """Verify that email body contains expected fragments"""
        for fragment in body_fragments:
            self.assertIn(fragment, msg.body)

    def test_email_sent_with_site(self):
        """
        Check the signal receiver and email sending.
        """
        UserTaskArtifact.objects.create(
            status=self.status, name='BASE_URL', url='https://test.edx.org/'
        )
        user_task_stopped.send(sender=UserTaskStatus, status=self.status)

        body_fragments = [
            f"Your {self.status.name.lower()} task has completed with the status",
            "https://test.edx.org/",
            reverse('usertaskstatus-detail', args=[self.status.uuid])
        ]

        self.assertEqual(len(mail.outbox), 1)

        msg = mail.outbox[0]

        self.assert_msg_subject(msg)
        self.assert_msg_body_fragments(msg, body_fragments)

    def test_email_not_sent_with_libary_import_task(self):
        """
        Check that email is not sent when library import task is completed.
        """
        end_of_task_status = self.status
        end_of_task_status.name = "bulk_migrate_from_modulestore"
        user_task_stopped.send(sender=UserTaskStatus, status=end_of_task_status)

        self.assertEqual(len(mail.outbox), 0)

    def test_email_not_sent_with_libary_content_update(self):
        """
        Check the signal receiver and email sending.
        """
        UserTaskArtifact.objects.create(
            status=self.status, name='BASE_URL', url='https://test.edx.org/'
        )
        end_of_task_status = self.status
        end_of_task_status.name = "updating block-v1:course+type@library_content+block@uuid from library"
        user_task_stopped.send(sender=UserTaskStatus, status=end_of_task_status)

        self.assertEqual(len(mail.outbox), 0)

    def test_email_not_sent_with_legacy_libary_content_ref_update(self):
        """
        Check the signal receiver and email sending.
        """
        end_of_task_status = self.status
        end_of_task_status.name = "Updating legacy library content blocks references of course-v1:UNIX+UN1+2025_T4"
        user_task_stopped.send(sender=UserTaskStatus, status=end_of_task_status)

        self.assertEqual(len(mail.outbox), 0)

    def test_email_sent_with_olx_validations_with_config_enabled(self):
        """
        Tests that email is sent with olx validation errors.
        """
        self.status.fail('Olx validation failed.')
        self.create_olx_validation_artifact()
        body_fragments_with_validation = [
            f"Your {self.status.name.lower()} task has completed with the status '{self.status.state}'",
            "Sign in to view the details of your task or download any files created.",
            *self.olx_validations['errors'],
            *self.olx_validations['warnings']
        ]

        with patch.dict(settings.FEATURES, ENABLE_COURSE_OLX_VALIDATION=True):
            user_task_stopped.send(sender=UserTaskStatus, status=self.status)
            msg = mail.outbox[0]

            self.assertEqual(len(mail.outbox), 1)
            self.assert_msg_subject(msg)
            self.assert_msg_body_fragments(msg, body_fragments_with_validation)

    def test_email_sent_with_olx_validations_with_default_config(self):
        """
        Tests that email is sent without olx validation errors.
        """
        self.status.fail('Olx validation failed.')
        self.create_olx_validation_artifact()
        body_fragments = [
            f"Your {self.status.name.lower()} task has completed with the status '{self.status.state}'",
            "Sign in to view the details of your task or download any files created.",
        ]

        user_task_stopped.send(sender=UserTaskStatus, status=self.status)
        msg = mail.outbox[0]

        # Verify olx validation is not enabled out of the box.
        self.assertFalse(settings.FEATURES.get('ENABLE_COURSE_OLX_VALIDATION'))
        self.assertEqual(len(mail.outbox), 1)
        self.assert_msg_subject(msg)
        self.assert_msg_body_fragments(msg, body_fragments)

    @patch.dict(settings.FEATURES, ENABLE_COURSE_OLX_VALIDATION=True)
    @override_waffle_flag(BYPASS_OLX_FAILURE, active=True)
    def test_email_sent_with_olx_validations_with_bypass_flag(self):
        """
        Tests that email does not contain olx validation information
        when bypass setting is enabled.
        """
        self.create_olx_validation_artifact()

        body_fragments = [
            f"Your {self.status.name.lower()} task has completed with the status",
            "Sign in to view the details of your task or download any files created.",
        ]

        user_task_stopped.send(sender=UserTaskStatus, status=self.status)

        self.assertEqual(len(mail.outbox), 1)
        msg = mail.outbox[0]
        self.assert_msg_subject(msg)
        self.assert_msg_body_fragments(msg, body_fragments)
        self.assertNotIn("Here are some validations we found with your course content.", msg.body)

    def test_email_not_sent_for_child(self):
        """
        No email should be send for child tasks in chords, chains, etc.
        """
        child_status = UserTaskStatus.objects.create(
            user=self.user, task_id=str(uuid4()), task_class='test_rest_api.sample_task', name='SampleTask 2',
            total_steps=5, parent=self.status)
        user_task_stopped.send(sender=UserTaskStatus, status=child_status)
        self.assertEqual(len(mail.outbox), 0)

    def test_email_sent_without_site(self):
        """
        Make sure we send a generic email if the BASE_URL artifact doesn't exist
        """
        user_task_stopped.send(sender=UserTaskStatus, status=self.status)

        body_fragments = [
            f"Your {self.status.name.lower()} task has completed with the status",
            "Sign in to view the details of your task or download any files created."
        ]

        self.assertEqual(len(mail.outbox), 1)

        msg = mail.outbox[0]
        self.assert_msg_subject(msg)
        self.assert_msg_body_fragments(msg, body_fragments)

    def test_email_retries(self):
        """
        Make sure we can succeed on retries
        """
        with mock.patch('django.core.mail.send_mail') as mock_exception:
            mock_exception.side_effect = botocore.exceptions.ClientError(
                {'error_response': 'error occurred'}, {'operation_name': 'test'}
            )

            with mock.patch('cms.djangoapps.cms_user_tasks.tasks.send_task_complete_email.retry') as mock_retry:
                user_task_stopped.send(sender=UserTaskStatus, status=self.status)
                self.assertTrue(mock_retry.called)

    def test_queue_email_failure(self):
        logger = logging.getLogger("cms.djangoapps.cms_user_tasks.signals")
        hdlr = MockLoggingHandler(level="DEBUG")
        logger.addHandler(hdlr)

        with mock.patch('cms.djangoapps.cms_user_tasks.tasks.send_task_complete_email.delay') as mock_delay:
            mock_delay.side_effect = botocore.exceptions.ClientError(
                {'error_response': 'error occurred'}, {'operation_name': 'test'}
            )
            user_task_stopped.send(sender=UserTaskStatus, status=self.status)
            self.assertTrue(mock_delay.called)
            self.assertEqual(hdlr.messages['error'][0], 'Unable to queue send_task_complete_email')
