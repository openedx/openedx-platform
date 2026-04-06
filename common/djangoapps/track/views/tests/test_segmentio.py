"""Ensure we can parse events sent to us from the Segment webhook integration"""


import json
from unittest.mock import sentinel

from dateutil import parser
from ddt import data, ddt, unpack
from django.contrib.auth.models import User  # lint-amnesty, pylint: disable=imported-auth-user
from django.test.utils import override_settings

from common.djangoapps.track.middleware import TrackMiddleware
from common.djangoapps.track.views import segmentio
from common.djangoapps.track.views.tests.base import (  # lint-amnesty, pylint: disable=line-too-long
    SEGMENTIO_TEST_ENDPOINT,
    SEGMENTIO_TEST_USER_ID,
    SegmentIOTrackingTestCaseBase,
)
from openedx.core.lib.tests.assertions.events import assert_event_matches


def expect_failure_with_message(message):
    """Ensure the test raises an exception and does not emit an event"""
    def test_decorator(func):
        def test_decorated(self, *args, **kwargs):
            self.assertRaisesRegex(segmentio.EventValidationError, message, func, self, *args, **kwargs)
            self.assert_no_events_emitted()
        return test_decorated
    return test_decorator


@ddt
class SegmentIOTrackingTestCase(SegmentIOTrackingTestCaseBase):
    """
    Test processing of Segment events.
    """

    def setUp(self):
        super().setUp()

        User.objects.create(pk=SEGMENTIO_TEST_USER_ID, username=str(sentinel.username))

    def test_get_request(self):
        request = self.request_factory.get(SEGMENTIO_TEST_ENDPOINT)
        response = segmentio.segmentio_event(request)
        assert response.status_code == 405
        self.assert_no_events_emitted()

    @override_settings(
        TRACKING_SEGMENTIO_WEBHOOK_SECRET=None
    )
    def test_no_secret_config(self):
        request = self.request_factory.post(SEGMENTIO_TEST_ENDPOINT)
        response = segmentio.segmentio_event(request)
        assert response.status_code == 401
        self.assert_no_events_emitted()

    def test_no_secret_provided(self):
        request = self.request_factory.post(SEGMENTIO_TEST_ENDPOINT)
        response = segmentio.segmentio_event(request)
        assert response.status_code == 401
        self.assert_no_events_emitted()

    def test_secret_mismatch(self):
        request = self.create_request(key='y')
        response = segmentio.segmentio_event(request)
        assert response.status_code == 401
        self.assert_no_events_emitted()

    @data('identify', 'Group', 'Alias', 'Page', 'identify', 'screen')
    def test_segmentio_ignore_actions(self, action):
        self.post_segmentio_event(action=action)
        self.assert_no_events_emitted()

    def test_segmentio_ignore_missing_context_entry(self):
        sample_event_raw = self.create_segmentio_event()
        del sample_event_raw['context']
        self.post_modified_segmentio_event(sample_event_raw)
        self.assert_no_events_emitted()

    def test_segmentio_ignore_null_context_entry(self):
        sample_event_raw = self.create_segmentio_event()
        sample_event_raw['context'] = None
        self.post_modified_segmentio_event(sample_event_raw)
        self.assert_no_events_emitted()

    def test_segmentio_ignore_missing_library_entry(self):
        sample_event_raw = self.create_segmentio_event()
        del sample_event_raw['context']['library']
        self.post_modified_segmentio_event(sample_event_raw)
        self.assert_no_events_emitted()

    def test_segmentio_ignore_null_library_entry(self):
        sample_event_raw = self.create_segmentio_event()
        sample_event_raw['context']['library'] = None
        self.post_modified_segmentio_event(sample_event_raw)
        self.assert_no_events_emitted()

    def test_segmentio_ignore_unknown_libraries(self):
        self.post_segmentio_event(library_name='foo')
        self.assert_no_events_emitted()

    @expect_failure_with_message(segmentio.ERROR_MISSING_NAME)
    def test_segmentio_ignore_missing_properties_entry(self):
        sample_event_raw = self.create_segmentio_event()
        del sample_event_raw['properties']
        self.post_modified_segmentio_event(sample_event_raw)

    @expect_failure_with_message(segmentio.ERROR_MISSING_NAME)
    def test_segmentio_ignore_null_properties_entry(self):
        sample_event_raw = self.create_segmentio_event()
        sample_event_raw['properties'] = None
        self.post_modified_segmentio_event(sample_event_raw)

    @expect_failure_with_message(segmentio.ERROR_USER_NOT_EXIST)
    def test_no_user_for_user_id(self):
        self.post_segmentio_event(user_id=40)

    @expect_failure_with_message(segmentio.ERROR_INVALID_USER_ID)
    def test_invalid_user_id(self):
        self.post_segmentio_event(user_id='foobar')

    @data('foo/bar/baz', 'course-v1:foo+bar+baz')
    def test_success(self, course_id):
        middleware = TrackMiddleware(get_response=lambda request: None)

        request = self.create_request(
            data=self.create_segmentio_event_json(data={'foo': 'bar'}, course_id=course_id),
            content_type='application/json'
        )

        middleware.process_request(request)
        # The middleware normally emits an event, make sure it doesn't in this case.
        self.assert_no_events_emitted()
        try:
            response = segmentio.segmentio_event(request)
            assert response.status_code == 200

            expected_event = {
                'accept_language': '',
                'referer': '',
                'username': str(sentinel.username),
                'ip': '',
                'session': '',
                'event_source': 'mobile',
                'event_type': str(sentinel.name),
                'name': str(sentinel.name),
                'event': {'foo': 'bar'},
                'agent': str(sentinel.user_agent),
                'page': None,
                'time': parser.parse("2014-08-27T16:33:39.215Z"),
                'host': 'testserver',
                'context': {
                    'application': {
                        'name': 'edx.mobile.android',
                        'version': '1.0.1',
                    },
                    'user_id': SEGMENTIO_TEST_USER_ID,
                    'course_id': course_id,
                    'org_id': 'foo',
                    'path': SEGMENTIO_TEST_ENDPOINT,
                    'client': {
                        'library': {
                            'name': 'test-app',
                            'version': 'unknown'
                        },
                        'app': {
                            'version': '1.0.1',
                        },
                    },
                    'received_at': parser.parse("2014-08-27T16:33:39.100Z"),
                },
            }
        finally:
            middleware.process_response(request, None)

        assert_event_matches(expected_event, self.get_event())

    def test_invalid_course_id(self):
        request = self.create_request(
            data=self.create_segmentio_event_json(course_id='invalid'),
            content_type='application/json'
        )
        segmentio.track_segmentio_event(request)
        self.assert_events_emitted()

    @data(
        None,
        'a string',
        ['a', 'list'],
    )
    @expect_failure_with_message(segmentio.ERROR_INVALID_CONTEXT_FIELD_TYPE)
    def test_invalid_context_field_type(self, invalid_value):
        sample_event_raw = self.create_segmentio_event()
        sample_event_raw['properties']['context'] = invalid_value
        self.post_modified_segmentio_event(sample_event_raw)

    @data(
        None,
        'a string',
        ['a', 'list'],
    )
    @expect_failure_with_message(segmentio.ERROR_INVALID_DATA_FIELD_TYPE)
    def test_invalid_data_field_type(self, invalid_value):
        sample_event_raw = self.create_segmentio_event()
        sample_event_raw['properties']['data'] = invalid_value
        self.post_modified_segmentio_event(sample_event_raw)

    @expect_failure_with_message(segmentio.ERROR_MISSING_NAME)
    def test_missing_name(self):
        sample_event_raw = self.create_segmentio_event()
        del sample_event_raw['properties']['name']
        self.post_modified_segmentio_event(sample_event_raw)

    @expect_failure_with_message(segmentio.ERROR_MISSING_DATA)
    def test_missing_data(self):
        sample_event_raw = self.create_segmentio_event()
        del sample_event_raw['properties']['data']
        self.post_modified_segmentio_event(sample_event_raw)

    @expect_failure_with_message(segmentio.ERROR_MISSING_TIMESTAMP)
    def test_missing_timestamp(self):
        sample_event_raw = self.create_event_without_fields('timestamp')
        self.post_modified_segmentio_event(sample_event_raw)

    @expect_failure_with_message(segmentio.ERROR_MISSING_RECEIVED_AT)
    def test_missing_received_at(self):
        sample_event_raw = self.create_event_without_fields('receivedAt')
        self.post_modified_segmentio_event(sample_event_raw)

    def create_event_without_fields(self, *fields):
        """Create a fake event and remove some fields from it"""
        event = self.create_segmentio_event()

        for field in fields:
            if field in event:
                del event[field]

        return event

    def test_string_user_id(self):
        self.post_segmentio_event(user_id=str(SEGMENTIO_TEST_USER_ID))
        self.assert_events_emitted()

    @data(
        '2018-12-11T07:27:28.015900357Z',
        '2014-08-27T16:33:39.100Z',
        '2014-08-27T16:33:39.215Z'
    )
    def test_timestamp_success(self, timestamp):
        sample_event_raw = self.create_segmentio_event()
        sample_event_raw['receivedAt'] = timestamp
        sample_event_raw['timestamp'] = timestamp
        request = self.create_request(
            data=json.dumps(sample_event_raw),
            content_type='application/json'
        )
        response = segmentio.segmentio_event(request)
        assert response.status_code == 200
        self.assert_events_emitted()

    def test_hiding_failure(self):
        sample_event_raw = self.create_event_without_fields('timestamp')
        request = self.create_request(
            data=json.dumps(sample_event_raw),
            content_type='application/json'
        )

        response = segmentio.segmentio_event(request)
        assert response.status_code == 200
        self.assert_no_events_emitted()

    @data(
        ('edx.video.played', 'play_video'),
        ('edx.video.paused', 'pause_video'),
        ('edx.video.stopped', 'stop_video'),
        ('edx.video.loaded', 'load_video'),
        ('edx.video.position.changed', 'seek_video'),
        ('edx.video.transcript.shown', 'show_transcript'),
        ('edx.video.transcript.hidden', 'hide_transcript'),
    )
    @unpack
    def test_video_event(self, name, event_type):
        course_id = 'foo/bar/baz'
        middleware = TrackMiddleware(get_response=lambda request: None)

        input_payload = {
            'current_time': 132.134456,
            'module_id': 'i4x://foo/bar/baz/some_module',
            'code': 'mobile'
        }
        if name == 'edx.video.loaded':
            # We use the same expected payload for all of these types of events, but the load video event is the only
            # one that is not actually expected to contain a "current time" field. So we remove it from the expected
            # event here.
            del input_payload['current_time']

        request = self.create_request(
            data=self.create_segmentio_event_json(
                name=name,
                data=input_payload,
                context={
                    'open_in_browser_url': 'https://testserver/courses/foo/bar/baz/courseware/Week_1/Activity/2',
                    'course_id': course_id,
                    'application': {
                        'name': 'edx.mobileapp.android',
                        'version': '29',
                        'component': 'videoplayer'
                    }
                }),
            content_type='application/json'
        )

        middleware.process_request(request)
        try:
            response = segmentio.segmentio_event(request)
            assert response.status_code == 200

            expected_event = {
                'accept_language': '',
                'referer': '',
                'username': str(sentinel.username),
                'ip': '',
                'session': '',
                'event_source': 'mobile',
                'event_type': event_type,
                'name': name,
                'agent': str(sentinel.user_agent),
                'page': 'https://testserver/courses/foo/bar/baz/courseware/Week_1/Activity',
                'time': parser.parse("2014-08-27T16:33:39.215Z"),
                'host': 'testserver',
                'context': {
                    'user_id': SEGMENTIO_TEST_USER_ID,
                    'course_id': course_id,
                    'org_id': 'foo',
                    'path': SEGMENTIO_TEST_ENDPOINT,
                    'client': {
                        'library': {
                            'name': 'test-app',
                            'version': 'unknown'
                        },
                        'app': {
                            'version': '1.0.1',
                        },
                    },
                    'application': {
                        'name': 'edx.mobileapp.android',
                        'version': '29',
                        'component': 'videoplayer'
                    },
                    'received_at': parser.parse("2014-08-27T16:33:39.100Z"),
                },
                'event': {
                    'currentTime': 132.134456,
                    'id': 'i4x-foo-bar-baz-some_module',
                    'code': 'mobile'
                }
            }
            if name == 'edx.video.loaded':
                # We use the same expected payload for all of these types of events, but the load video event is the
                # only one that is not actually expected to contain a "current time" field. So we remove it from the
                # expected event here.
                del expected_event['event']['currentTime']
        finally:
            middleware.process_response(request, None)

        actual_event = self.get_event()
        assert_event_matches(expected_event, actual_event)

    @data(
        # Verify positive slide case. Verify slide to onSlideSeek. Verify
        # edx.video.seeked emitted from iOS v1.0.02 is changed to
        # edx.video.position.changed.
        (1, 1, "seek_type", "slide", "onSlideSeek", "edx.video.seeked", "edx.video.position.changed", 'edx.mobileapp.iOS', '1.0.02'),  # lint-amnesty, pylint: disable=line-too-long
        # Verify negative slide case. Verify slide to onSlideSeek. Verify
        # edx.video.seeked to edx.video.position.changed.
        (-2, -2, "seek_type", "slide", "onSlideSeek", "edx.video.seeked", "edx.video.position.changed", 'edx.mobileapp.iOS', '1.0.02'),  # lint-amnesty, pylint: disable=line-too-long
        # Verify +30 is changed to -30 which is incorrectly emitted in iOS
        # v1.0.02. Verify skip to onSkipSeek
        (30, -30, "seek_type", "skip", "onSkipSeek", "edx.video.position.changed", "edx.video.position.changed", 'edx.mobileapp.iOS', '1.0.02'),  # lint-amnesty, pylint: disable=line-too-long
        # Verify the correct case of -30 is also handled as well. Verify skip
        # to onSkipSeek
        (-30, -30, "seek_type", "skip", "onSkipSeek", "edx.video.position.changed", "edx.video.position.changed", 'edx.mobileapp.iOS', '1.0.02'),  # lint-amnesty, pylint: disable=line-too-long
        # Verify positive slide case where onSkipSeek is changed to
        # onSlideSkip. Verify edx.video.seeked emitted from Android v1.0.02 is
        # changed to edx.video.position.changed.
        (1, 1, "type", "onSkipSeek", "onSlideSeek", "edx.video.seeked", "edx.video.position.changed", 'edx.mobileapp.android', '1.0.02'),  # lint-amnesty, pylint: disable=line-too-long
        # Verify positive slide case where onSkipSeek is changed to
        # onSlideSkip. Verify edx.video.seeked emitted from Android v1.0.02 is
        # changed to edx.video.position.changed.
        (-2, -2, "type", "onSkipSeek", "onSlideSeek", "edx.video.seeked", "edx.video.position.changed", 'edx.mobileapp.android', '1.0.02'),  # lint-amnesty, pylint: disable=line-too-long
        # Verify positive skip case where onSkipSeek is not changed and does
        # not become negative.
        (30, 30, "type", "onSkipSeek", "onSkipSeek", "edx.video.position.changed", "edx.video.position.changed", 'edx.mobileapp.android', '1.0.02'),  # lint-amnesty, pylint: disable=line-too-long
        # Verify positive skip case where onSkipSeek is not changed.
        (-30, -30, "type", "onSkipSeek", "onSkipSeek", "edx.video.position.changed", "edx.video.position.changed", 'edx.mobileapp.android', '1.0.02')  # lint-amnesty, pylint: disable=line-too-long
    )
    @unpack
    def test_previous_builds(self,
                             requested_skip_interval,
                             expected_skip_interval,
                             seek_type_key,
                             seek_type,
                             expected_seek_type,
                             name,
                             expected_name,
                             platform,
                             version,
                             ):
        """
        Test backwards compatibility of previous app builds

        iOS version 1.0.02: Incorrectly emits the skip back 30 seconds as +30
        instead of -30.
        Android version 1.0.02: Skip and slide were both being returned as a
        skip. Skip or slide is determined by checking if the skip time is == -30
        Additionally, for both of the above mentioned versions, edx.video.seeked
        was sent instead of edx.video.position.changed
        """
        course_id = 'foo/bar/baz'
        middleware = TrackMiddleware(get_response=lambda request: None)
        input_payload = {
            "code": "mobile",
            "new_time": 89.699177437,
            "old_time": 119.699177437,
            seek_type_key: seek_type,
            "requested_skip_interval": requested_skip_interval,
            'module_id': 'i4x://foo/bar/baz/some_module',
        }
        request = self.create_request(
            data=self.create_segmentio_event_json(
                name=name,
                data=input_payload,
                context={
                    'open_in_browser_url': 'https://testserver/courses/foo/bar/baz/courseware/Week_1/Activity/2',
                    'course_id': course_id,
                    'application': {
                        'name': platform,
                        'version': version,
                        'component': 'videoplayer'
                    }
                },
            ),
            content_type='application/json'
        )

        middleware.process_request(request)
        try:
            response = segmentio.segmentio_event(request)
            assert response.status_code == 200

            expected_event = {
                'accept_language': '',
                'referer': '',
                'username': str(sentinel.username),
                'ip': '',
                'session': '',
                'event_source': 'mobile',
                'event_type': "seek_video",
                'name': expected_name,
                'agent': str(sentinel.user_agent),
                'page': 'https://testserver/courses/foo/bar/baz/courseware/Week_1/Activity',
                'time': parser.parse("2014-08-27T16:33:39.215Z"),
                'host': 'testserver',
                'context': {
                    'user_id': SEGMENTIO_TEST_USER_ID,
                    'course_id': course_id,
                    'org_id': 'foo',
                    'path': SEGMENTIO_TEST_ENDPOINT,
                    'client': {
                        'library': {
                            'name': 'test-app',
                            'version': 'unknown'
                        },
                        'app': {
                            'version': '1.0.1',
                        },
                    },
                    'application': {
                        'name': platform,
                        'version': version,
                        'component': 'videoplayer'
                    },
                    'received_at': parser.parse("2014-08-27T16:33:39.100Z"),
                },
                'event': {
                    "code": "mobile",
                    "new_time": 89.699177437,
                    "old_time": 119.699177437,
                    "type": expected_seek_type,
                    "requested_skip_interval": expected_skip_interval,
                    'id': 'i4x-foo-bar-baz-some_module',
                }
            }
        finally:
            middleware.process_response(request, None)

        actual_event = self.get_event()
        assert_event_matches(expected_event, actual_event)
