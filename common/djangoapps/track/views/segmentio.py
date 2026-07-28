"""Handle events that were forwarded from the Segment webhook integration"""


import json
import logging

from dateutil import parser
from django.conf import settings
from django.contrib.auth.models import User  # pylint: disable=imported-auth-user
from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from edx_django_utils.monitoring import set_custom_attribute
from eventtracking import tracker
from opaque_keys import InvalidKeyError
from opaque_keys.edx.keys import CourseKey

from common.djangoapps.util.json_request import expect_json

log = logging.getLogger(__name__)


ERROR_UNAUTHORIZED = 'Unauthorized'
ERROR_MISSING_USER_ID = 'Required user_id missing from context'
ERROR_USER_NOT_EXIST = 'Specified user does not exist'
ERROR_INVALID_USER_ID = 'Unable to parse userId as an integer'
ERROR_INVALID_CONTEXT_FIELD_TYPE = 'The properties.context field is not a dict.'
ERROR_INVALID_DATA_FIELD_TYPE = 'The properties.data field is not a dict.'
ERROR_MISSING_DATA = 'The data field must be specified in the properties dictionary'
ERROR_MISSING_NAME = 'The name field must be specified in the properties dictionary'
ERROR_MISSING_TIMESTAMP = 'Required timestamp field not found'
ERROR_MISSING_RECEIVED_AT = 'Required receivedAt field not found'


FORUM_THREAD_VIEWED_EVENT_LABEL = 'Forum: View Thread'
BI_SCREEN_VIEWED_EVENT_NAME = 'edx.bi.app.navigation.screen'


@require_POST
@expect_json
@csrf_exempt
def segmentio_event(request):
    """
    An endpoint for logging events using Segment's webhook integration.

    Segment provides a custom integration mechanism that initiates a request to a configurable URL every time an
    event is received by their system. This endpoint is designed to receive those requests and convert the events into
    standard tracking log entries.

    For now we limit the scope of handled events to track and screen events from mobile devices. In the future we could
    enable logging of other types of events, however, there is significant overlap with our non-Segment based event
    tracking. Given that Segment is closed third party solution we are limiting its required usage to just
    collecting events from mobile devices for the time being.

    Many of the root fields of a standard edX tracking event are read out of the "properties" dictionary provided by the
    Segment event, which is, in turn, provided by the client that emitted the event.

    In order for an event to be accepted and logged the "key" query string parameter must exactly match the django
    setting TRACKING_SEGMENTIO_WEBHOOK_SECRET. While the endpoint is public, we want to limit access to it to the
    Segment servers only.

    """

    # Validate the security token. We must use a query string parameter for this since we cannot customize the POST body
    # in the Segment webhook configuration, we can only change the URL that they call, so we force this token to be
    # included in the URL and reject any requests that do not include it. This also assumes HTTPS is used to make the
    # connection between their server and ours.
    expected_secret = getattr(settings, 'TRACKING_SEGMENTIO_WEBHOOK_SECRET', None)
    provided_secret = request.GET.get('key')
    if not expected_secret or provided_secret != expected_secret:
        return HttpResponse(status=401)

    try:
        track_segmentio_event(request)
    except EventValidationError as err:
        log.debug(
            'Unable to process event received from Segment: message="%s" event="%s"',
            str(err),
            request.body
        )
        # Do not let the requestor know why the event wasn't saved. If the secret key is compromised this diagnostic
        # information could be used to scrape useful information from the system.

    return HttpResponse(status=200)


class EventValidationError(Exception):
    """Raised when an invalid event is received."""
    pass  # pylint: disable=unnecessary-pass


