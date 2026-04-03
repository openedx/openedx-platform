"""
HTTP end-points for the Bookmarks API.

For more information, see:
https://openedx.atlassian.net/wiki/display/TNL/Bookmarks+API
"""


import logging

import edx_api_doc_tools as apidocs
import eventtracking
from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.utils.translation import gettext as _
from django.utils.translation import gettext_noop
from edx_rest_framework_extensions.paginators import DefaultPagination
from opaque_keys import InvalidKeyError
from opaque_keys.edx.keys import CourseKey, UsageKey
from rest_framework import permissions, status
from rest_framework.authentication import SessionAuthentication
from rest_framework.generics import ListCreateAPIView
from rest_framework.response import Response
from rest_framework.views import APIView

from openedx.core.djangoapps.bookmarks.api import BookmarksLimitReachedError
from openedx.core.lib.api.authentication import BearerAuthentication
from openedx.core.lib.api.permissions import IsUserInUrl
from openedx.core.lib.url_utils import unquote_slashes
from xmodule.modulestore.exceptions import ItemNotFoundError  # lint-amnesty, pylint: disable=wrong-import-order

from . import DEFAULT_FIELDS, OPTIONAL_FIELDS, api
from .serializers import BookmarkSerializer

log = logging.getLogger(__name__)


# Default error message for user
DEFAULT_USER_MESSAGE = gettext_noop('An error has occurred. Please try again.')


class BookmarksPagination(DefaultPagination):
    """
    Paginator for bookmarks API.
    """
    page_size = 10
    max_page_size = 100

    def get_paginated_response(self, data):
        """
        Annotate the response with pagination information.
        """
        response = super().get_paginated_response(data)

        # Add `current_page` value, it's needed for pagination footer.
        response.data["current_page"] = self.page.number

        # Add `start` value, it's needed for the pagination header.
        response.data["start"] = (self.page.number - 1) * self.get_page_size(self.request)

        return response


class BookmarksViewMixin:
    """
    Shared code for bookmarks views.
    """

    def fields_to_return(self, params):
        """
        Returns names of fields which should be included in the response.

        Arguments:
            params (dict): The request parameters.
        """
        optional_fields = params.get('fields', '').split(',')
        return DEFAULT_FIELDS + [field for field in optional_fields if field in OPTIONAL_FIELDS]

    def error_response(self, developer_message, user_message=None, error_status=status.HTTP_400_BAD_REQUEST):
        """
        Create and return a Response.

        Arguments:
            message (string): The message to put in the developer_message
                and user_message fields.
            status: The status of the response. Default is HTTP_400_BAD_REQUEST.
        """
        if not user_message:
            user_message = developer_message

        return Response(
            {
                "developer_message": developer_message,
                "user_message": _(user_message)  # lint-amnesty, pylint: disable=translation-of-non-string
            },
            status=error_status
        )


