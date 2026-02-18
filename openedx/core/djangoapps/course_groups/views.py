"""
Views related to course groups functionality.
"""


import logging
import re

from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User  # lint-amnesty, pylint: disable=imported-auth-user
from django.core.exceptions import ValidationError
from django.core.paginator import EmptyPage, Paginator
from django.http import Http404, HttpResponseBadRequest
from django.urls import reverse
from django.utils.translation import gettext
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_http_methods, require_POST
from edx_rest_framework_extensions.auth.jwt.authentication import JwtAuthentication
from edx_rest_framework_extensions.auth.session.authentication import SessionAuthenticationAllowInactiveUser
from edx_rest_framework_extensions.paginators import NamespacedPageNumberPagination
from opaque_keys.edx.keys import CourseKey
from rest_framework import permissions, status
from rest_framework.generics import GenericAPIView
from rest_framework.response import Response
from rest_framework.serializers import Serializer

from lms.djangoapps.courseware.courses import get_course, get_course_with_access
from common.djangoapps.edxmako.shortcuts import render_to_response
from openedx.core.djangoapps.course_groups.models import (
    CohortAssignmentNotAllowed,
    CohortChangeNotAllowed,
    CohortMembership,
)
from openedx.core.djangoapps.course_groups.permissions import IsStaffOrAdmin
from openedx.core.lib.api.authentication import BearerAuthenticationAllowInactiveUser
from openedx.core.lib.api.view_utils import DeveloperErrorViewMixin
from common.djangoapps.student.auth import has_course_author_access
from common.djangoapps.util.json_request import JsonResponse, expect_json

from . import api, cohorts
from .models import CourseUserGroup, CourseUserGroupPartitionGroup
from .serializers import CohortUsersAPISerializer

MAX_PAGE_SIZE = 100

log = logging.getLogger(__name__)


def json_http_response(data):
    """
    Return an HttpResponse with the data json-serialized and the right content
    type header.
    """
    return JsonResponse(data)


def split_by_comma_and_whitespace(cstr):
    """
    Split a string both by commas and whitespace.  Returns a list.
    """
    return re.split(r'[\s,]+', cstr)


def link_cohort_to_partition_group(cohort, partition_id, group_id):
    """
    Create cohort to partition_id/group_id link.
    """
    CourseUserGroupPartitionGroup(
        course_user_group=cohort,
        partition_id=partition_id,
        group_id=group_id,
    ).save()


def unlink_cohort_partition_group(cohort):
    """
    Remove any existing cohort to partition_id/group_id link.
    """
    CourseUserGroupPartitionGroup.objects.filter(course_user_group=cohort).delete()


# pylint: disable=invalid-name
def _get_course_cohort_settings_representation(cohort_id, is_cohorted):
    """
    Returns a JSON representation of a course cohort settings.
    """
    return {
        'id': cohort_id,
        'is_cohorted': is_cohorted,
    }


def _cohort_settings(course_key):
    """
    Fetch a course current cohort settings.
    """
    return _get_course_cohort_settings_representation(
        cohorts.get_course_cohort_id(course_key),
        cohorts.is_course_cohorted(course_key)
    )


def _get_cohort_representation(cohort, course):
    """
    Returns a JSON representation of a cohort.
    """
    group_id, partition_id = cohorts.get_group_info_for_cohort(cohort)
    assignment_type = cohorts.get_assignment_type(cohort)
    return {
        'name': cohort.name,
        'id': cohort.id,
        'user_count': cohort.users.filter(courseenrollment__course_id=course.location.course_key,
                                          courseenrollment__is_active=1).count(),
        'assignment_type': assignment_type,
        'user_partition_id': partition_id,
        'group_id': group_id,
    }


@require_http_methods(("GET", "PATCH"))
@ensure_csrf_cookie
@expect_json
@login_required
def course_cohort_settings_handler(request, course_key_string):
    """
    The restful handler for cohort setting requests. Requires JSON.
    This will raise 404 if user is not staff.
    GET
        Returns the JSON representation of cohort settings for the course.
    PATCH
        Updates the cohort settings for the course. Returns the JSON representation of updated settings.
    """
    course_key = CourseKey.from_string(course_key_string)
    # Although this course data is not used this method will return 404 is user is not staff
    get_course_with_access(request.user, 'staff', course_key)

    if request.method == 'PATCH':
        if 'is_cohorted' not in request.json:
            return JsonResponse({"error": "Bad Request"}, 400)

        is_cohorted = request.json.get('is_cohorted')
        try:
            cohorts.set_course_cohorted(course_key, is_cohorted)
        except ValueError as err:
            # Note: error message not translated because it is not exposed to the user (UI prevents this state).
            return JsonResponse({"error": str(err)}, 400)

    return JsonResponse(_get_course_cohort_settings_representation(
        cohorts.get_course_cohort_id(course_key),
        cohorts.is_course_cohorted(course_key)
    ))


