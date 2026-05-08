"""
Views for user API
"""


import datetime
import logging
from functools import cached_property
from typing import Dict, List, Optional, Set  # noqa: UP035

import pytz
from completion.exceptions import UnavailableCompletionData
from completion.models import BlockCompletion
from completion.utilities import get_key_to_last_completed_block
from django.contrib.auth.models import User  # pylint: disable=imported-auth-user
from django.contrib.auth.signals import user_logged_in
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect
from django.utils import dateparse
from django.utils.decorators import method_decorator
from edx_rest_framework_extensions.paginators import DefaultPagination
from opaque_keys import InvalidKeyError
from opaque_keys.edx.keys import UsageKey
from opaque_keys.edx.locator import CourseLocator
from rest_framework import generics, views
from rest_framework.decorators import api_view
from rest_framework.permissions import SAFE_METHODS
from rest_framework.response import Response
from xblock.fields import Scope
from xblock.runtime import KeyValueStore

from common.djangoapps.student.models import (  # pylint: disable=reimported  # noqa: F811
    CourseEnrollment,
    User,
)
from lms.djangoapps.courseware.access import is_mobile_available_for_user
from lms.djangoapps.courseware.access_utils import ACCESS_GRANTED
from lms.djangoapps.courseware.block_render import get_block_for_descriptor
from lms.djangoapps.courseware.context_processor import get_user_timezone_or_last_seen_timezone_or_utc
from lms.djangoapps.courseware.courses import get_current_child
from lms.djangoapps.courseware.model_data import FieldDataCache
from lms.djangoapps.courseware.models import StudentModule
from lms.djangoapps.courseware.views.index import save_positions_recursively_up
from lms.djangoapps.mobile_api.models import MobileConfig
from lms.djangoapps.mobile_api.utils import API_V05, API_V1, API_V2, API_V3, API_V4
from openedx.core.djangoapps.site_configuration.helpers import get_current_site_orgs
from openedx.features.course_duration_limits.access import check_course_expired
from xmodule.modulestore.django import modulestore  # pylint: disable=wrong-import-order
from xmodule.modulestore.exceptions import ItemNotFoundError  # pylint: disable=wrong-import-order

from .. import errors
from ..decorators import mobile_course_access, mobile_view
from .enums import EnrollmentStatuses
from .serializers import (
    CourseEnrollmentSerializer,
    CourseEnrollmentSerializerModifiedForPrimary,
    CourseEnrollmentSerializerv05,
    UserSerializer,
)

log = logging.getLogger(__name__)


@mobile_view(is_user=True)
class UserDetail(generics.RetrieveAPIView):
    """
    **Use Case**

        Get information about the specified user and access other resources
        the user has permissions for.

        Users are redirected to this endpoint after they sign in.

        You can use the **course_enrollments** value in the response to get a
        list of courses the user is enrolled in.

    **Example Request**

        GET /api/mobile/{version}/users/{username}

    **Response Values**

        If the request is successful, the request returns an HTTP 200 "OK" response.

        The HTTP 200 response has the following values.

        * course_enrollments: The URI to list the courses the currently signed
          in user is enrolled in.
        * email: The email address of the currently signed in user.
        * id: The ID of the user.
        * name: The full name of the currently signed in user.
        * username: The username of the currently signed in user.
    """
    queryset = (
        User.objects.all().select_related('profile')
    )
    serializer_class = UserSerializer
    lookup_field = 'username'

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context['api_version'] = self.kwargs.get('api_version')
        return context


