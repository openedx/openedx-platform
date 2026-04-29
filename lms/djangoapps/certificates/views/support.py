"""
Certificate end-points used by the student support UI.

See lms/djangoapps/support for more details.

"""


import logging
import urllib
from functools import wraps

import nh3
from django.db import transaction
from django.db.models import Q
from django.http import HttpResponse, HttpResponseBadRequest, HttpResponseForbidden, HttpResponseServerError
from django.utils.translation import gettext as _
from django.views.decorators.http import require_GET, require_POST
from opaque_keys import InvalidKeyError
from opaque_keys.edx.keys import CourseKey

from common.djangoapps.student.models import CourseEnrollment, User
from common.djangoapps.util.json_request import JsonResponse
from lms.djangoapps.certificates.api import generate_certificate_task, get_certificates_for_user
from lms.djangoapps.certificates.generation_handler import CertificateGenerationNotAllowed
from lms.djangoapps.certificates.permissions import GENERATE_ALL_CERTIFICATES, VIEW_ALL_CERTIFICATES
from lms.djangoapps.instructor_task.api import generate_certificates_for_students
from openedx.core.djangoapps.content.course_overviews.api import get_course_overview_or_none

log = logging.getLogger(__name__)


def require_certificate_permission(permission):
    """
    View decorator that requires permission to view and regenerate certificates.
    """
    def inner(func):
        """
        The outer wrapper, used to allow the decorator to take optional arguments.
        """
        @wraps(func)
        def wrapper(request, *args, **kwargs):
            """
            The inner wrapper, which wraps the view function.
            """
            if request.user.has_perm(permission, 'global'):
                return func(request, *args, **kwargs)
            return HttpResponseForbidden()
        return wrapper
    return inner


@require_GET
@require_certificate_permission(VIEW_ALL_CERTIFICATES)
def search_certificates(request):
    """
    Search for certificates for a particular user OR along with the given course.

    Supports search by either username or email address along with course id.

    First filter the records for the given username/email and then filter against the given course id (if given).
    Show the 'Regenerate' button if a record found in 'generatedcertificate' model otherwise it will show the Generate
    button.

    Arguments:
        request (HttpRequest): The request object.

    Returns:
        JsonResponse

    Example Usage:
        GET /certificates/search?user=bob@example.com
        GET /certificates/search?user=bob@example.com&course_id=xyz

        Response: 200 OK
        Content-Type: application/json
        [
            {
                "username": "bob",
                "course_key": "edX/DemoX/Demo_Course",
                "type": "verified",
                "status": "downloadable",
                "download_url": "http://www.example.com/cert.pdf",
                "grade": "0.98",
                "created": 2015-07-31T00:00:00Z,
                "modified": 2015-07-31T00:00:00Z
            }
        ]

    """
    uncleaned_filter = urllib.parse.unquote(urllib.parse.quote_plus(request.GET.get("user", "")))
    user_filter = nh3.clean(uncleaned_filter)
    if not user_filter:
        msg = _("user is not given.")
        return HttpResponseBadRequest(msg)

    try:
        user = User.objects.get(Q(email=user_filter) | Q(username=user_filter))
    except User.DoesNotExist:
        return HttpResponseBadRequest(_("user '{user}' does not exist").format(user=user_filter))

    certificates = get_certificates_for_user(user.username)
    for cert in certificates:
        cert["course_key"] = str(cert["course_key"])
        cert["created"] = cert["created"].isoformat()
        cert["modified"] = cert["modified"].isoformat()
        cert["regenerate"] = not cert['is_pdf_certificate']

    course_id = urllib.parse.quote_plus(request.GET.get("course_id", ""), safe=':/')
    if course_id:
        try:
            course_key = CourseKey.from_string(course_id)
        except InvalidKeyError:
            return HttpResponseBadRequest(_("Course id '{course_id}' is not valid").format(course_id=course_id))
        else:
            course_overview = get_course_overview_or_none(course_key)
            if not course_overview:
                msg = _("The course does not exist against the given key '{course_key}'").format(course_key=course_key)
                return HttpResponseBadRequest(msg)

            certificates = [certificate for certificate in certificates
                            if certificate['course_key'] == course_id]
            if not certificates:
                return JsonResponse([{'username': user.username, 'course_key': course_id, 'regenerate': False}])

    return JsonResponse(certificates)


