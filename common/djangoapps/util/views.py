# lint-amnesty, pylint: disable=missing-module-docstring
import json
import logging
import sys
from functools import wraps

import calc
import crum
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponse, HttpResponseForbidden, HttpResponseServerError
from django.shortcuts import redirect
from django.views.decorators.csrf import ensure_csrf_cookie, requires_csrf_token
from django.views.defaults import server_error
from opaque_keys import InvalidKeyError
from opaque_keys.edx.keys import CourseKey, UsageKey

from common.djangoapps.edxmako.shortcuts import render_to_response
from common.djangoapps.student.roles import GlobalStaff
from common.djangoapps.track import views as track_views
from lms.djangoapps.courseware.access import has_access
from lms.djangoapps.courseware.masquerade import setup_masquerade
from openedx.core.djangoapps.schedules.utils import reset_self_paced_schedule
from openedx.features.course_experience.utils import dates_banner_should_display

log = logging.getLogger(__name__)


def ensure_valid_course_key(view_func):
    """
    This decorator should only be used with views which have argument course_key_string (studio) or course_id (lms).
    If course_key_string (studio) or course_id (lms) is not valid raise 404.
    """
    @wraps(view_func)
    def inner(request, *args, **kwargs):
        course_key = kwargs.get('course_key_string') or kwargs.get('course_id')
        if course_key is not None:
            try:
                CourseKey.from_string(course_key)
            except InvalidKeyError:
                raise Http404  # lint-amnesty, pylint: disable=raise-missing-from

        response = view_func(request, *args, **kwargs)
        return response

    return inner


def ensure_valid_usage_key(view_func):
    """
    This decorator should only be used with views which have argument usage_key_string.
    If usage_key_string is not valid raise 404.
    """
    @wraps(view_func)
    def inner(request, *args, **kwargs):
        usage_key = kwargs.get('usage_key_string')
        if usage_key is not None:
            try:
                UsageKey.from_string(usage_key)
            except InvalidKeyError:
                raise Http404  # lint-amnesty, pylint: disable=raise-missing-from

        response = view_func(request, *args, **kwargs)
        return response

    return inner


def require_global_staff(func):
    """View decorator that requires that the user have global staff permissions. """
    @wraps(func)
    def wrapped(request, *args, **kwargs):
        if GlobalStaff().has_user(request.user):
            return func(request, *args, **kwargs)
        else:
            return HttpResponseForbidden(
                "Must be {platform_name} staff to perform this action.".format(
                    platform_name=settings.PLATFORM_NAME
                )
            )
    return login_required(wrapped)


def fix_crum_request(func):
    """
    A decorator that ensures that the 'crum' package (a middleware that stores and fetches the current request in
    thread-local storage) can correctly fetch the current request. Under certain conditions, the current request cannot
    be fetched by crum (e.g.: when HTTP errors are raised in our views via 'raise Http404', et. al.). This decorator
    manually sets the current request for crum if it cannot be fetched.
    """
    @wraps(func)
    def wrapper(request, *args, **kwargs):
        if not crum.get_current_request():
            crum.set_current_request(request=request)
        return func(request, *args, **kwargs)
    return wrapper


@requires_csrf_token
def jsonable_server_error(request, template_name='500.html'):
    """
    500 error handler that serves JSON on an AJAX request, and proxies
    to the Django default `server_error` view otherwise.
    """
    if request.headers.get('x-requested-with') == 'XMLHttpRequest':
        msg = {"error": "The edX servers encountered an error"}
        return HttpResponseServerError(json.dumps(msg))
    else:
        return server_error(request, template_name=template_name)


def handle_500(template_path, context=None, test_func=None):
    """
    Decorator for view specific 500 error handling.
    Custom handling will be skipped only if test_func is passed and it returns False

    Usage:

        @handle_500(
            template_path='certificates/server-error.html',
            context={'error-info': 'Internal Server Error'},
            test_func=lambda request: request.GET.get('preview', None)
        )
        def my_view(request):
            # Any unhandled exception in this view would be handled by the handle_500 decorator
            # ...

    """
    def decorator(func):
        """
        Decorator to render custom html template in case of uncaught exception in wrapped function
        """
        @wraps(func)
        def inner(request, *args, **kwargs):
            """
            Execute the function in try..except block and return custom server-error page in case of unhandled exception
            """
            try:
                return func(request, *args, **kwargs)
            except Exception:  # pylint: disable=broad-except
                if settings.DEBUG:  # lint-amnesty, pylint: disable=no-else-raise
                    # In debug mode let django process the 500 errors and display debug info for the developer
                    raise
                elif test_func is None or test_func(request):
                    # Display custom 500 page if either
                    #   1. test_func is None (meaning nothing to test)
                    #   2. or test_func(request) returns True
                    log.exception("Error in django view.")
                    return render_to_response(template_path, context)
                else:
                    # Do not show custom 500 error when test fails
                    raise
        return inner
    return decorator


def calculate(request):
    ''' Calculator in footer of every page. '''
    equation = request.GET['equation']
    try:
        result = calc.evaluator({}, {}, equation)
    except:  # lint-amnesty, pylint: disable=bare-except
        event = {'error': list(map(str, sys.exc_info())),
                 'equation': equation}
        track_views.server_track(request, 'error:calc', event, page='calc')
        return HttpResponse(json.dumps({'result': 'Invalid syntax'}))  # lint-amnesty, pylint: disable=http-response-with-json-dumps
    return HttpResponse(json.dumps({'result': str(result)}))  # lint-amnesty, pylint: disable=http-response-with-json-dumps


def add_p3p_header(view_func):
    """
    This decorator should only be used with views which may be displayed through the iframe.
    It adds additional headers to response and therefore gives IE browsers an ability to save cookies inside the iframe
    Details:
    http://blogs.msdn.com/b/ieinternals/archive/2013/09/17/simple-introduction-to-p3p-cookie-blocking-frame.aspx
    http://stackoverflow.com/questions/8048306/what-is-the-most-broad-p3p-header-that-will-work-with-ie
    """
    @wraps(view_func)
    def inner(request, *args, **kwargs):
        """
        Helper function
        """
        response = view_func(request, *args, **kwargs)
        response['P3P'] = settings.P3P_HEADER
        return response
    return inner


@ensure_csrf_cookie
def reset_course_deadlines(request):
    """
    Set the start_date of a schedule to today, which in turn will adjust due dates for
    sequentials belonging to a self paced course

    IMPORTANT NOTE: If updates are happening to the logic here, ALSO UPDATE the `reset_course_deadlines`
    function in openedx/features/course_experience/api/v1/views.py as well.
    """
    course_key = CourseKey.from_string(request.POST.get('course_id'))
    _course_masquerade, user = setup_masquerade(
        request,
        course_key,
        has_access(request.user, 'staff', course_key)
    )

    # We ignore the missed_deadlines because this endpoint could be used for
    # learners who have remaining attempts on a problem and reset their due dates in order to
    # submit additional attempts. This can apply for 'completed' (submitted) content that would
    # not be marked as past_due
    _missed_deadlines, missed_gated_content = dates_banner_should_display(course_key, user)
    if not missed_gated_content:
        reset_self_paced_schedule(user, course_key)

    referrer = request.META.get('HTTP_REFERER')
    return redirect(referrer) if referrer else HttpResponse()


def expose_header(header, response):
    """
    Add a header name to Access-Control-Expose-Headers to allow client code to access that header's value
    """
    exposedHeaders = response.get('Access-Control-Expose-Headers', '')
    exposedHeaders += f', {header}' if exposedHeaders else header
    response['Access-Control-Expose-Headers'] = exposedHeaders
    return response
