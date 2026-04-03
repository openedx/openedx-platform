# lint-amnesty, pylint: disable=missing-module-docstring

import json

import six
from django.contrib.auth.models import User  # lint-amnesty, pylint: disable=imported-auth-user
from django.http import HttpResponse
from eventtracking import tracker as eventtracker
from ipware.ip import get_client_ip

from common.djangoapps.track import contexts, shim, tracker
from lms.djangoapps.utils import OptimizelyClient


def _get_request_header(request, header_name, default=''):
    """Helper method to get header values from a request's META dict, if present."""
    if request is not None and hasattr(request, 'META') and header_name in request.META:
        return request.META[header_name]
    else:
        return default


def _get_request_ip(request, default=''):
    """Helper method to get IP from a request's META dict, if present."""
    if request is not None and hasattr(request, 'META'):
        return get_client_ip(request)[0]
    else:
        return default


def _get_request_value(request, value_name, default=''):
    """Helper method to get header values from a request's GET/POST dict, if present."""
    if request is not None:
        if request.method == 'GET':
            return request.GET.get(value_name, default)
        elif request.method == 'POST':
            return request.POST.get(value_name, default)
    return default


def _get_course_context(page):
    """Return the course context from the provided page.

    If the context has no/empty course_id, return empty context
    """
    course_context = contexts.course_context_from_url(page)
    if course_context.get('course_id', '') == '':
        course_context = {}

    return course_context


def _add_user_id_for_username(data):
    """
    If data contains a username, adds the corresponding user_id to the data.

    In certain use cases, the caller may have the username and not the
    user_id. This enables us to standardize on user_id in event data,
    even when the caller only has access to the username.
    """
    if data and ('username' in data) and ('user_id' not in data):
        try:
            user = User.objects.get(username=data.get('username'))
            data['user_id'] = user.id
        except User.DoesNotExist:
            pass


def user_track(request):
    """
    Log when POST call to "event" URL is made by a user.

    GET or POST call should provide "event_type", "event", and "page" arguments. It may optionally provide
    a "courserun_key" argument (otherwise may be extracted from the page).
    """
    try:
        username = request.user.username
    except:  # lint-amnesty, pylint: disable=bare-except
        username = "anonymous"

    name = _get_request_value(request, 'event_type')
    data = _get_request_value(request, 'event', {})
    course_id_string = _get_request_value(request, 'courserun_key', None)
    page = _get_request_value(request, 'page')

    if isinstance(data, str) and len(data) > 0:
        try:
            data = json.loads(data)
            _add_user_id_for_username(data)
        except ValueError:
            pass

    context_override = contexts.course_context_from_url(page, course_id_string)
    context_override['username'] = username
    context_override['event_source'] = 'browser'
    context_override['page'] = page

    with eventtracker.get_tracker().context('edx.course.browser', context_override):
        eventtracker.emit(name=name, data=data)

    # TODO: VAN-1052: This event is added to track the KPIs for A/B experiment.
    #  Remove it after the experiment has been paused.
    if (
        name == 'edx.course.home.resume_course.clicked' and
        data.get('event_type') == 'start' and
        request.user
    ):
        optimizely_client = OptimizelyClient.get_optimizely_client()
        if optimizely_client:
            optimizely_client.track('course_started', str(request.user.id))

    return HttpResponse('success')


def server_track(request, event_type, event, page=None):
    """
    Log events related to server requests.

    Handle the situation where the request may be NULL, as may happen with management commands.
    """
    if event_type.startswith("/event_logs") and request.user.is_staff:
        return  # don't log

    try:
        username = request.user.username
    except:  # lint-amnesty, pylint: disable=bare-except
        username = "anonymous"

    context_override = _get_course_context(page)
    context_override.update({
        'username': username,
        'event_source': 'server',
        'page': page
    })

    event_tracker = eventtracker.get_tracker()
    with event_tracker.context('edx.course.server', context_override):
        eventtracker.emit(name=event_type, data=event)


def task_track(request_info, task_info, event_type, event, page=None):
    """
    Logs tracking information for events occuring within celery tasks.

    The `event_type` is a string naming the particular event being logged,
    while `event` is a dict containing whatever additional contextual information
    is desired.

    The `request_info` is a dict containing information about the original
    task request.  Relevant keys are `username`, `ip`, `agent`, and `host`.
    While the dict is required, the values in it are not, so that {} can be
    passed in.

    In addition, a `task_info` dict provides more information about the current
    task, to be stored with the `event` dict.  This may also be an empty dict.

    The `page` parameter is optional, and allows the name of the page to
    be provided.
    """

    # supplement event information with additional information
    # about the task in which it is running.
    data = dict(event, **task_info)

    context_override = contexts.course_context_from_url(page)
    context_override.update({
        'username': request_info.get('username', 'unknown'),
        'ip': request_info.get('ip', 'unknown'),
        'agent': request_info.get('agent', 'unknown'),
        'host': request_info.get('host', 'unknown'),
        'event_source': 'task',
        'page': page,
    })

    with eventtracker.get_tracker().context('edx.course.task', context_override):
        eventtracker.emit(name=event_type, data=data)
