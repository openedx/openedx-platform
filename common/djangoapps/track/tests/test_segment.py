"""Ensure emitted events contain the fields legacy processors expect to find."""


from unittest.mock import patch, sentinel

import ddt
from django.test import TestCase
from django.test.utils import override_settings
from eventtracking import tracker
from eventtracking.django import DjangoTracker

from common.djangoapps.track import segment


@ddt.ddt
class SegmentTrackTestCase(TestCase):
    """Ensure emitted events contain the expected context values."""

    def setUp(self):
        super().setUp()
        self.tracker = DjangoTracker()
        tracker.register_tracker(self.tracker)
        self.properties = {sentinel.key: sentinel.value}

        patcher = patch('common.djangoapps.track.segment.analytics.track')
        self.mock_segment_track = patcher.start()
        self.addCleanup(patcher.stop)

    def test_missing_key(self):
        segment.track(sentinel.user_id, sentinel.name, self.properties)
        assert not self.mock_segment_track.called

    @override_settings(LMS_SEGMENT_KEY=None)
    def test_null_key(self):
        segment.track(sentinel.user_id, sentinel.name, self.properties)
        assert not self.mock_segment_track.called

    @override_settings(LMS_SEGMENT_KEY="testkey")
    def test_missing_name(self):
        segment.track(sentinel.user_id, None, self.properties)
        assert not self.mock_segment_track.called

    @override_settings(LMS_SEGMENT_KEY="testkey")
    def test_track_without_tracking_context(self):
        segment.track(sentinel.user_id, sentinel.name, self.properties)
        assert self.mock_segment_track.called
        args, kwargs = self.mock_segment_track.call_args  # pylint: disable=unused-variable
        expected_segment_context = {}
        assert (sentinel.user_id, sentinel.name, self.properties, expected_segment_context) == args

    @ddt.unpack
    @ddt.data(
        ({'ip': sentinel.ip}, {'ip': sentinel.provided_ip}, {'ip': sentinel.ip}),
        ({'agent': sentinel.agent}, {'userAgent': sentinel.provided_agent}, {'userAgent': sentinel.agent}),
        ({'path': sentinel.path}, {'page': {'path': sentinel.provided_path}}, {'page': {'path': sentinel.path}}),
        ({'referer': sentinel.referer}, {'page': {'referrer': sentinel.provided_referer}}, {'page': {'referrer': sentinel.referer}}),  # pylint: disable=line-too-long
        ({'page': sentinel.page}, {'page': {'url': sentinel.provided_page}}, {'page': {'url': sentinel.page}}),
        ({'client_id': sentinel.client_id}, {'Google Analytics': {'clientId': sentinel.provided_client_id}}, {'Google Analytics': {'clientId': sentinel.client_id}}),  # pylint: disable=line-too-long
    )
    @override_settings(LMS_SEGMENT_KEY="testkey")
    def test_track_context_with_stuff(self, tracking_context, provided_context, expected_segment_context):
        # Test first with tracking and no provided context.
        with self.tracker.context('test', tracking_context):
            segment.track(sentinel.user_id, sentinel.name, self.properties)
        args, kwargs = self.mock_segment_track.call_args  # pylint: disable=unused-variable
        assert (sentinel.user_id, sentinel.name, self.properties, expected_segment_context) == args

        # Test with provided context and no tracking context.
        segment.track(sentinel.user_id, sentinel.name, self.properties, provided_context)
        args, kwargs = self.mock_segment_track.call_args
        assert (sentinel.user_id, sentinel.name, self.properties, provided_context) == args

        # Test with provided context and also tracking context.
        with self.tracker.context('test', tracking_context):
            segment.track(sentinel.user_id, sentinel.name, self.properties, provided_context)
        assert self.mock_segment_track.called
        args, kwargs = self.mock_segment_track.call_args
        assert (sentinel.user_id, sentinel.name, self.properties, provided_context) == args

    @override_settings(LMS_SEGMENT_KEY="testkey")
    def test_track_with_standard_context(self):

        # Note that 'host' and 'path' will be urlparsed, so must be strings.
        tracking_context = {
            'accept_language': sentinel.accept_language,
            'referer': sentinel.referer,
            'username': sentinel.username,
            'session': sentinel.session,
            'ip': sentinel.ip,
            'host': 'hostname',
            'agent': sentinel.agent,
            'path': '/this/is/a/path',
            'user_id': sentinel.user_id,
            'course_id': sentinel.course_id,
            'org_id': sentinel.org_id,
            'client_id': sentinel.client_id,
        }
        with self.tracker.context('test', tracking_context):
            segment.track(sentinel.user_id, sentinel.name, self.properties)

        assert self.mock_segment_track.called
        args, kwargs = self.mock_segment_track.call_args  # pylint: disable=unused-variable

        expected_segment_context = {
            'ip': sentinel.ip,
            'Google Analytics': {
                'clientId': sentinel.client_id,
            },
            'userAgent': sentinel.agent,
            'page': {
                'path': '/this/is/a/path',
                'referrer': sentinel.referer,
                'url': 'https://hostname/this/is/a/path'  # Synthesized URL value.
            }
        }
        assert (sentinel.user_id, sentinel.name, self.properties, expected_segment_context) == args


class SegmentIdentifyTestCase(TestCase):
    """Ensure emitted events contain the fields legacy processors expect to find."""

    def setUp(self):
        super().setUp()
        patcher = patch('common.djangoapps.track.segment.analytics.identify')
        self.mock_segment_identify = patcher.start()
        self.addCleanup(patcher.stop)
        self.properties = {sentinel.key: sentinel.value}

    def test_missing_key(self):
        segment.identify(sentinel.user_id, self.properties)
        assert not self.mock_segment_identify.called

    @override_settings(LMS_SEGMENT_KEY=None)
    def test_null_key(self):
        segment.identify(sentinel.user_id, self.properties)
        assert not self.mock_segment_identify.called

    @override_settings(LMS_SEGMENT_KEY="testkey")
    def test_normal_call(self):
        segment.identify(sentinel.user_id, self.properties)
        assert self.mock_segment_identify.called
        args, kwargs = self.mock_segment_identify.call_args  # pylint: disable=unused-variable
        assert (sentinel.user_id, self.properties, {}) == args

    @override_settings(LMS_SEGMENT_KEY="testkey")
    def test_call_with_context(self):
        provided_context = {sentinel.context_key: sentinel.context_value}
        segment.identify(sentinel.user_id, self.properties, provided_context)
        assert self.mock_segment_identify.called
        args, kwargs = self.mock_segment_identify.call_args  # pylint: disable=unused-variable
        assert (sentinel.user_id, self.properties, provided_context) == args