@require_http_methods(("GET", "PUT", "POST", "PATCH"))
@ensure_csrf_cookie
@expect_json
@login_required
def cohort_handler(request, course_key_string, cohort_id=None):
    """
    The restful handler for cohort requests. Requires JSON.
    GET
        If a cohort ID is specified, returns a JSON representation of the cohort
            (name, id, user_count, assignment_type, user_partition_id, group_id).
        If no cohort ID is specified, returns the JSON representation of all cohorts.
           This is returned as a dict with the list of cohort information stored under the
           key `cohorts`.
    PUT or POST or PATCH
        If a cohort ID is specified, updates the cohort with the specified ID. Currently the only
        properties that can be updated are `name`, `user_partition_id` and `group_id`.
        Returns the JSON representation of the updated cohort.
        If no cohort ID is specified, creates a new cohort and returns the JSON representation of the updated
        cohort.
    """
    course_key = CourseKey.from_string(course_key_string)
    if not has_course_author_access(request.user, course_key):
        raise Http404('The requesting user does not have course author permissions.')

    course = get_course(course_key)

    if request.method == 'GET':
        if not cohort_id:
            all_cohorts = [
                _get_cohort_representation(c, course)
                for c in cohorts.get_course_cohorts(course)
            ]
            return JsonResponse({'cohorts': all_cohorts})
        else:
            cohort = cohorts.get_cohort_by_id(course_key, cohort_id)
            return JsonResponse(_get_cohort_representation(cohort, course))
    else:
        name = request.json.get('name')
        assignment_type = request.json.get('assignment_type')
        if not name:
            # Note: error message not translated because it is not exposed to the user (UI prevents this state).
            return JsonResponse({"error": "Cohort name must be specified."}, 400)
        if not assignment_type:
            # Note: error message not translated because it is not exposed to the user (UI prevents this state).
            return JsonResponse({"error": "Assignment type must be specified."}, 400)
        # If cohort_id is specified, update the existing cohort. Otherwise, create a new cohort.
        if cohort_id:
            cohort = cohorts.get_cohort_by_id(course_key, cohort_id)
            if name != cohort.name:
                if cohorts.is_cohort_exists(course_key, name):
                    err_msg = gettext("A cohort with the same name already exists.")
                    return JsonResponse({"error": str(err_msg)}, 400)
                cohort.name = name
                cohort.save()
            try:
                cohorts.set_assignment_type(cohort, assignment_type)
            except ValueError as err:
                return JsonResponse({"error": str(err)}, 400)
        else:
            try:
                cohort = cohorts.add_cohort(course_key, name, assignment_type)
            except ValueError as err:
                return JsonResponse({"error": str(err)}, 400)

        group_id = request.json.get('group_id')
        if group_id is not None:
            user_partition_id = request.json.get('user_partition_id')
            if user_partition_id is None:
                # Note: error message not translated because it is not exposed to the user (UI prevents this state).
                return JsonResponse(
                    {"error": "If group_id is specified, user_partition_id must also be specified."}, 400
                )
            existing_group_id, existing_partition_id = cohorts.get_group_info_for_cohort(cohort)
            if group_id != existing_group_id or user_partition_id != existing_partition_id:
                unlink_cohort_partition_group(cohort)
                link_cohort_to_partition_group(cohort, user_partition_id, group_id)
        else:
            # If group_id was specified as None, unlink the cohort if it previously was associated with a group.
            existing_group_id, _ = cohorts.get_group_info_for_cohort(cohort)
            if existing_group_id is not None:
                unlink_cohort_partition_group(cohort)

        return JsonResponse(_get_cohort_representation(cohort, course))


