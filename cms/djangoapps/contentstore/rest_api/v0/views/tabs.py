""" API Views for course tabs """

from django.utils.translation import gettext_lazy as _
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from opaque_keys.edx.keys import CourseKey
from openedx_authz.constants.permissions import (
    COURSES_MANAGE_PAGES_AND_RESOURCES,
    COURSES_VIEW_PAGES_AND_RESOURCES,
)
from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from openedx.core.djangoapps.authz.constants import LegacyAuthoringPermission
from openedx.core.djangoapps.authz.decorators import user_has_course_permission
from openedx.core.lib.api.view_utils import DeveloperErrorViewMixin, verify_course_exists, view_auth_classes
from xmodule.modulestore.django import modulestore
from xmodule.modulestore.exceptions import ItemNotFoundError

from ....views.tabs import edit_tab_handler, get_course_tabs, reorder_tabs_handler
from ..serializers import CourseTabSerializer, CourseTabUpdateSerializer, TabIDLocatorSerializer


@view_auth_classes(is_authenticated=True)
class CourseTabListView(DeveloperErrorViewMixin, APIView):
    """
    API view to list course tabs.
    """

    @extend_schema(
        parameters=[OpenApiParameter("course_id", OpenApiTypes.STR, OpenApiParameter.PATH, description="Course ID")],
        responses={
            200: CourseTabSerializer,
            401: OpenApiResponse(description="The requester is not authenticated."),
            403: OpenApiResponse(description="The requester cannot access the specified course."),
            404: OpenApiResponse(description="The requested course does not exist."),
        },
    )
    @verify_course_exists()
    def get(self, request: Request, course_id: str) -> Response:
        """
        Get a list of all the tabs in a course including hidden tabs.

        **Example Request**

            GET /api/contentstore/v0/tabs/{course_id}

        **Response Values**

        If the request is successful, an HTTP 200 "OK" response is returned.

        The HTTP 200 response contains a list of objects that contain info
        about each tab.

        **Example Response**

        ```json
        [
            {
                "course_staff_only": false,
                "is_hidden": false,
                "is_hideable": false,
                "is_movable": false,
                "name": "Home",
                "settings": {},
                "tab_id": "info",
                "title": "Home",
                "type": "course_info"
            },
            {
                "course_staff_only": false,
                "is_hidden": false,
                "is_hideable": false,
                "is_movable": false,
                "name": "Course",
                "settings": {},
                "tab_id": "courseware",
                "title": "Course",
                "type": "courseware"
            },
            ...
        }
        ```
        """
        course_key = CourseKey.from_string(course_id)
        if not user_has_course_permission(
            request.user,
            COURSES_VIEW_PAGES_AND_RESOURCES.identifier,
            course_key,
            LegacyAuthoringPermission.READ,
        ):
            self.permission_denied(request)

        course_block = modulestore().get_course(course_key)
        tabs_to_render = get_course_tabs(course_block, request.user)
        serializedCourseTabs = CourseTabSerializer(tabs_to_render, many=True).data
        json_tabs = []
        for tab in serializedCourseTabs:
            if tab.get('type') == 'static_tab':
                url_slug = tab.get('settings').get('url_slug')
                static_tab_loc = course_block.id.make_usage_key("static_tab", url_slug)
                tab["id"] = str(static_tab_loc)
                json_tabs.append(tab)
        return Response(json_tabs)