class BookmarksListView(ListCreateAPIView, BookmarksViewMixin):
    """REST endpoints for lists of bookmarks."""

    authentication_classes = (BearerAuthentication, SessionAuthentication,)
    pagination_class = BookmarksPagination
    permission_classes = (permissions.IsAuthenticated,)
    serializer_class = BookmarkSerializer

    @apidocs.schema(
        parameters=[
            apidocs.string_parameter(
                'course_id',
                apidocs.ParameterLocation.QUERY,
                description="The id of the course to limit the list",
            ),
            apidocs.string_parameter(
                'fields',
                apidocs.ParameterLocation.QUERY,
                description="The fields to return: display_name, path.",
            ),
        ],
    )
    def get(self, request, *args, **kwargs):
        """
        Get a paginated list of bookmarks for a user.

        The list can be filtered by passing parameter "course_id=<course_id>"
        to only include bookmarks from a particular course.

        The bookmarks are always sorted in descending order by creation date.

        Each page in the list contains 10 bookmarks by default. The page
        size can be altered by passing parameter "page_size=<page_size>".

        To include the optional fields pass the values in "fields" parameter
        as a comma separated list. Possible values are:

        * "display_name"
        * "path"

        **Example Requests**

        GET /api/bookmarks/v1/bookmarks/?course_id={course_id1}&fields=display_name,path
        """
        return super().get(request, *args, **kwargs)

    def get_serializer_context(self):
        """
        Return the context for the serializer.
        """
        context = super().get_serializer_context()
        if self.request.method == 'GET':
            context['fields'] = self.fields_to_return(self.request.query_params)
        return context

    def get_queryset(self):
        """
        Returns queryset of bookmarks for GET requests.

        The results will only include bookmarks for the request's user.
        If the course_id is specified in the request parameters,
        the queryset will only include bookmarks from that course.
        """
        course_id = self.request.query_params.get('course_id', None)

        if course_id:
            try:
                course_key = CourseKey.from_string(course_id)
            except InvalidKeyError:
                log.error('Invalid course_id: %s.', course_id)
                return []
        else:
            course_key = None

        return api.get_bookmarks(
            user=self.request.user, course_key=course_key,
            fields=self.fields_to_return(self.request.query_params), serialized=False
        )

    def paginate_queryset(self, queryset):
        """ Override GenericAPIView.paginate_queryset for the purpose of eventing """
        page = super().paginate_queryset(queryset)

        course_id = self.request.query_params.get('course_id')
        if course_id:
            try:
                CourseKey.from_string(course_id)
            except InvalidKeyError:
                return page

        event_data = {
            'list_type': 'all_courses',
            'bookmarks_count': self.paginator.page.paginator.count,
            'page_size': self.paginator.page.paginator.per_page,
            'page_number': self.paginator.page.number,
        }
        if course_id is not None:
            event_data['list_type'] = 'per_course'
            event_data['course_id'] = course_id

        eventtracking.tracker.emit('edx.bookmark.listed', event_data)

        return page

    @apidocs.schema()
    def post(self, request, *unused_args, **unused_kwargs):  # lint-amnesty, pylint: disable=unused-argument
        """Create a new bookmark for a user.

        The POST request only needs to contain one parameter "usage_id".

        Http400 is returned if the format of the request is not correct,
        the usage_id is invalid or a block corresponding to the usage_id
        could not be found.

        **Example Requests**

        POST /api/bookmarks/v1/bookmarks/
        Request data: {"usage_id": <usage-id>}
        """
        if not request.data:
            return self.error_response(gettext_noop('No data provided.'), DEFAULT_USER_MESSAGE)

        usage_id = request.data.get('usage_id', None)
        if not usage_id:
            return self.error_response(gettext_noop('Parameter usage_id not provided.'), DEFAULT_USER_MESSAGE)

        try:
            usage_key = UsageKey.from_string(unquote_slashes(usage_id))
        except InvalidKeyError:
            error_message = gettext_noop('Invalid usage_id: {usage_id}.').format(usage_id=usage_id)
            log.error(error_message)
            return self.error_response(error_message, DEFAULT_USER_MESSAGE)

        try:
            bookmark = api.create_bookmark(user=self.request.user, usage_key=usage_key)
        except ItemNotFoundError:
            error_message = gettext_noop('Block with usage_id: {usage_id} not found.').format(usage_id=usage_id)
            log.error(error_message)
            return self.error_response(error_message, DEFAULT_USER_MESSAGE)
        except BookmarksLimitReachedError:
            error_message = gettext_noop(
                'You can create up to {max_num_bookmarks_per_course} bookmarks.'
                ' You must remove some bookmarks before you can add new ones.'
            ).format(max_num_bookmarks_per_course=settings.MAX_BOOKMARKS_PER_COURSE)
            log.info(
                'Attempted to create more than %s bookmarks',
                settings.MAX_BOOKMARKS_PER_COURSE
            )
            return self.error_response(error_message)

        return Response(bookmark, status=status.HTTP_201_CREATED)


