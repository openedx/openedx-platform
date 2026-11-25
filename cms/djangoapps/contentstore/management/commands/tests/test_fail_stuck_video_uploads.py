"""
Tests for the `fail_stuck_video_uploads` management command.
"""
from datetime import datetime, timedelta

import pytz
from django.core.management import call_command
from django.test import TestCase
from edxval.api import create_video
from edxval.models import Video


class FailStuckVideoUploadsTest(TestCase):
    """
    Tests for the `fail_stuck_video_uploads` management command.
    """

    def create_video(self, edx_video_id, status, age):
        """Create a video in `status` whose upload started `age` ago."""
        create_video({
            'edx_video_id': edx_video_id,
            'client_video_id': f'{edx_video_id}.mp4',
            'duration': 42.0,
            'status': status,
            'courses': ['course-v1:edX+DemoX+Demo_Course'],
            'encoded_videos': [],
        })
        Video.objects.filter(edx_video_id=edx_video_id).update(created=datetime.now(pytz.utc) - age)

    def statuses(self):
        return dict(Video.objects.values_list('edx_video_id', 'status'))

    def setUp(self):
        super().setUp()
        self.create_video('stuck', 'upload', timedelta(hours=25))
        self.create_video('uploading', 'upload', timedelta(hours=23))
        self.create_video('old-and-ready', 'file_complete', timedelta(hours=48))

    def test_marks_only_uploads_older_than_the_upload_window_as_failed(self):
        call_command('fail_stuck_video_uploads')

        assert self.statuses() == {
            'stuck': 'upload_failed',
            'uploading': 'upload',
            'old-and-ready': 'file_complete',
        }

    def test_dry_run_changes_nothing(self):
        before = self.statuses()

        call_command('fail_stuck_video_uploads', '--dry-run')

        assert self.statuses() == before
