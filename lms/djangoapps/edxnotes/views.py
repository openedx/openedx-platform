"""
Views related to EdxNotes.
"""


import json
import logging

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponse
from django.urls import reverse
from django.views.decorators.http import require_GET
from opaque_keys.edx.keys import CourseKey
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from common.djangoapps.edxmako.shortcuts import render_to_response
from common.djangoapps.util.json_request import JsonResponse, JsonResponseBadRequest
from lms.djangoapps.courseware.block_render import get_block_for_descriptor
from lms.djangoapps.courseware.courses import get_course_with_access
from lms.djangoapps.courseware.model_data import FieldDataCache
from lms.djangoapps.edxnotes.exceptions import EdxNotesParseError, EdxNotesServiceUnavailable
from lms.djangoapps.edxnotes.helpers import (
    DEFAULT_PAGE,
    DEFAULT_PAGE_SIZE,
    NoteJSONEncoder,
    delete_all_notes_for_user,
    get_course_position,
    get_edxnotes_id_token,
    get_notes,
    is_feature_enabled,
)
from openedx.core.djangoapps.user_api.accounts.permissions import CanRetireUser
from openedx.core.djangoapps.user_api.models import RetirementStateError, UserRetirementStatus

log = logging.getLogger(__name__)


@login_required
def edxnotes(request, course_id):
    """
    Displays the EdxNotes page.

    Arguments:
        request: HTTP request object
        course_id: course id

    Returns:
        Rendered HTTP response.
    """
    course_key = CourseKey.from_string(course_id)
    course = get_course_with_access(request.user, "load", course_key)

    if not is_feature_enabled(course, request.user):
        raise Http404

    notes_info = get_notes(request, course)
    has_notes = (len(notes_info.get('results')) > 0)
    context = {
        "course": course,
        "notes_endpoint": reverse("notes", kwargs={"course_id": course_id}),
        "notes": notes_info,
        "page_size": DEFAULT_PAGE_SIZE,
        "debug": settings.DEBUG,
        'position': None,
        'disabled_tabs': settings.NOTES_DISABLED_TABS,
        'has_notes': has_notes,
    }

    if not has_notes:
        field_data_cache = FieldDataCache.cache_for_block_descendents(
            course.id, request.user, course, depth=2
        )
        course_block = get_block_for_descriptor(
            request.user, request, course, field_data_cache, course_key, course=course
        )
        position = get_course_position(course_block)
        if position:
            context.update({
                'position': position,
            })

    return render_to_response("edxnotes/edxnotes.html", context)


@require_GET
@login_required
def notes(request, course_id):
    """
    Notes view to handle list and search requests.

    Query parameters:
        page: page number to get
        page_size: number of items in the page
        text: text string to search. If `text` param is missing then get all the
              notes for the current user for this course else get only those notes
              which contain the `text` value.

    Arguments:
        request: HTTP request object
        course_id: course id

    Returns:
        Paginated response as JSON. A sample response is below.
        {
          "count": 101,
          "num_pages": 11,
          "current_page": 1,
          "results": [
            {
              "chapter": {
                "index": 4,
                "display_name": "About Exams and Certificates",
                "location": "i4x://org/course/category/name@revision",
                "children": [
                  "i4x://org/course/category/name@revision"
                ]
              },
              "updated": "Dec 09, 2015 at 09:31 UTC",
              "tags": ["shadow","oil"],
              "quote": "foo bar baz",
              "section": {
                "display_name": "edX Exams",
                "location": "i4x://org/course/category/name@revision",
                "children": [
                  "i4x://org/course/category/name@revision",
                  "i4x://org/course/category/name@revision",
                ]
              },
              "created": "2015-12-09T09:31:17.338305Z",
              "ranges": [
                {
                  "start": "/div[1]/p[1]",
                  "end": "/div[1]/p[1]",
                  "startOffset": 0,
                  "endOffset": 6
                }
              ],
              "user": "50cf92f9a3d8489df95e583549b919df",
              "text": "first angry height hungry structure",
              "course_id": "edx/DemoX/Demo",
              "id": "1231",
              "unit": {
                "url": "/courses/edx%2FDemoX%2FDemo/courseware/1414ffd5143b4b508f739b563ab468b7/workflow/1",
                "display_name": "EdX Exams",
                "location": "i4x://org/course/category/name@revision"
              },
              "usage_id": "i4x://org/course/category/name@revision"
            } ],
          "next": "http://0.0.0.0:8000/courses/edx%2FDemoX%2FDemo/edxnotes/notes/?page=2&page_size=10",
          "start": 0,
          "previous": null
        }
    """
    course_key = CourseKey.from_string(course_id)
    course = get_course_with_access(request.user, 'load', course_key)

    if not is_feature_enabled(course, request.user):
        raise Http404

    page = request.GET.get('page') or DEFAULT_PAGE
    page_size = request.GET.get('page_size') or DEFAULT_PAGE_SIZE
    text = request.GET.get('text')

    try:
        notes_info = get_notes(
            request,
            course,
            page=page,
            page_size=page_size,
            text=text
        )
    except (EdxNotesParseError, EdxNotesServiceUnavailable) as err:
        return JsonResponseBadRequest({"error": str(err)}, status=500)

    return HttpResponse(json.dumps(notes_info, cls=NoteJSONEncoder), content_type="application/json")  # pylint: disable=http-response-with-content-type-json, http-response-with-json-dumps


