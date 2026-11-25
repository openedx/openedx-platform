"""
Tests for instructor_task/models.py.
"""


import copy
import time
from datetime import UTC, datetime
from io import BytesIO, StringIO
from types import SimpleNamespace
from unittest.mock import PropertyMock, patch

import pytest
from botocore.exceptions import ClientError
from django.conf import settings
from django.test import SimpleTestCase, TestCase, override_settings
from opaque_keys.edx.locator import CourseLocator
from storages.backends.s3boto3 import S3Boto3Storage

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

        assert not unbounded_reads, f'buffer was read without a size bound: {unbounded_reads}'
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


class _FakeS3Object:
    """
    The parts of a boto3 S3 Object that S3Boto3Storage calls, backed by a dict.
    """

    def __init__(self, s3, key):
        self._s3 = s3
        self.key = key
        self.content_encoding = None

    def upload_fileobj(self, fileobj, ExtraArgs=None, Config=None):  # pylint: disable=invalid-name,unused-argument
        """Consume the upload in bounded chunks, as s3transfer's part uploads do."""
        parts = []
        while chunk := fileobj.read(self._s3.CHUNK_SIZE):
            parts.append(chunk)
        self._s3.objects[self.key] = (b''.join(parts), datetime.now(UTC))

    def load(self, **_kwargs):
        if self.key not in self._s3.objects:
            raise ClientError({'Error': {'Code': '404'}, 'ResponseMetadata': {'HTTPStatusCode': 404}}, 'HeadObject')

    def download_fileobj(self, fileobj, ExtraArgs=None, Config=None):  # pylint: disable=invalid-name,unused-argument
        fileobj.write(self._s3.objects[self.key][0])

    @property
    def content_length(self):
        return len(self._s3.objects[self.key][0])

    @property
    def last_modified(self):
        return self._s3.objects[self.key][1]


class _FakeS3:
    """
    An in-memory stand-in for the boto3 S3 resource behind S3Boto3Storage.

    Only the network layer is replaced: S3Boto3Storage.save and _save run for
    real, so the store's buffer goes through the same wrapping it gets in
    production before it reaches upload_fileobj.
    """
    CHUNK_SIZE = 64 * 1024

    def __init__(self):
        self.objects = {}
        self.meta = SimpleNamespace(client=SimpleNamespace(
            get_paginator=lambda _operation: SimpleNamespace(paginate=self._list_objects),
            generate_presigned_url=self._presigned_url,
        ))

    def Bucket(self, name):  # pylint: disable=invalid-name
        return SimpleNamespace(name=name, Object=lambda key: _FakeS3Object(self, key))

    def _list_objects(self, Bucket, Delimiter, Prefix):  # pylint: disable=invalid-name,unused-argument
        return [{
            'Contents': [
                {'Key': key} for key in sorted(self.objects)
                if key.startswith(Prefix) and Delimiter not in key[len(Prefix):]
            ],
        }]

    @staticmethod
    def _presigned_url(ClientMethod, Params, ExpiresIn, HttpMethod=None):  # pylint: disable=invalid-name,unused-argument
        return f"https://{Params['Bucket']}.s3.test/{Params['Key']}"


class DjangoStorageReportStoreS3TestCase(ReportStoreTestMixin, TestReportMixin, SimpleTestCase):
    """
    Test the DjangoStorageReportStore implementation on S3Boto3Storage, with
    only the S3 API replaced.
    """
    def setUp(self):
        super().setUp()
        self.s3 = _FakeS3()
        connection = patch('storages.backends.s3.S3Storage.connection', new_callable=PropertyMock, return_value=self.s3)
        connection.start()
        self.addCleanup(connection.stop)

    def create_report_store(self):
        """
        Create and return a DjangoStorageReportStore configured to use S3 for
        storage.

        The test settings' STORAGE_TYPE ('localfs') is dropped: from_config
        checks it before STORAGE_CLASS, so leaving it in builds a
        FileSystemStorage and none of these tests touch S3.
        """
        test_settings = copy.deepcopy(settings.GRADES_DOWNLOAD)
        del test_settings['STORAGE_TYPE']
        test_settings['STORAGE_CLASS'] = 'storages.backends.s3boto3.S3Boto3Storage'
        test_settings['STORAGE_KWARGS'] = {
            'bucket_name': settings.GRADES_DOWNLOAD['BUCKET'],
            # S3 keys have no leading slash; ROOT_PATH is a local temp dir here.
            'location': settings.GRADES_DOWNLOAD['ROOT_PATH'].lstrip('/'),
        }
        with override_settings(GRADES_DOWNLOAD=test_settings):
            report_store = ReportStore.from_config(config_name='GRADES_DOWNLOAD')
        assert isinstance(report_store.storage, S3Boto3Storage)
        return report_store

    def test_store_uploads_to_s3(self):
        """
        A binary report is uploaded as the object's whole body, in bounded
        chunks, under the course's hashed path.
        """
        report_store = self.create_report_store()
        payload = b'student_id,grade\n' + b'1,0.5\n' * 50000

        report_store.store(self.course_id, 'uploaded.csv', BytesIO(payload))

        key = f"{report_store.storage.location}/{report_store.path_to(self.course_id, 'uploaded.csv')}"
        assert self.s3.objects[key][0] == payload
        assert len(payload) > 4 * _FakeS3.CHUNK_SIZE


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