@ensure_csrf_cookie
def users_in_cohort(request, course_key_string, cohort_id):
    """
    Return users in the cohort.  Show up to 100 per page, and page
    using the 'page' GET attribute in the call.  Format:

    Returns:
        Json dump of dictionary in the following format:
        {'success': True,
         'page': page,
         'num_pages': paginator.num_pages,
         'users': [{'username': ..., 'email': ..., 'name': ...}]
    }
    """
    # this is a string when we get it here
    course_key = CourseKey.from_string(course_key_string)

    get_course_with_access(request.user, 'staff', course_key)

    # this will error if called with a non-int cohort_id.  That's ok--it
    # shouldn't happen for valid clients.
    cohort = cohorts.get_cohort_by_id(course_key, int(cohort_id))

    paginator = Paginator(cohort.users.all(), 100)
    try:
        page = int(request.GET.get('page'))
    except (TypeError, ValueError):
        # These strings aren't user-facing so don't translate them
        return HttpResponseBadRequest('Requested page must be numeric')
    else:
        if page < 0:
            return HttpResponseBadRequest('Requested page must be greater than zero')

    try:
        users = paginator.page(page)
    except EmptyPage:
        users = []  # When page > number of pages, return a blank page

    user_info = [{'username': u.username,
                  'email': u.email,
                  'name': f'{u.first_name} {u.last_name}'}
                 for u in users]

    return json_http_response({'success': True,
                               'page': page,
                               'num_pages': paginator.num_pages,
                               'users': user_info})


@ensure_csrf_cookie
@require_POST
def add_users_to_cohort(request, course_key_string, cohort_id):
    """
    Return json dict of:

    {'success': True,
     'added': [{'username': ...,
                'name': ...,
                'email': ...}, ...],
     'changed': [{'username': ...,
                  'name': ...,
                  'email': ...,
                  'previous_cohort': ...}, ...],
     'present': [str1, str2, ...],    # already there
     'unknown': [str1, str2, ...],
     'preassigned': [str1, str2, ...],
     'invalid': [str1, str2, ...]}

     Raises Http404 if the cohort cannot be found for the given course.
    """
    # this is a string when we get it here
    course_key = CourseKey.from_string(course_key_string)
    get_course_with_access(request.user, 'staff', course_key)

    try:
        cohort = cohorts.get_cohort_by_id(course_key, cohort_id)
    except CourseUserGroup.DoesNotExist:
        raise Http404("Cohort (ID {cohort_id}) not found for {course_key_string}".format(  # lint-amnesty, pylint: disable=raise-missing-from
            cohort_id=cohort_id,
            course_key_string=course_key_string
        ))

    users = request.POST.get('users', '')
    added = []
    changed = []
    present = []
    unknown = []
    preassigned = []
    invalid = []
    not_allowed = []
    for username_or_email in split_by_comma_and_whitespace(users):
        if not username_or_email:
            continue

        try:
            # A user object is only returned by add_user_to_cohort if the user already exists.
            (user, previous_cohort, preassignedCohort) = cohorts.add_user_to_cohort(cohort, username_or_email)

            if preassignedCohort:
                preassigned.append(username_or_email)
            elif previous_cohort:
                info = {'email': user.email,
                        'previous_cohort': previous_cohort,
                        'username': user.username}
                changed.append(info)
            else:
                info = {'username': user.username,
                        'email': user.email}
                added.append(info)
        except User.DoesNotExist:
            unknown.append(username_or_email)
        except ValidationError:
            invalid.append(username_or_email)
        except ValueError:
            present.append(username_or_email)
        except (CohortAssignmentNotAllowed, CohortChangeNotAllowed):
            not_allowed.append(username_or_email)

    return json_http_response({'success': True,
                               'added': added,
                               'changed': changed,
                               'present': present,
                               'unknown': unknown,
                               'preassigned': preassigned,
                               'invalid': invalid,
                               'not_allowed': not_allowed})


@ensure_csrf_cookie
@require_POST
def remove_user_from_cohort(request, course_key_string, cohort_id):  # lint-amnesty, pylint: disable=unused-argument
    """
    Expects 'username': username in POST data.

    Return json dict of:

    {'success': True} or
    {'success': False,
     'msg': error_msg}
    """
    # this is a string when we get it here
    course_key = CourseKey.from_string(course_key_string)
    get_course_with_access(request.user, 'staff', course_key)

    username = request.POST.get('username')
    if username is None:
        return json_http_response({'success': False, 'msg': 'No username specified'})

    try:
        api.remove_user_from_cohort(course_key, username)
    except User.DoesNotExist:
        log.debug('no user')
        return json_http_response({'success': False, 'msg': f"No user '{username}'"})

    return json_http_response({'success': True})