@mobile_view(is_user=True)
@method_decorator(transaction.non_atomic_requests, name='dispatch')
class UserCourseStatus(views.APIView):
    """
    **Use Cases**

        Get or update the ID of the module that the specified user last
        visited in the specified course.

        Get ID of the last completed block in case of version v1

    **Example Requests**

        GET /api/mobile/{version}/users/{username}/course_status_info/{course_id}

        PATCH /api/mobile/{version}/users/{username}/course_status_info/{course_id}

        **PATCH Parameters**

          The body of the PATCH request can include the following parameters.

          * last_visited_module_id={module_id}
          * modification_date={date}

            The modification_date parameter is optional. If it is present, the
            update will only take effect if the modification_date in the
            request is later than the modification_date saved on the server.

    **Response Values**

        If the request is successful, the request returns an HTTP 200 "OK" response.

        The HTTP 200 response has the following values.

        * last_visited_module_id: The ID of the last module that the user
          visited in the course.
        * last_visited_module_path: The ID of the modules in the path from the
          last visited module to the course block.

        For version v1 GET request response includes the following values.

        * last_visited_block_id: ID of the last completed block.

    """

    http_method_names = ["get", "patch"]

    def dispatch(self, request, *args, **kwargs):
        if request.method in SAFE_METHODS:
            return super().dispatch(request, *args, **kwargs)
        else:
            with transaction.atomic():
                return super().dispatch(request, *args, **kwargs)

    def _last_visited_block_path(self, request, course):
        """
        Returns the path from the last block visited by the current user in the given course up to
        the course block. If there is no such visit, the first item deep enough down the course
        tree is used.
        """
        field_data_cache = FieldDataCache.cache_for_block_descendents(
            course.id, request.user, course, depth=2)

        course_block = get_block_for_descriptor(
            request.user, request, course, field_data_cache, course.id, course=course
        )

        path = [course_block] if course_block else []
        chapter = get_current_child(course_block, min_depth=2)
        if chapter is not None:
            path.append(chapter)
            section = get_current_child(chapter, min_depth=1)
            if section is not None:
                path.append(section)

        path.reverse()
        return path

    def _get_course_info(self, request, course):
        """
        Returns the course status
        """
        path = self._last_visited_block_path(request, course)
        path_ids = [str(block.location) for block in path]
        return Response({
            "last_visited_module_id": path_ids[0],
            "last_visited_module_path": path_ids,
        })

    def _update_last_visited_module_id(self, request, course, module_key, modification_date):
        """
        Saves the module id if the found modification_date is less recent than the passed modification date
        """
        field_data_cache = FieldDataCache.cache_for_block_descendents(
            course.id, request.user, course, depth=2)
        try:
            descriptor = modulestore().get_item(module_key)
        except ItemNotFoundError:
            log.error(f"{errors.ERROR_INVALID_MODULE_ID} %s", module_key)
            return Response(errors.ERROR_INVALID_MODULE_ID, status=400)
        block = get_block_for_descriptor(
            request.user, request, descriptor, field_data_cache, course.id, course=course
        )

        if modification_date:
            key = KeyValueStore.Key(
                scope=Scope.user_state,
                user_id=request.user.id,
                block_scope_id=course.location,
                field_name='position'
            )
            original_store_date = field_data_cache.last_modified(key)
            if original_store_date is not None and modification_date < original_store_date:
                # old modification date so skip update
                return self._get_course_info(request, course)

        save_positions_recursively_up(request.user, request, field_data_cache, block, course=course)
        return self._get_course_info(request, course)

    @mobile_course_access(depth=2)
    def get(self, request, course, *args, **kwargs):  # pylint: disable=unused-argument
        """
        Get the ID of the module that the specified user last visited in the specified course.
        """
        user_course_status = self._get_course_info(request, course)

        api_version = self.kwargs.get("api_version")
        if api_version == API_V1:
            # Get ID of the block that the specified user last visited in the specified course.
            try:
                block_id = str(get_key_to_last_completed_block(request.user, course.id))
            except UnavailableCompletionData:
                block_id = ""

            user_course_status.data["last_visited_block_id"] = block_id

        return user_course_status

    @mobile_course_access(depth=2)
    def patch(self, request, course, *args, **kwargs):  # pylint: disable=unused-argument
        """
        Update the ID of the module that the specified user last visited in the specified course.
        """
        module_id = request.data.get("last_visited_module_id")
        modification_date_string = request.data.get("modification_date")
        modification_date = None
        if modification_date_string:
            modification_date = dateparse.parse_datetime(modification_date_string)
            if not modification_date or not modification_date.tzinfo:
                log.error(f"{errors.ERROR_INVALID_MODIFICATION_DATE} %s", modification_date_string)
                return Response(errors.ERROR_INVALID_MODIFICATION_DATE, status=400)

        if module_id:
            try:
                module_key = UsageKey.from_string(module_id)
            except InvalidKeyError:
                log.error(f"{errors.ERROR_INVALID_MODULE_ID} %s", module_id)
                return Response(errors.ERROR_INVALID_MODULE_ID, status=400)

            return self._update_last_visited_module_id(request, course, module_key, modification_date)
        else:
            # The arguments are optional, so if there's no argument just succeed
            return self._get_course_info(request, course)