class BookmarksDetailView(APIView, BookmarksViewMixin):
    """
    **Use Cases**

        Get or delete a specific bookmark for a user.

    **Example Requests**:

        GET /api/bookmarks/v1/bookmarks/{username},{usage_id}/?fields=display_name,path

        DELETE /api/bookmarks/v1/bookmarks/{username},{usage_id}/

    **Response for GET**

        Users can only delete their own bookmarks. If the bookmark_id does not belong
        to a requesting user's bookmark a Http404 is returned. Http404 will also be
        returned if the bookmark does not exist.

        * id: String. The identifier string for the bookmark: {user_id},{usage_id}.

        * course_id: String. The identifier string of the bookmark's course.

        * usage_id: String. The identifier string of the bookmark's XBlock.

        * display_name: (optional) String. Display name of the XBlock.

        * path: (optional) List of dicts containing {"usage_id": <usage-id>, display_name: <display-name>}
            for the XBlocks from the top of the course tree till the parent of the bookmarked XBlock.

        * created: ISO 8601 String. The timestamp of bookmark's creation.

    **Response for DELETE**

        Users can only delete their own bookmarks.

        A successful delete returns a 204 and no content.

        Users can only delete their own bookmarks. If the bookmark_id does not belong
        to a requesting user's bookmark a 404 is returned. 404 will also be returned
        if the bookmark does not exist.
    """

    authentication_classes = (BearerAuthentication, SessionAuthentication)
    permission_classes = (permissions.IsAuthenticated, IsUserInUrl)

    serializer_class = BookmarkSerializer

    def get_usage_key_or_error_response(self, usage_id):
        """
        Create and return usage_key or error Response.

        Arguments:
            usage_id (string): The id of required block.
        """
        try:
            return UsageKey.from_string(usage_id)
        except InvalidKeyError:
            error_message = gettext_noop('Invalid usage_id: {usage_id}.').format(usage_id=usage_id)
            log.error(error_message)
            return self.error_response(error_message, error_status=status.HTTP_404_NOT_FOUND)

    @apidocs.schema()
    def get(self, request, username=None, usage_id=None):  # lint-amnesty, pylint: disable=unused-argument
        """
        Get a specific bookmark for a user.

        **Example Requests**

        GET /api/bookmarks/v1/bookmarks/{username},{usage_id}?fields=display_name,path
        """
        usage_key_or_response = self.get_usage_key_or_error_response(usage_id=usage_id)

        if isinstance(usage_key_or_response, Response):
            return usage_key_or_response

        try:
            bookmark_data = api.get_bookmark(
                user=request.user,
                usage_key=usage_key_or_response,
                fields=self.fields_to_return(request.query_params)
            )
        except ObjectDoesNotExist:
            error_message = gettext_noop(
                'Bookmark with usage_id: {usage_id} does not exist.'
            ).format(usage_id=usage_id)
            log.error(error_message)
            return self.error_response(error_message, error_status=status.HTTP_404_NOT_FOUND)

        return Response(bookmark_data)

    def delete(self, request, username=None, usage_id=None):  # pylint: disable=unused-argument
        """
        DELETE /api/bookmarks/v1/bookmarks/{username},{usage_id}
        """
        usage_key_or_response = self.get_usage_key_or_error_response(usage_id=usage_id)

        if isinstance(usage_key_or_response, Response):
            return usage_key_or_response

        try:
            api.delete_bookmark(user=request.user, usage_key=usage_key_or_response)
        except ObjectDoesNotExist:
            error_message = gettext_noop(
                'Bookmark with usage_id: {usage_id} does not exist.'
            ).format(usage_id=usage_id)
            log.error(error_message)
            return self.error_response(error_message, error_status=status.HTTP_404_NOT_FOUND)

        return Response(status=status.HTTP_204_NO_CONTENT)