def debug_cohort_mgmt(request, course_key_string):
    """
    Debugging view for dev.
    """
    # this is a string when we get it here
    course_key = CourseKey.from_string(course_key_string)
    # add staff check to make sure it's safe if it's accidentally deployed.
    get_course_with_access(request.user, 'staff', course_key)

    context = {'cohorts_url': reverse(
        'cohorts',
        kwargs={'course_key': str(course_key)}
    )}
    return render_to_response('/course_groups/debug.html', context)


def _get_course_with_access(request, course_key_string, action='staff'):
    """
    Fetching a course with expected permission level
    """
    course_key = CourseKey.from_string(course_key_string)
    return course_key, get_course_with_access(request.user, action, course_key)


def _get_cohort_response(cohort, course):
    """
    Helper method that returns APIView Response of a cohort representation
    """
    return Response(_get_cohort_representation(cohort, course), status=status.HTTP_200_OK)


def _update_cohort_partition_group(cohort, group_id, user_partition_id, api_error_func):
    """
    Helper method to update the partition group association for a cohort.

    Note: This logic is duplicated from the legacy cohort_handler function (lines 218-234).
    We chose not to refactor cohort_handler to use this helper because:
    1. cohort_handler returns JsonResponse for errors, while this helper uses DRF's api_error pattern
    2. Unifying the error handling would require changing cohort_handler's interface or adding
       try/except blocks, which could have downstream effects on existing callers
    3. The legacy endpoint may be deprecated in favor of the v1 API in the future

    Args:
        cohort: The cohort to update
        group_id: The group_id from the request (can be None to unlink, or an integer to link)
        user_partition_id: The user_partition_id from the request
        api_error_func: Function to call to raise API errors (e.g., self.api_error)

    Raises:
        API error if group_id is provided without user_partition_id
    """
    if group_id is not None:
        if user_partition_id is None:
            raise api_error_func(
                status.HTTP_400_BAD_REQUEST,
                'If group_id is specified, user_partition_id must also be specified.',
                'missing-user-partition-id'
            )
        existing_group_id, existing_partition_id = cohorts.get_group_info_for_cohort(cohort)
        if group_id != existing_group_id or user_partition_id != existing_partition_id:
            unlink_cohort_partition_group(cohort)
            link_cohort_to_partition_group(cohort, user_partition_id, group_id)
    else:
        # If group_id was explicitly set to None, unlink the cohort from any partition group
        existing_group_id, _ = cohorts.get_group_info_for_cohort(cohort)
        if existing_group_id is not None:
            unlink_cohort_partition_group(cohort)


def _get_cohort_settings_response(course_key):
    """
    Helper method to return a serialized response for the cohort settings.
    """
    return Response(_cohort_settings(course_key))


class APIPermissions(GenericAPIView):
    """
    Helper class defining the authentication and permission class for the subclass views.
    """
    authentication_classes = (
        JwtAuthentication,
        BearerAuthenticationAllowInactiveUser,
        SessionAuthenticationAllowInactiveUser,
    )
    permission_classes = (permissions.IsAuthenticated, IsStaffOrAdmin)
    serializer_class = Serializer


class CohortSettings(DeveloperErrorViewMixin, APIPermissions):
    """
    **Use Cases**

        Get the cohort setting for a course.
        Set the cohort setting for a course.

    **Example Requests**:

        GET /api/cohorts/v1/settings/{course_id}
        PUT /api/cohorts/v1/settings/{course_id}

    **Response Values**

        * is_cohorted: current status of the cohort setting
    """

    def get(self, request, course_key_string):
        """
        Endpoint to fetch the course cohort settings.
        """
        course_key, _ = _get_course_with_access(request, course_key_string)
        return _get_cohort_settings_response(course_key)

    def put(self, request, course_key_string):
        """
        Endpoint to set the course cohort settings.
        """
        course_key, _ = _get_course_with_access(request, course_key_string)

        if 'is_cohorted' not in request.data:
            raise self.api_error(status.HTTP_400_BAD_REQUEST,
                                 'Missing field "is_cohorted".')
        try:
            cohorts.set_course_cohorted(course_key, request.data.get('is_cohorted'))
        except ValueError as err:
            raise self.api_error(status.HTTP_400_BAD_REQUEST, err)
        return _get_cohort_settings_response(course_key)