def _validate_post_params(params):
    """
    Validate request POST parameters to the generate and regenerate certificates end-point.

    Arguments:
        params (QueryDict): Request parameters.

    Returns: tuple of (dict, HttpResponse)

    """
    # Validate the username
    try:
        username = params.get("username")
        user = User.objects.get(username=username)
    except User.DoesNotExist:
        msg = _("User {username} does not exist").format(username=username)
        return None, HttpResponseBadRequest(msg)

    # Validate the course key
    try:
        course_key = CourseKey.from_string(params.get("course_key"))
    except InvalidKeyError:
        msg = _("{course_key} is not a valid course key").format(course_key=params.get("course_key"))
        return None, HttpResponseBadRequest(msg)

    return {"user": user, "course_key": course_key}, None


# Grades can potentially be written - if so, let grading manage the transaction.
@transaction.non_atomic_requests
@require_POST
@require_certificate_permission(GENERATE_ALL_CERTIFICATES)
def regenerate_certificate_for_user(request):
    """
    Regenerate certificates for a user.

    This is meant to be used by support staff through the UI in lms/djangoapps/support

    Arguments:
        request (HttpRequest): The request object

    Returns:
        HttpResponse

    Example Usage:

        POST /certificates/regenerate
            * username: "bob"
            * course_key: "edX/DemoX/Demo_Course"

        Response: 200 OK

    """
    # Check the POST parameters, returning a 400 response if they're not valid.
    params, response = _validate_post_params(request.POST)
    if response is not None:
        return response

    user = params["user"]
    course_key = params["course_key"]

    course_overview = get_course_overview_or_none(course_key)
    if not course_overview:
        msg = _("The course {course_key} does not exist").format(course_key=course_key)
        return HttpResponseBadRequest(msg)

    # Check that the user is enrolled in the course
    if not CourseEnrollment.is_enrolled(user, course_key):
        msg = _("User {user_id} is not enrolled in the course {course_key}").format(
            user_id=user.id,
            course_key=course_key
        )
        return HttpResponseBadRequest(msg)

    # Attempt to regenerate certificates
    try:
        generate_certificate_task(user, course_key)
    except CertificateGenerationNotAllowed as e:
        log.exception(
            "Certificate generation not allowed for user %s in course %s",
            str(user),
            course_key,
        )
        return HttpResponseBadRequest(str(e))
    except:  # noqa: E722
        # We are pessimistic about the kinds of errors that might get thrown by the
        # certificates API.  This may be overkill, but we're logging everything so we can
        # track down unexpected errors.
        log.exception(f"Could not regenerate certificate for user {user.id} in course {course_key}")
        return HttpResponseServerError(_("An unexpected error occurred while regenerating certificates."))

    log.info(
        f"Started regenerating certificates for user {user.id} in course {course_key} from the support page."
    )
    return HttpResponse(200)


@transaction.non_atomic_requests
@require_POST
@require_certificate_permission(GENERATE_ALL_CERTIFICATES)
def generate_certificate_for_user(request):
    """
    Generate certificates for a user.

    This is meant to be used by support staff through the UI in lms/djangoapps/support

    Arguments:
        request (HttpRequest): The request object

    Returns:
        HttpResponse

    Example Usage:

        POST /certificates/generate
            * username: "bob"
            * course_key: "edX/DemoX/Demo_Course"

        Response: 200 OK

    """
    # Check the POST parameters, returning a 400 response if they're not valid.
    params, response = _validate_post_params(request.POST)
    if response is not None:
        return response

    course_overview = get_course_overview_or_none(params["course_key"])
    if not course_overview:
        msg = _("The course {course_key} does not exist").format(course_key=params["course_key"])
        return HttpResponseBadRequest(msg)

    # Check that the user is enrolled in the course
    if not CourseEnrollment.is_enrolled(params["user"], params["course_key"]):
        msg = _("User {username} is not enrolled in the course {course_key}").format(
            username=params["user"].username,
            course_key=params["course_key"]
        )
        return HttpResponseBadRequest(msg)

    # Attempt to generate certificate
    generate_certificates_for_students(
        request,
        params["course_key"],
        student_set="specific_student",
        specific_student_id=params["user"].id
    )
    return HttpResponse(200)