def track_segmentio_event(request):  # pylint: disable=too-many-statements
    """
    Record an event received from Segment to the tracking logs.

    This method assumes that the event has come from a trusted source.

    The received event must meet the following conditions in order to be logged:

    * The value of the "type" field of the event must be included in the list specified by the django setting
      TRACKING_SEGMENTIO_ALLOWED_TYPES. In order to make use of *all* of the features Segment offers we would have
      to implement some sort of persistent storage of information contained in some actions (like identify). For now,
      we defer support of those actions and just support a limited set that can be handled without storing information
      in external state.
    * The value of the standard "userId" field of the event must be an integer that can be used to look up the user
      using the primary key of the User model.
    * Include a "name" field in the properties dictionary that indicates the edX event name. Note this can differ
      from the "event" field found in the root of a Segment event. The "event" field at the root of the structure is
      intended to be human readable, the "name" field is expected to conform to the standard for naming events
      found in the edX data documentation.
    * Have originated from a known and trusted Segment client library. The django setting
      TRACKING_SEGMENTIO_SOURCE_MAP maps the known library names to internal "event_source" strings. In order to be
      logged the event must have a library name that is a valid key in that map.

    Additionally the event can optionally:

    * Provide a "context" dictionary in the properties dictionary. This dictionary will be applied to the
      existing context on the server overriding any existing keys. This context dictionary should include a "course_id"
      field when the event is scoped to a particular course. The value of this field should be a valid course key. The
      context may contain other arbitrary data that will be logged with the event, for example: identification
      information for the device that emitted the event.

    """

    # The POST body will contain the JSON encoded event
    full_segment_event = request.json

    # We mostly care about the properties
    segment_properties = _get_dict_value_with_default(full_segment_event, 'properties', {})

    # Start with the context provided by Segment in the "client" field if it exists
    # We should tightly control which fields actually get included in the event emitted.
    segment_context = _get_dict_value_with_default(full_segment_event, 'context', {})

    # Build up the event context by parsing fields out of the event received from Segment
    context = {}

    library_name = _get_dict_value_with_default(segment_context, 'library', {}).get('name')
    source_map = getattr(settings, 'TRACKING_SEGMENTIO_SOURCE_MAP', {})
    event_source = source_map.get(library_name)
    if not event_source:
        return
    else:
        context['event_source'] = event_source

    # Ignore event types that are unsupported
    segment_event_type = full_segment_event.get('type')
    allowed_types = [a.lower() for a in getattr(settings, 'TRACKING_SEGMENTIO_ALLOWED_TYPES', [])]
    if not segment_event_type or (segment_event_type.lower() not in allowed_types):
        return

    # Ignore event names that are unsupported
    segment_event_name = _get_segmentio_event_name(segment_properties)
    disallowed_substring_names = [
        a.lower() for a in getattr(settings, 'TRACKING_SEGMENTIO_DISALLOWED_SUBSTRING_NAMES', [])
    ]

    if any(disallowed_subs_name in segment_event_name.lower() for disallowed_subs_name in disallowed_substring_names):
        return

    set_custom_attribute('segment_event_name', segment_event_name)
    set_custom_attribute('segment_event_source', event_source)

    # Attempt to extract and validate the data field.
    if 'data' not in segment_properties:
        raise EventValidationError(ERROR_MISSING_DATA)
    segment_event_data = segment_properties.get('data', {})
    if type(segment_event_data) is not dict:  # pylint: disable=unidiomatic-typecheck
        set_custom_attribute('segment_unexpected_data', str(segment_event_data))
        raise EventValidationError(ERROR_INVALID_DATA_FIELD_TYPE)

    # create and populate application field if it doesn't exist
    app_context = segment_properties.get('context', {})
    if type(app_context) is not dict:  # pylint: disable=unidiomatic-typecheck
        set_custom_attribute('segment_unexpected_context', str(app_context))
        raise EventValidationError(ERROR_INVALID_CONTEXT_FIELD_TYPE)
    if 'application' not in app_context:
        context['application'] = {
            'name': app_context.get('app_name', ''),
            'version': segment_context.get('app', {}).get('version', ''),
        }
    app_context.pop('app_name', None)

    if segment_context:
        # copy the entire segment's context dict as a sub-field of our custom context dict
        context['client'] = dict(segment_context)
        context['agent'] = segment_context.get('userAgent', '')

        # remove duplicate and unnecessary fields from our copy
        for field in ('traits', 'integrations', 'userAgent'):
            if field in context['client']:
                del context['client'][field]

    # Overlay any context provided in the properties
    context.update(app_context)

    user_id = full_segment_event.get('userId')
    if not user_id:
        raise EventValidationError(ERROR_MISSING_USER_ID)

    # userId is assumed to be the primary key of the django User model
    try:
        user = User.objects.get(pk=user_id)
    except User.DoesNotExist:
        raise EventValidationError(ERROR_USER_NOT_EXIST)  # pylint: disable=raise-missing-from  # noqa: B904
    except ValueError:
        raise EventValidationError(ERROR_INVALID_USER_ID)  # pylint: disable=raise-missing-from  # noqa: B904
    context['user_id'] = user.id
    context['username'] = user.username

    # course_id is expected to be provided in the context when applicable
    course_id = context.get('course_id')
    if course_id:
        try:
            course_key = CourseKey.from_string(course_id)
            context['org_id'] = course_key.org
        except InvalidKeyError:
            log.warning(
                'unable to parse course_id "{course_id}" from event: {event}'.format(  # noqa: UP032
                    course_id=course_id,
                    event=json.dumps(full_segment_event),
                ),
                exc_info=True
            )

    if 'timestamp' in full_segment_event:
        context['timestamp'] = parse_iso8601_timestamp(full_segment_event['timestamp'])
    else:
        raise EventValidationError(ERROR_MISSING_TIMESTAMP)

    if 'receivedAt' in full_segment_event:
        context['received_at'] = parse_iso8601_timestamp(full_segment_event['receivedAt'])
    else:
        raise EventValidationError(ERROR_MISSING_RECEIVED_AT)

    context['ip'] = segment_properties.get('context', {}).get('ip', '')

    # For Business Intelligence events: Add label to context
    if 'label' in segment_properties:
        context['label'] = segment_properties['label']

    # For Android-sourced Business Intelligence events: add course ID to context
    if 'course_id' in segment_properties:
        context['course_id'] = segment_properties['course_id']

    with tracker.get_tracker().context('edx.segmentio', context):
        tracker.emit(segment_event_name, segment_event_data)


def _get_segmentio_event_name(event_properties):
    """
    Get the name of a SegmentIO event.

    Args:
        event_properties: dict
            The properties of the event, which should contain the event's
            name or, in the case of an old Android screen event, its screen
            label.

    Returns: str
        The name (or effective name) of the event.

    Note:
        In older versions of the Android app, screen-view tracking events
        did not have a name. So, in order to capture forum-thread-viewed events
        from those old-versioned apps, we have to accept the event based on
        its screen label. We return an event name that matches screen-view
        events in the iOS app and newer versions of the Android app.

    Raises:
        EventValidationError if name is missing
    """
    if 'name' in event_properties:
        return event_properties['name']
    elif event_properties.get('label') == FORUM_THREAD_VIEWED_EVENT_LABEL:
        return BI_SCREEN_VIEWED_EVENT_NAME
    else:
        raise EventValidationError(ERROR_MISSING_NAME)


def parse_iso8601_timestamp(timestamp):
    """Parse a particular type of ISO8601 formatted timestamp"""
    return parser.parse(timestamp)


def _get_dict_value_with_default(dict_object, key, default):
    """
    Returns default if the dict doesn't have the key or if the value is Falsey.
    Otherwise, returns the dict's value for the key.
    """
    value = dict_object.get(key, None)
    return value if value else default