class CohortHandler(DeveloperErrorViewMixin, APIPermissions):
    """
    **Use Cases**

        Get the current cohorts in a course.
        Create a new cohort in a course.
        Modify a cohort in a course.

    **Example Requests**:

        GET /api/cohorts/v1/courses/{course_id}/cohorts
        POST /api/cohorts/v1/courses/{course_id}/cohorts
        GET /api/cohorts/v1/courses/{course_id}/cohorts/{cohort_id}
        PATCH /api/cohorts/v1/courses/{course_id}/cohorts/{cohort_id}

    **POST Request Values**

        * name (required): The string identifier for a cohort.
        * assignment_type (required): The string representing the assignment type ("manual" or "random").
        * user_partition_id (optional): The integer identifier of the UserPartition (content group configuration).
        * group_id (optional): The integer identifier of the specific group in the partition.
          If group_id is specified, user_partition_id must also be specified.

    **PATCH Request Values**

        * name (optional): The string identifier for a cohort.
        * assignment_type (optional): The string representing the assignment type ("manual" or "random").
        * user_partition_id (optional): The integer identifier of the UserPartition (content group configuration).
        * group_id (optional): The integer identifier of the specific group in the partition.
          Set group_id to null to remove the content group association.
          If group_id is specified (non-null), user_partition_id must also be specified.

        At least one of name, assignment_type, or group_id must be provided.

    **Response Values**

        * cohorts: List of cohorts.
        * cohort: A cohort representation:
            * name: The string identifier for a cohort.
            * id: The integer identifier for a cohort.
            * user_count: The number of students in the cohort.
            * assignment_type: The string representing the assignment type.
            * user_partition_id: The integer identifier of the UserPartition.
            * group_id: The integer identifier of the specific group in the partition.
    """
    queryset = []

    def get(self, request, course_key_string, cohort_id=None):
        """
        Endpoint to get either one or all cohorts.
        """
        course_key, course = _get_course_with_access(request, course_key_string, 'load')
        if not cohort_id:
            all_cohorts = cohorts.get_course_cohorts(course)
            paginator = NamespacedPageNumberPagination()
            paginator.max_page_size = MAX_PAGE_SIZE
            page = paginator.paginate_queryset(all_cohorts, request)
            response = [_get_cohort_representation(c, course) for c in page]
            return Response(response, status=status.HTTP_200_OK)
        else:
            cohort = cohorts.get_cohort_by_id(course_key, cohort_id)
            return _get_cohort_response(cohort, course)

    def post(self, request, course_key_string, cohort_id=None):
        """
        Endpoint to create a new cohort, must not include cohort_id.
        """
        if cohort_id is not None:
            raise self.api_error(status.HTTP_405_METHOD_NOT_ALLOWED,
                                 'Please use the parent endpoint.',
                                 'wrong-endpoint')
        course_key, course = _get_course_with_access(request, course_key_string)
        name = request.data.get('name')
        if not name:
            raise self.api_error(status.HTTP_400_BAD_REQUEST,
                                 '"name" must be specified to create cohort.',
                                 'missing-cohort-name')
        assignment_type = request.data.get('assignment_type')
        if not assignment_type:
            raise self.api_error(status.HTTP_400_BAD_REQUEST,
                                 '"assignment_type" must be specified to create cohort.',
                                 'missing-assignment-type')

        cohort = cohorts.add_cohort(course_key, name, assignment_type)

        # Handle optional group_id and user_partition_id for content group association
        group_id = request.data.get('group_id')
        user_partition_id = request.data.get('user_partition_id')
        if group_id is not None or user_partition_id is not None:
            _update_cohort_partition_group(cohort, group_id, user_partition_id, self.api_error)

        return _get_cohort_response(cohort, course)

    def patch(self, request, course_key_string, cohort_id=None):
        """
        Endpoint to update a cohort name, assignment type, and/or content group association.
        """
        if cohort_id is None:
            raise self.api_error(status.HTTP_405_METHOD_NOT_ALLOWED,
                                 'Request method requires cohort_id in path',
                                 'missing-cohort-id')
        name = request.data.get('name')
        assignment_type = request.data.get('assignment_type')
        # has_group_id checks key presence rather than truthiness because
        # group_id=null is a valid request to unlink the content group association,
        # which is distinct from group_id not being sent at all (no change).
        has_group_id = 'group_id' in request.data
        group_id = request.data.get('group_id')
        user_partition_id = request.data.get('user_partition_id')

        if not any((name, assignment_type, has_group_id)):
            raise self.api_error(status.HTTP_400_BAD_REQUEST,
                                 'Request must include name, assignment_type, and/or group_id.',
                                 'missing-fields')
        course_key, _ = _get_course_with_access(request, course_key_string)
        cohort = cohorts.get_cohort_by_id(course_key, cohort_id)
        if name is not None and name != cohort.name:
            if cohorts.is_cohort_exists(course_key, name):
                raise self.api_error(status.HTTP_400_BAD_REQUEST,
                                     'A cohort with the same name already exists.',
                                     'cohort-name-exists')
            cohort.name = name
            cohort.save()
        if assignment_type is not None:
            try:
                cohorts.set_assignment_type(cohort, assignment_type)
            except ValueError as e:
                raise self.api_error(status.HTTP_400_BAD_REQUEST, str(e), 'last-random-cohort')

        # Handle group_id and user_partition_id for content group association
        if has_group_id:
            _update_cohort_partition_group(cohort, group_id, user_partition_id, self.api_error)

        return Response(status=status.HTTP_204_NO_CONTENT)