@mobile_view(is_user=True)
class UserCourseEnrollmentsList(generics.ListAPIView):
    """
    **Use Case**

        Get information about the courses that the currently signed in user is
        enrolled in.

        v1 differs from v0.5 version by returning ALL enrollments for
        a user rather than only the enrollments the user has access to (that haven't expired).
        An additional attribute "expiration" has been added to the response, which lists the date
        when access to the course will expire or null if it doesn't expire.

        In v4 we added to the response primary object. Primary object contains the latest user's enrollment
        or course where user has the latest progress. Primary object has been cut from user's
        enrolments array and inserted into separated section with key `primary`.

    **Example Request**

        GET /api/mobile/v1/users/{username}/course_enrollments/

    **Response Values**

        If the request for information about the user is successful, the
        request returns an HTTP 200 "OK" response.

        The HTTP 200 response has the following values.

        * expiration: The course expiration date for given user course pair
          or null if the course does not expire.
        * certificate: Information about the user's earned certificate in the
          course.
        * course: A collection of the following data about the course.

        * courseware_access: A JSON representation with access information for the course,
          including any access errors.

          * course_about: The URL to the course about page.
          * course_sharing_utm_parameters: Encoded UTM parameters to be included in course sharing url
          * course_handouts: The URI to get data for course handouts.
          * course_image: The path to the course image.
          * course_updates: The URI to get data for course updates.
          * discussion_url: The URI to access data for course discussions if
            it is enabled, otherwise null.
          * end: The end date of the course.
          * id: The unique ID of the course.
          * name: The name of the course.
          * number: The course number.
          * org: The organization that created the course.
          * start: The date and time when the course starts.
          * start_display:
            If start_type is a string, then the advertised_start date for the course.
            If start_type is a timestamp, then a formatted date for the start of the course.
            If start_type is empty, then the value is None and it indicates that the course has not yet started.
          * start_type: One of either "string", "timestamp", or "empty"
          * subscription_id: A unique "clean" (alphanumeric with '_') ID of
            the course.
          * video_outline: The URI to get the list of all videos that the user
            can access in the course.

        * created: The date the course was created.
        * is_active: Whether the course is currently active. Possible values
          are true or false.
        * mode: The type of certificate registration for this course (honor or
          certified).
        * url: URL to the downloadable version of the certificate, if exists.
        * course_progress: Contains information about how many assignments are in the course
          and how many assignments the student has completed.
        * total_assignments_count: Total course's assignments count.
        * assignments_completed: Assignments witch the student has completed.
    """

    lookup_field = 'username'

    # In Django Rest Framework v3, there is a default pagination
    # class that transmutes the response data into a dictionary
    # with pagination information.  The original response data (a list)
    # is stored in a "results" value of the dictionary.
    # For backwards compatibility with the existing API, we disable
    # the default behavior by setting the pagination_class to None.
    pagination_class = None

    def is_org(self, check_org, course_org):
        """
        Check course org matches request org param or no param provided
        """
        current_orgs = get_current_site_orgs()

        if current_orgs and course_org not in current_orgs:
            return False

        return check_org is None or (check_org.lower() == course_org.lower())

    def get_serializer_context(self):
        context = super().get_serializer_context()
        requested_fields = self.request.GET.get('requested_fields', '')

        context['api_version'] = self.kwargs.get('api_version')
        context['requested_fields'] = requested_fields.split(',')
        return context

    def get_serializer_class(self):
        api_version = self.kwargs.get('api_version')
        if api_version == API_V05:
            return CourseEnrollmentSerializerv05
        return CourseEnrollmentSerializer

    @cached_property
    def queryset_for_user(self):
        """
        Find and return the list of course enrollments for the user.

        In v4 added filtering by statuses.
        """
        api_version = self.kwargs.get('api_version')
        status = self.request.GET.get('status')
        username = self.kwargs['username']

        queryset = CourseEnrollment.objects.all().select_related('course', 'user').filter(
            user__username=username,
            is_active=True
        ).order_by('-created')

        if api_version == API_V4 and status in EnrollmentStatuses.values():
            if status == EnrollmentStatuses.IN_PROGRESS.value:
                queryset = queryset.in_progress(username=username, time_zone=self.user_timezone)
            elif status == EnrollmentStatuses.COMPLETED.value:
                queryset = queryset.completed(username=username)
            elif status == EnrollmentStatuses.EXPIRED.value:
                queryset = queryset.expired(username=username, time_zone=self.user_timezone)

        return queryset

    def get_queryset(self):
        api_version = self.kwargs.get('api_version')
        status = self.request.GET.get('status')
        mobile_available = self.get_same_org_mobile_available_enrollments()

        not_duration_limited = (
            enrollment for enrollment in mobile_available
            if check_course_expired(self.request.user, enrollment.course) == ACCESS_GRANTED
        )

        if api_version == API_V4 and status not in EnrollmentStatuses.values():
            primary_enrollment_obj = self.get_primary_enrollment_by_latest_enrollment_or_progress()
            if primary_enrollment_obj:
                mobile_available.remove(primary_enrollment_obj)

        if api_version == API_V05:
            # for v0.5 don't return expired courses
            return list(not_duration_limited)
        else:
            # return all courses, with associated expiration
            return mobile_available

    def get_same_org_mobile_available_enrollments(self) -> list[CourseEnrollment]:
        """
        Gets list with `CourseEnrollment` for mobile available courses.
        """
        org = self.request.query_params.get('org', None)

        same_org = (
            enrollment for enrollment in self.queryset_for_user
            if enrollment.course_overview and self.is_org(org, enrollment.course_overview.org)
        )
        mobile_available = (
            enrollment for enrollment in same_org
            if is_mobile_available_for_user(self.request.user, enrollment.course_overview)
        )
        return list(mobile_available)

    def list(self, request, *args, **kwargs):
        response = super().list(request, *args, **kwargs)
        api_version = self.kwargs.get('api_version')
        status = self.request.GET.get('status')

        if api_version in (API_V2, API_V3, API_V4):
            enrollment_data = {
                'configs': MobileConfig.get_structured_configs(),
                'user_timezone': str(self.user_timezone),
                'enrollments': response.data
            }
            if api_version == API_V4 and status not in EnrollmentStatuses.values():
                primary_enrollment_obj = self.get_primary_enrollment_by_latest_enrollment_or_progress()
                if primary_enrollment_obj:
                    serializer = CourseEnrollmentSerializerModifiedForPrimary(
                        primary_enrollment_obj,
                        context=self.get_serializer_context(),
                    )
                    enrollment_data.update({'primary': serializer.data})

            return Response(enrollment_data)

        return response

    @cached_property
    def user_timezone(self):
        """
        Get the user's timezone.
        """
        return get_user_timezone_or_last_seen_timezone_or_utc(self.get_user())

    def get_user(self) -> User:
        """
        Get user object by username.
        """
        return get_object_or_404(User, username=self.kwargs['username'])

    def get_primary_enrollment_by_latest_enrollment_or_progress(self) -> Optional[CourseEnrollment]:  # noqa: UP045
        """
        Gets primary enrollment obj by latest enrollment or latest progress on the course.
        """
        mobile_available = self.get_same_org_mobile_available_enrollments()
        if not mobile_available:
            return None

        mobile_available_course_ids = [enrollment.course_id for enrollment in mobile_available]

        latest_enrollment = self.queryset_for_user.filter(
            course__id__in=mobile_available_course_ids
        ).order_by('-created').first()

        if not latest_enrollment:
            return None

        latest_progress = StudentModule.objects.filter(
            student__username=self.kwargs['username'],
            course_id__in=mobile_available_course_ids,
        ).order_by('-modified').first()

        if not latest_progress:
            return latest_enrollment

        enrollment_with_latest_progress = self.queryset_for_user.filter(
            course_id=latest_progress.course_id,
            user__username=self.kwargs['username'],
        ).first()

        if latest_enrollment.created > latest_progress.modified:
            return latest_enrollment
        else:
            return enrollment_with_latest_progress

    # pylint: disable=attribute-defined-outside-init
    @property
    def paginator(self):
        """
        Override API View paginator property to dynamically determine pagination class

        Implements solutions from the discussion at
        https://www.github.com/encode/django-rest-framework/issues/6397.
        """
        super().paginator  # pylint: disable=expression-not-assigned  # noqa: B018
        api_version = self.kwargs.get('api_version')

        if self._paginator is None and api_version == API_V3:
            self._paginator = DefaultPagination()
        if self._paginator is None and api_version == API_V4:
            self._paginator = UserCourseEnrollmentsV4Pagination()

        return self._paginator


