"""
Defines the "ReSTful" API for course modes.
"""


import logging

from django.shortcuts import get_object_or_404
from edx_rest_framework_extensions import permissions
from edx_rest_framework_extensions.auth.jwt.authentication import JwtAuthentication
from edx_rest_framework_extensions.auth.session.authentication import SessionAuthenticationAllowInactiveUser
from opaque_keys.edx.keys import CourseKey
from rest_framework import status
from rest_framework.generics import ListCreateAPIView, RetrieveUpdateDestroyAPIView
from rest_framework.response import Response

from common.djangoapps.course_modes.models import CourseMode
from common.djangoapps.course_modes.rest_api.serializers import CourseModeSerializer
from openedx.core.lib.api.authentication import BearerAuthenticationAllowInactiveUser
from openedx.core.lib.api.parsers import MergePatchParser

log = logging.getLogger(__name__)


class CourseModesMixin:
    """
    A base class for course modes views that specifies authentication, permissions,
    serialization, pagination, and the base queryset.
    """
    authentication_classes = (
        JwtAuthentication,
        BearerAuthenticationAllowInactiveUser,
        SessionAuthenticationAllowInactiveUser,
    )
    # When not considering JWT conditions, this permission class grants access
    # to any authenticated client that is staff.  When consider JWT, the client
    # must be granted access to this resource via their JWT scopes.
    permission_classes = (permissions.JWT_RESTRICTED_APPLICATION_OR_USER_ACCESS,)
    required_scopes = ['course_modes:read']
    serializer_class = CourseModeSerializer
    pagination_class = None
    lookup_field = 'course_id'


class CourseModesView(CourseModesMixin, ListCreateAPIView):
    """
    View to list or create course modes for a course.

    **Use Case**

        List all course modes for a course, or create a new
        course mode.

    **Example Requests**

        GET /api/course_modes/v1/courses/{course_id}/

            Returns a list of all existing course modes for a course.

        POST /api/course_modes/v1/courses/{course_id}/

            Creates a new course mode in a course.

    **Response Values**

        For each HTTP verb below, an HTTP 404 "Not Found" response is returned if the
        requested course id does not exist.

        GET: If the request is successful, an HTTP 200 "OK" response is returned
        along with a list of course mode dictionaries within a course.
        The details are contained in a JSON dictionary as follows:

          * course_id: The course identifier.
          * mode_slug: The short name for the course mode.
          * mode_display_name: The verbose name for the course mode.
          * min_price: The minimum price for which a user can
            enroll in this mode.
          * currency: The currency of the listed prices.
          * expiration_datetime: The date and time after which
            users cannot enroll in the course in this mode (not required for POST).
          * expiration_datetime_is_explicit: Whether the expiration_datetime field was
            explicitly set (not required for POST).
          * description: A description of this mode (not required for POST).
          * sku: The SKU for this mode (for ecommerce purposes, not required for POST).
          * bulk_sku: The bulk SKU for this mode (for ecommerce purposes, not required for POST).

        POST: If the request is successful, an HTTP 201 "Created" response is returned.
    """
    def get_queryset(self):
        lookup_url_kwarg = self.lookup_url_kwarg or self.lookup_field
        filter_kwargs = {self.lookup_field: self.kwargs[lookup_url_kwarg]}
        if 'course_id' in filter_kwargs:
            filter_kwargs['course_id'] = CourseKey.from_string(filter_kwargs['course_id'])
        return CourseMode.objects.filter(**filter_kwargs)


class CourseModesDetailView(CourseModesMixin, RetrieveUpdateDestroyAPIView):
    """
    View to retrieve, update, or delete a specific course mode for a course.

    **Use Case**

        Get or update course mode details for a specific course mode on a course.
        Or you may delete a specific course mode from a course.

    **Example Requests**

        GET /api/course_modes/v1/courses/{course_id}/{mode_slug}

            Returns details on an existing course mode for a course.

        PATCH /api/course_modes/v1/courses/{course_id}/{mode_slug}

            Updates (via merge) details of an existing course mode for a course.

        DELETE /api/course_modes/v1/courses/{course_id}/{mode_slug}

            Deletes an existing course mode for a course.

    **Response Values**

        For each HTTP verb below, an HTTP 404 "Not Found" response is returned if the
        requested course id does not exist, or the mode slug does not exist within the course.

        GET: If the request is successful, an HTTP 200 "OK" response is returned
        along with a details for a single course mode within a course.  The details are contained
        in a JSON dictionary as follows:

          * course_id: The course identifier.
          * mode_slug: The short name for the course mode.
          * mode_display_name: The verbose name for the course mode.
          * min_price: The minimum price for which a user can
            enroll in this mode.
          * currency: The currency of the listed prices.
          * expiration_datetime: The date and time after which
            users cannot enroll in the course in this mode (not required for PATCH).
          * expiration_datetime_is_explicit: Whether the expiration_datetime field was
            explicitly set (not required for PATCH).
          * description: A description of this mode (not required for PATCH).
          * sku: The SKU for this mode (for ecommerce purposes, not required for PATCH).
          * bulk_sku: The bulk SKU for this mode (for ecommerce purposes, not required for PATCH).

        PATCH: If the request is successful, an HTTP 204 "No Content" response is returned.
        If "application/merge-patch+json" is not the specified content type,
        a 415 "Unsupported Media Type" response is returned.

        DELETE: If the request is successful, an HTTP 204 "No Content" response is returned.
    """
    http_method_names = ['get', 'patch', 'delete', 'head', 'options']
    parser_classes = (MergePatchParser,)
    multiple_lookup_fields = ('course_id', 'mode_slug')
    queryset = CourseMode.objects.all()

    def get_object(self):
        queryset = self.get_queryset()
        query_filter = {}
        for field in self.multiple_lookup_fields:
            query_filter[field] = self.kwargs[field]

        if 'course_id' in query_filter:
            query_filter['course_id'] = CourseKey.from_string(query_filter['course_id'])

        obj = get_object_or_404(queryset, **query_filter)
        self.check_object_permissions(self.request, obj)
        return obj

    def patch(self, request, *args, **kwargs):
        """
        Performs a partial update of a CourseMode instance.
        """
        course_mode = self.get_object()
        serializer = self.serializer_class(course_mode, data=request.data, partial=True)

        if serializer.is_valid(raise_exception=True):
            serializer.save()  # can also raise ValidationError
            return Response(
                status=status.HTTP_204_NO_CONTENT,
                content_type='application/json',
            )
