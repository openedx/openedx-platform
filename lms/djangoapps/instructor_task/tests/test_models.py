"""
Tests for instructor_task/models.py.
"""


import copy
import time
from io import BytesIO, StringIO

import pytest
from django.conf import settings
from django.test import SimpleTestCase, TestCase, override_settings
from opaque_keys.edx.locator import CourseLocator

from common.test.utils import MockS3Boto3Mixin
from lms.djangoapps.instructor_task.models import TASK_INPUT_LENGTH, InstructorTask, ReportStore
from lms.djangoapps.instructor_task.tests.test_base import TestReportMixin


class TestInstructorTasksModel(TestCase):
    """
    Test validations in instructor task model
    """
    def test_task_input_valid_length(self):
        """
        Test allowed length of task_input field
        """
        task_input = 's' * TASK_INPUT_LENGTH
        with pytest.raises(AttributeError):
            InstructorTask.create(
                course_id='dummy_course_id',
                task_type='dummy type',
                task_key='dummy key',
                task_input=task_input,
                requester='dummy requester',
            )


class ReportStoreTestMixin:
    """
    Mixin for report store tests.
    """

    def setUp(self):
        super().setUp()
        self.course_id = CourseLocator(org="testx", course="coursex", run="runx")

    def create_report_store(self):
        """
        Subclasses should override this and return their report store.
        """
        pass  # pylint: disable=unnecessary-pass

    def test_store_streams_binary_buffer(self):
        """
        A binary buffer should reach the storage backend without first being
        read into memory in its entirety.

        Asserted behaviourally rather than by inspecting call args: a streaming
        backend reads in bounded chunks (File.chunks / upload_fileobj both pass
        an explicit size), so an unbounded read() is the signature of the whole
        report being slurped into RAM before upload.
        """
        unbounded_reads = []

        class _RecordingBytesIO(BytesIO):
            """BytesIO that notes any read() not bounded by an explicit size."""
            def read(self, size=-1, /):
                if size is None or size < 0:
                    unbounded_reads.append(size)
                return super().read(size)

        report_store = self.create_report_store()  # pylint: disable=assignment-from-no-return
        payload = b'student_id,grade\n' + b'1,0.5\n' * 5000

        report_store.store(self.course_id, 'streamed.csv', _RecordingBytesIO(payload))

        assert unbounded_reads == []
        with report_store.storage.open(report_store.path_to(self.course_id, 'streamed.csv')) as stored:
            assert stored.read() == payload

    def test_store_text_buffer_round_trips(self):
        """
        Text buffers are still accepted, and are utf-8 encoded on the way out.

        Not every caller has a binary file to hand, so this path has to keep
        working even though it cannot stream.
        """
        report_store = self.create_report_store()  # pylint: disable=assignment-from-no-return
        contents = 'student_id,grade\n1,0.5\nüser,1.0\n'

        report_store.store(self.course_id, 'text.csv', StringIO(contents))

        with report_store.storage.open(report_store.path_to(self.course_id, 'text.csv')) as stored:
            assert stored.read() == contents.encode('utf-8')

    def test_store_rows_round_trips(self):
        """
        store_rows() builds its CSV in a binary buffer, so it takes the
        streaming path too. Verify the bytes on disk are unchanged by that.
        """
        report_store = self.create_report_store()  # pylint: disable=assignment-from-no-return

        report_store.store_rows(
            self.course_id,
            'rows.csv',
            [['student_id', 'grade'], [1, 0.5], ['üser', 'Not Attempted']],
        )

        with report_store.storage.open(report_store.path_to(self.course_id, 'rows.csv')) as stored:
            assert stored.read().decode('utf-8').splitlines() == [
                'student_id,grade',
                '1,0.5',
                'üser,Not Attempted',
            ]

    def test_links_for_order(self):
        """
        Test that ReportStore.links_for() returns file download links
        in reverse chronological order.
        """
        report_store = self.create_report_store()  # pylint: disable=assignment-from-no-return
        assert report_store.links_for(self.course_id) == []

        report_store.store(self.course_id, 'old_file', StringIO())
        time.sleep(1)  # Ensure we have a unique timestamp.
        report_store.store(self.course_id, 'middle_file', StringIO())
        time.sleep(1)  # Ensure we have a unique timestamp.
        report_store.store(self.course_id, 'new_file', StringIO())

        assert [link[0] for link in report_store.links_for(self.course_id)] == ['new_file', 'middle_file', 'old_file']


class LocalFSReportStoreTestCase(ReportStoreTestMixin, TestReportMixin, SimpleTestCase):
    """
    Test the old LocalFSReportStore configuration.
    """
    def create_report_store(self):
        """
        Create and return a DjangoStorageReportStore using the old
        LocalFSReportStore configuration.
        """
        return ReportStore.from_config(config_name='GRADES_DOWNLOAD')


class DjangoStorageReportStoreLocalTestCase(ReportStoreTestMixin, TestReportMixin, SimpleTestCase):
    """
    Test the DjangoStorageReportStore implementation using the local
    filesystem.
    """
    def create_report_store(self):
        """
        Create and return a DjangoStorageReportStore configured to use the
        local filesystem for storage.
        """
        test_settings = copy.deepcopy(settings.GRADES_DOWNLOAD)
        test_settings['STORAGE_KWARGS'] = {'location': settings.GRADES_DOWNLOAD['ROOT_PATH']}
        with override_settings(GRADES_DOWNLOAD=test_settings):
            return ReportStore.from_config(config_name='GRADES_DOWNLOAD')


class DjangoStorageReportStoreS3TestCase(MockS3Boto3Mixin, ReportStoreTestMixin, TestReportMixin, SimpleTestCase):
    """
    Test the DjangoStorageReportStore implementation using S3 stubs.
    """
    def create_report_store(self):
        """
        Create and return a DjangoStorageReportStore configured to use S3 for
        storage.
        """
        test_settings = copy.deepcopy(settings.GRADES_DOWNLOAD)
        test_settings['STORAGE_CLASS'] = 'storages.backends.s3boto3.S3Boto3Storage'
        test_settings['STORAGE_KWARGS'] = {
            'bucket': settings.GRADES_DOWNLOAD['BUCKET'],
            'location': settings.GRADES_DOWNLOAD['ROOT_PATH'],
        }
        with override_settings(GRADES_DOWNLOAD=test_settings):
            self.mocked_connection.create_bucket(settings.GRADES_DOWNLOAD['STORAGE_KWARGS']['bucket'])
            return ReportStore.from_config(config_name='GRADES_DOWNLOAD')


class TestS3ReportStorage(TestCase):
    """
    Test the S3ReportStorage to make sure that configuration overrides from settings.FINANCIAL_REPORTS
    are used instead of default ones.
    """

    def test_financial_report_overrides(self):
        """
        Test that CUSTOM_DOMAIN from FINANCIAL_REPORTS is used to construct file url. instead of domain defined via
        AWS_S3_CUSTOM_DOMAIN setting.
        """
        with override_settings(FINANCIAL_REPORTS={
            'STORAGE_TYPE': 's3',
            'BUCKET': 'edx-financial-reports',
            'CUSTOM_DOMAIN': 'edx-financial-reports.s3.amazonaws.com',
            'ROOT_PATH': 'production',
        }):
            report_store = ReportStore.from_config(config_name="FINANCIAL_REPORTS")
            # Make sure CUSTOM_DOMAIN from FINANCIAL_REPORTS is used to construct file url
            assert 'edx-financial-reports.s3.amazonaws.com' in report_store.storage.url('')