@api_view(["GET"])
@mobile_view()
def my_user_info(request, api_version):
    """
    Redirect to the currently-logged-in user's info page
    """
    # update user's last logged in from here because
    # updating it from the oauth2 related code is too complex
    user_logged_in.send(sender=User, user=request.user, request=request)
    return redirect("user-detail", api_version=api_version, username=request.user.username)


@mobile_view(is_user=True)
class UserEnrollmentsStatus(views.APIView):
    """
    **Use Case**

        Get information about user's enrolments status.

        Returns active enrolment status if user was enrolled for the course
        less than 30 days ago or has progressed in the course in the last 30 days.
        Otherwise, the registration is considered inactive.

        USER_ENROLLMENTS_LIMIT - adds users enrollments query limit to
        safe API from possible DDOS attacks.

    **Example Request**

        GET /api/mobile/{api_version}/users/<user_name>/enrollments_status/

    **Response Values**

        If the request for information about the user's enrolments is successful, the
        request returns an HTTP 200 "OK" response.

        The HTTP 200 response has the following values.

        * course_id (str): The course id associated with the user's enrollment.
        * course_name (str): The course name associated with the user's enrollment.
        * recently_active (bool): User's course enrolment status.


        The HTTP 200 response contains a list of dictionaries that contain info
        about each user's enrolment status.

    **Example Response**

        ```json
        [
            {
                "course_id": "course-v1:a+a+a",
                "course_name": "a",
                "recently_active": true
            },
            {
                "course_id": "course-v1:b+b+b",
                "course_name": "b",
                "recently_active": true
            },
            {
                "course_id": "course-v1:c+c+c",
                "course_name": "c",
                "recently_active": false
            },
            ...
        ]
        ```
    """

    USER_ENROLLMENTS_LIMIT = 500

    def get(self, request, *args, **kwargs) -> Response:
        """
        Gets user's enrollments status.
        """
        active_status_date = datetime.datetime.now(pytz.UTC) - datetime.timedelta(days=30)
        username = kwargs.get('username')
        course_ids_where_user_has_completions = self._get_course_ids_where_user_has_completions(
            username,
            active_status_date,
        )
        enrollments_status = self._build_enrollments_status_dict(
            username,
            active_status_date,
            course_ids_where_user_has_completions
        )
        return Response(enrollments_status)

    def _build_enrollments_status_dict(
        self,
        username: str,
        active_status_date: datetime,
        course_ids: Set[CourseLocator],  # noqa: UP006
    ) -> List[Dict[str, bool]]:  # noqa: UP006
        """
        Builds list with dictionaries with user's enrolments statuses.
        """
        user = get_object_or_404(User, username=username)
        user_enrollments = (
            CourseEnrollment
            .enrollments_for_user(user)
            .select_related('course')
            [:self.USER_ENROLLMENTS_LIMIT]
        )
        mobile_available = [
            enrollment for enrollment in user_enrollments
            if is_mobile_available_for_user(user, enrollment.course_overview)
        ]
        enrollments_status = []
        for user_enrollment in mobile_available:
            course_id = user_enrollment.course_overview.id
            enrollments_status.append(
                {
                    'course_id': str(course_id),
                    'course_name': user_enrollment.course_overview.display_name,
                    'recently_active': bool(
                        course_id in course_ids
                        or user_enrollment.created > active_status_date
                    )
                }
            )
        return enrollments_status

    @staticmethod
    def _get_course_ids_where_user_has_completions(
        username: str,
        active_status_date: datetime,
    ) -> Set[CourseLocator]:  # noqa: UP006
        """
        Gets course keys where user has completions.
        """
        context_keys = BlockCompletion.objects.filter(
            user__username=username,
            created__gte=active_status_date
        ).values_list('context_key', flat=True).distinct()

        return set(context_keys)


class UserCourseEnrollmentsV4Pagination(DefaultPagination):
    """
    Pagination for `UserCourseEnrollments` API v4.
    """
    page_size = 5
    max_page_size = 50