@login_required
def get_token(request, course_id):  # pylint: disable=unused-argument
    """
    Get JWT ID-Token, in case you need new one.
    """
    return HttpResponse(get_edxnotes_id_token(request.user), content_type='text/plain')


@login_required
def edxnotes_visibility(request, course_id):
    """
    Handle ajax call from "Show notes" checkbox.
    """
    course_key = CourseKey.from_string(course_id)
    course = get_course_with_access(request.user, "load", course_key)
    field_data_cache = FieldDataCache([course], course_key, request.user)
    course_block = get_block_for_descriptor(
        request.user, request, course, field_data_cache, course_key, course=course
    )

    if not is_feature_enabled(course, request.user):
        raise Http404

    try:
        visibility = json.loads(request.body.decode('utf8'))["visibility"]
        course_block.edxnotes_visibility = visibility
        course_block.save()
        return JsonResponse(status=200)
    except (ValueError, KeyError):
        log.warning(
            "Could not decode request body as JSON and find a boolean visibility field: '%s'", request.body
        )
        return JsonResponseBadRequest()


class RetireUserView(APIView):
    """
    **Use Cases**

        A superuser or the user with the username specified by settings.RETIREMENT_SERVICE_WORKER_USERNAME can "retire"
        the user's data from the edx-notes-api (aka. Edxnotes) service, which will delete all notes (aka.  annotations)
        the user has made.

    **Example Requests**

        * POST /api/edxnotes/v1/retire_user/
          {
              "username": "an_original_username"
          }

    **Example Response**

        * HTTP 204 with empty body, indicating success.

        * HTTP 404 with empty body.  This can happen when:
          - The requested user does not exist in the retirement queue.

        * HTTP 405 (Method Not Allowed) with error message.  This can happen when:
          - RetirementStateError is thrown: the user is currently in a retirement state which cannot be acted on, such
            as a terminal or completed state.

        * HTTP 500 with error message.  This can happen when:
          - EdxNotesServiceUnavailable is thrown: the edx-notes-api IDA is not available.
    """

    permission_classes = (permissions.IsAuthenticated, CanRetireUser)

    def post(self, request):
        """
        Implements the retirement endpoint.
        """
        username = request.data['username']
        try:
            retirement = UserRetirementStatus.get_retirement_for_retirement_action(username)
            delete_all_notes_for_user(retirement.user)
        except UserRetirementStatus.DoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)
        except RetirementStateError as exc:
            return Response(str(exc), status=status.HTTP_405_METHOD_NOT_ALLOWED)
        except Exception as exc:  # pylint: disable=broad-except
            return Response(str(exc), status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        return Response(status=status.HTTP_204_NO_CONTENT)