@view_auth_classes(is_authenticated=True)
class CourseTabSettingsView(DeveloperErrorViewMixin, APIView):
    """
    API view for course tabs settings.
    """

    def handle_exception(self, exc):
        """Handle NotImplementedError and return a proper response for it."""
        if isinstance(exc, NotImplementedError):
            return self._make_error_response(400, str(exc))
        if isinstance(exc, ItemNotFoundError):
            return self._make_error_response(400, str(exc))
        return super().handle_exception(exc)

    @extend_schema(
        request=CourseTabUpdateSerializer(help_text=_("Change the visibility of tabs in a course.")),
        parameters=[
            OpenApiParameter("course_id", OpenApiTypes.STR, OpenApiParameter.PATH, description="Course ID"),
            OpenApiParameter("tab_id", OpenApiTypes.STR, OpenApiParameter.QUERY, description="Tab ID"),
            OpenApiParameter("tab_location", OpenApiTypes.STR, OpenApiParameter.QUERY, description="Tab usage key"),
        ],
        responses={
            204: OpenApiResponse(description="In case of success, a 204 is returned with no content."),
            401: OpenApiResponse(description="The requester is not authenticated."),
            403: OpenApiResponse(description="The requester cannot access the specified course."),
            404: OpenApiResponse(description="The requested course does not exist."),
        },
    )
    @verify_course_exists()
    def post(self, request: Request, course_id: str) -> Response:
        """
        Change visibility of tabs in a course.

        **Example Requests**

        You can provide either a tab_id or a tab_location.

        Hide a course tab using ``tab_id``:

            POST /api/contentstore/v0/tabs/{course_id}/settings/?tab_id={tab_id} {
                "is_hidden": true
            }

        Hide a course tab using ``tab_location``

            POST /api/contentstore/v0/tabs/{course_id}/settings/?tab_location={tab_location} {
                "is_hidden": true
            }

        **Response Values**

        If the request is successful, an HTTP 204 response is returned
        without any content.
        """
        course_key = CourseKey.from_string(course_id)
        if not user_has_course_permission(
            request.user,
            COURSES_MANAGE_PAGES_AND_RESOURCES.identifier,
            course_key,
            LegacyAuthoringPermission.WRITE,
        ):
            self.permission_denied(request)

        tab_id_locator = TabIDLocatorSerializer(data=request.query_params)
        tab_id_locator.is_valid(raise_exception=True)

        course_block = modulestore().get_course(course_key)
        serializer = CourseTabUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        edit_tab_handler(
            course_block,
            {
                "tab_id_locator": tab_id_locator.data,
                **serializer.data,
            },
            request.user,
        )
        return Response(status=status.HTTP_204_NO_CONTENT)


@view_auth_classes(is_authenticated=True)
class CourseTabReorderView(DeveloperErrorViewMixin, APIView):
    """
    API view for reordering course tabs.
    """

    def handle_exception(self, exc: Exception) -> Response:
        """
        Handle NotImplementedError and return a proper response for it.
        """
        if isinstance(exc, NotImplementedError):
            return self._make_error_response(400, str(exc))
        return super().handle_exception(exc)

    @extend_schema(
        request=TabIDLocatorSerializer(many=True),
        parameters=[
            OpenApiParameter("course_id", OpenApiTypes.STR, OpenApiParameter.PATH, description="Course ID"),
        ],
        responses={
            204: OpenApiResponse(description="In case of success, a 204 is returned with no content."),
            401: OpenApiResponse(description="The requester is not authenticated."),
            403: OpenApiResponse(description="The requester cannot access the specified course."),
            404: OpenApiResponse(description="The requested course does not exist."),
        },
    )
    @verify_course_exists()
    def post(self, request: Request, course_id: str) -> Response:
        """
        Reorder tabs in a course.

        **Example Requests**

        Move course tabs:

            POST /api/contentstore/v0/tabs/{course_id}/reorder [
                {
                    "tab_locator": "block-v1:TstX+DemoX+Demo+type@static_tab+block@d26fcb0e93824fbfa5c9e5f100e2511a"
                },
                {
                    "tab_locator": "block-v1:TstX+DemoX+Demo+type@static_tab+block@a011f1bd05af4578ae397ed8cabccf62"
                },
            ]


        **Response Values**

        If the request is successful, an HTTP 204 response is returned
        without any content.
        """
        course_key = CourseKey.from_string(course_id)
        if not user_has_course_permission(
            request.user,
            COURSES_MANAGE_PAGES_AND_RESOURCES.identifier,
            course_key,
            LegacyAuthoringPermission.WRITE,
        ):
            self.permission_denied(request)

        course_block = modulestore().get_course(course_key)
        tab_id_locators = TabIDLocatorSerializer(data=request.data, many=True)
        tab_id_locators.is_valid(raise_exception=True)
        reorder_tabs_handler(
            course_block,
            tab_id_locators.validated_data,
            request.user,
        )
        return Response(status=status.HTTP_204_NO_CONTENT)
