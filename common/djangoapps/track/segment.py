"""
Wrapper methods for emitting events to Segment directly (rather than through tracking log events).

These take advantage of properties that are extracted from incoming requests by track middleware,
stored in tracking context objects, and extracted here to be passed to Segment as part of context
required by server-side events.

To use, call "from common.djangoapps.track import segment", then call segment.track() or segment.identify().

"""


import analytics
from django.conf import settings
from eventtracking import tracker
from six.moves.urllib.parse import urlunsplit


def track(user_id, event_name, properties=None, context=None, traits=None):
    """
    Wrapper for emitting Segment track event, including augmenting context information from middleware.
    """

    if event_name is not None and hasattr(settings, 'LMS_SEGMENT_KEY') and settings.LMS_SEGMENT_KEY:
        properties = properties or {}
        segment_context = dict(context) if context else {}
        tracking_context = tracker.get_tracker().resolve_context()

        if 'ip' not in segment_context and 'ip' in tracking_context:
            segment_context['ip'] = tracking_context.get('ip')

        if ('Google Analytics' not in segment_context or 'clientId' not in segment_context['Google Analytics']) and 'client_id' in tracking_context:  # pylint: disable=line-too-long
            segment_context['Google Analytics'] = {
                'clientId': tracking_context.get('client_id')
            }

        if 'userAgent' not in segment_context and 'agent' in tracking_context:
            segment_context['userAgent'] = tracking_context.get('agent')

        path = tracking_context.get('path')
        referer = tracking_context.get('referer')
        page = tracking_context.get('page')

        if path and not page:
            # Try to put together a url from host and path, hardcoding the schema.
            # (Segment doesn't care about the schema for GA, but will extract the host and path from the url.)
            host = tracking_context.get('host')
            if host:
                parts = ("https", host, path, "", "")
                page = urlunsplit(parts)

        if path is not None or referer is not None or page is not None:
            if 'page' not in segment_context:
                segment_context['page'] = {}
            if path is not None and 'path' not in segment_context['page']:
                segment_context['page']['path'] = path
            if referer is not None and 'referrer' not in segment_context['page']:
                segment_context['page']['referrer'] = referer
            if page is not None and 'url' not in segment_context['page']:
                segment_context['page']['url'] = page

        if traits:
            segment_context['traits'] = traits

        analytics.track(user_id, event_name, properties, segment_context)


def identify(user_id, properties, context=None):
    """
    Wrapper for emitting Segment identify event.
    """
    if hasattr(settings, 'LMS_SEGMENT_KEY') and settings.LMS_SEGMENT_KEY:
        segment_context = dict(context) if context else {}
        analytics.identify(user_id, properties, segment_context)
