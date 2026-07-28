"""
Base class for tests related to emitted events to one of the tracking 'views'
(e.g. SegmentIO).
"""


import json
from unittest.mock import sentinel

from django.test.client import RequestFactory
from django.test.utils import override_settings

from common.djangoapps.track.tests import EventTrackingTestCase
from common.djangoapps.track.views import segmentio

SEGMENTIO_TEST_SECRET = 'anything'
SEGMENTIO_TEST_ENDPOINT = '/segmentio/test/event'
SEGMENTIO_TEST_USER_ID = 10

_MOBILE_SHIM_PROCESSOR = [
    {'ENGINE': 'common.djangoapps.track.shim.LegacyFieldMappingProcessor'},
    {'ENGINE': 'common.djangoapps.track.shim.PrefixedEventProcessor'},
]


@override_settings(
    TRACKING_SEGMENTIO_WEBHOOK_SECRET=SEGMENTIO_TEST_SECRET,
    TRACKING_IGNORE_URL_PATTERNS=[SEGMENTIO_TEST_ENDPOINT],
    TRACKING_SEGMENTIO_ALLOWED_TYPES=['track'],
    TRACKING_SEGMENTIO_DISALLOWED_SUBSTRING_NAMES=[],
    TRACKING_SEGMENTIO_SOURCE_MAP={'test-app': 'mobile'},
    EVENT_TRACKING_PROCESSORS=_MOBILE_SHIM_PROCESSOR,
)
class SegmentIOTrackingTestCaseBase(EventTrackingTestCase):
    """
    Base class for tests that test the processing of Segment events.
    """

    def setUp(self):
        super().setUp()
        self.maxDiff = None  # pylint: disable=invalid-name
        self.request_factory = RequestFactory()

    def create_request(self, key=None, **kwargs):
        """Create a fake request that emulates a request from the Segment servers to ours"""
        if key is None:
            key = SEGMENTIO_TEST_SECRET

        request = self.request_factory.post(SEGMENTIO_TEST_ENDPOINT + "?key=" + key, **kwargs)
        if 'data' in kwargs:
            request.json = json.loads(kwargs['data'])

        return request

    def post_segmentio_event(self, **kwargs):
        """Post a fake Segment event to the view that processes it"""
        request = self.create_request(
            data=self.create_segmentio_event_json(**kwargs),
            content_type='application/json'
        )
        segmentio.track_segmentio_event(request)

    def post_modified_segmentio_event(self, event):
        """Post an externally-defined fake Segment event to the view that processes it"""
        request = self.create_request(
            data=json.dumps(event),
            content_type='application/json'
        )
        segmentio.track_segmentio_event(request)

    def create_segmentio_event(self, **kwargs):
        """Populate a fake Segment event with data of interest"""
        action = kwargs.get('action', 'Track')
        sample_event = {
            "userId": kwargs.get('user_id', SEGMENTIO_TEST_USER_ID),
            "event": "Did something",
            "properties": {
                'name': kwargs.get('name', str(sentinel.name)),
                'data': kwargs.get('data', {}),
                'context': {
                    'course_id': kwargs.get('course_id') or '',
                    'app_name': 'edx.mobile.android',
                }
            },
            "channel": 'server',
            "context": {
                "library": {
                    "name": kwargs.get('library_name', 'test-app'),
                    "version": "unknown"
                },
                "app": {
                    "version": "1.0.1",
                },
                'userAgent': str(sentinel.user_agent),
            },
            "receivedAt": "2014-08-27T16:33:39.100Z",
            "timestamp": "2014-08-27T16:33:39.215Z",
            "type": action.lower(),
            "projectId": "u0j33yjkr8",
            "messageId": "qy52hwp4",
            "version": 2,
            "integrations": {},
            "options": {
                "library": "unknown",
                "providers": {}
            },
            "action": action
        }

        if 'context' in kwargs:
            sample_event['properties']['context'].update(kwargs['context'])
        if 'label' in kwargs:
            sample_event['properties']['label'] = kwargs['label']
        if kwargs.get('exclude_name') is True:
            del sample_event['properties']['name']

        return sample_event

    def create_segmentio_event_json(self, **kwargs):
        """Return a json string containing a fake Segment event"""
        return json.dumps(self.create_segmentio_event(**kwargs))