class CohortUsers(DeveloperErrorViewMixin, APIPermissions):
    """
    **Use Cases**
        List users in a cohort
        Removes an user from a cohort.
        Add a user to a specific cohort.

    **Example Requests**

        GET /api/cohorts/v1/courses/{course_id}/cohorts/{cohort_id}/users
        DELETE /api/cohorts/v1/courses/{course_id}/cohorts/{cohort_id}/users/{username}
        POST /api/cohorts/v1/courses/{course_id}/cohorts/{cohort_id}/users/{username}
        POST /api/cohorts/v1/courses/{course_id}/cohorts/{cohort_id}/users

    **GET list of users in a cohort request parameters**

        * course_id (required): The course id of the course the cohort belongs to.
        * cohort_id (required): The cohort id of the cohort to list the users in.
        * page_size: A query string parameter with the number of results to return per page.
          Optional. Default: 10. Maximum: 100.
        * page: A query string parameter with the page number to retrieve. Optional. Default: 1.

    ** POST add a user to a cohort request parameters**

        * course_id (required): The course id of the course the cohort belongs to.
        * cohort_id (required): The cohort id of the cohort to list the users in.
        * users (required): A body JSON parameter with a list of usernames/email addresses of users
          to be added to the cohort.

    ** DELETE remove a user from a cohort request parameters**

        * course_id (required): The course id of the course the cohort belongs to.
        * cohort_id (required): The cohort id of the cohort to list the users in.
        * username (required): The username of the user to be removed from the given cohort.

    **GET Response Values**

        Returns a HTTP 404 Not Found response status code when:
            * The course corresponding to the corresponding course id could not be found.
            * The requesting user does not have staff access to the course.
            * The cohort corresponding to the given cohort id could not be found.
        Returns a HTTP 200 OK response status code to indicate success.

        * count: Number of users enrolled in the given cohort.
        * num_pages: Total number of pages of results.
        * current_page: Current page number.
        * start: The list index of the first item in the response.
        * previous: The URL of the previous page of results or null if it is the first page.
        * next: The URL of the next page of results or null if it is the last page.
        * results: A list of users in the cohort.
            * username: Username of the user.
            * email: Email address of the user.
            * name: Full name of the user.

    **POST Response Values**

        Returns a HTTP 404 Not Found response status code when:
            * The course corresponding to the corresponding course id could not be found.
            * The requesting user does not have staff access to the course.
            * The cohort corresponding to the given cohort id could not be found.
        Returns a HTTP 200 OK response status code to indicate success.

        * success: Boolean indicating if the operation was successful.
        * added: Usernames/emails of the users that have been added to the cohort.
        * changed: Usernames/emails of the users that have been moved to the cohort.
        * present: Usernames/emails of the users already present in the cohort.
        * unknown: Usernames/emails of the users with an unknown cohort.
        * preassigned: Usernames/emails of unenrolled users that have been preassigned to the cohort.
        * invalid: Invalid emails submitted.

    Adding multiple users to a cohort, send a request to:
    POST /api/cohorts/v1/courses/{course_id}/cohorts/{cohort_id}/users

    With a payload such as:
    {
        "users": [username1, username2, username3...]
    }

    **DELETE Response Values**

        Returns a HTTP 404 Not Found response status code when:
            * The course corresponding to the corresponding course id could not be found.
            * The requesting user does not have staff access to the course.
            * The cohort corresponding to the given cohort id could not be found.
            * The user corresponding to the given username could not be found.
        Returns a HTTP 204 No Content response status code to indicate success.
    """
    serializer_class = CohortUsersAPISerializer

    def _get_course_and_cohort(self, request, course_key_string, cohort_id):
        """
        Return the course and cohort for the given course_key_string and cohort_id.
        """
        course_key, _ = _get_course_with_access(request, course_key_string)

        try:
            cohort = cohorts.get_cohort_by_id(course_key, cohort_id)
        except CourseUserGroup.DoesNotExist:
            msg = 'Cohort (ID {cohort_id}) not found for {course_key_string}'.format(
                cohort_id=cohort_id,
                course_key_string=course_key_string
            )
            raise self.api_error(status.HTTP_404_NOT_FOUND, msg, 'cohort-not-found')  # lint-amnesty, pylint: disable=raise-missing-from
        return course_key, cohort

    def get(self, request, course_key_string, cohort_id, username=None):  # pylint: disable=unused-argument
        """
        Lists the users in a specific cohort.
        """

        _, cohort = self._get_course_and_cohort(request, course_key_string, cohort_id)
        queryset = cohort.users.all()
        page = self.paginate_queryset(queryset)

        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)

        return Response(self.get_serializer(queryset, many=True).data)

    def delete(self, request, course_key_string, cohort_id, username=None):
        """
        Removes and user from a specific cohort.

        Note: It's better to use the post method to move users between cohorts.
        """
        if username is None:
            raise self.api_error(status.HTTP_405_METHOD_NOT_ALLOWED,
                                 'Missing username in path',
                                 'missing-username')
        course_key, cohort = self._get_course_and_cohort(request, course_key_string, cohort_id)

        try:
            api.remove_user_from_cohort(course_key, username, cohort.id)
        except User.DoesNotExist:
            raise self.api_error(status.HTTP_404_NOT_FOUND, 'User does not exist.', 'user-not-found')  # lint-amnesty, pylint: disable=raise-missing-from
        except CohortMembership.DoesNotExist:  # pylint: disable=duplicate-except
            raise self.api_error(  # lint-amnesty, pylint: disable=raise-missing-from
                status.HTTP_400_BAD_REQUEST,
                'User not assigned to the given cohort.',
                'user-not-in-cohort'
            )
        return Response(status=status.HTTP_204_NO_CONTENT)

    def post(self, request, course_key_string, cohort_id, username=None):
        """
        Add given users to the cohort.
        """
        _, cohort = self._get_course_and_cohort(request, course_key_string, cohort_id)
        users = request.data.get('users')
        if not users:
            if username is not None:
                users = [username]
            else:
                raise self.api_error(status.HTTP_400_BAD_REQUEST, 'Missing users key in payload', 'missing-users')

        added, changed, present, unknown, preassigned, invalid = [], [], [], [], [], []
        for username_or_email in users:
            if not username_or_email:
                continue

            try:
                # A user object is only returned by add_user_to_cohort if the user already exists.
                (user, previous_cohort, preassignedCohort) = cohorts.add_user_to_cohort(cohort, username_or_email)

                if preassignedCohort:
                    preassigned.append(username_or_email)
                elif previous_cohort:
                    info = {
                        'email': user.email,
                        'previous_cohort': previous_cohort,
                        'username': user.username
                    }
                    changed.append(info)
                else:
                    info = {
                        'username': user.username,
                        'email': user.email
                    }
                    added.append(info)
            except User.DoesNotExist:
                unknown.append(username_or_email)
            except ValidationError:
                invalid.append(username_or_email)
            except ValueError:
                present.append(username_or_email)

        return Response({
            'success': True,
            'added': added,
            'changed': changed,
            'present': present,
            'unknown': unknown,
            'preassigned': preassigned,
            'invalid': invalid
        })
