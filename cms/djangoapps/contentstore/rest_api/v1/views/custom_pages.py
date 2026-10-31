"""API views for a course's custom pages and tabs."""

from django.core.exceptions import ImproperlyConfigured
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, extend_schema
from edx_rest_framework_extensions.auth.jwt.authentication import JwtAuthentication
from edx_rest_framework_extensions.auth.session.authentication import SessionAuthenticationAllowInactiveUser
from edx_rest_framework_extensions.errors import ErrorResponseSerializer
from edx_rest_framework_extensions.mixins import StandardizedErrorMixin
from edx_rest_framework_extensions.paginators import IterablePaginationMixin
from edx_rest_framework_extensions.url_converters import CourseKeyConverter
from rest_framework import status, viewsets
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.response import Response

from cms.djangoapps.contentstore.rest_api.pagination import DocumentedDefaultPagination
from cms.djangoapps.contentstore.rest_api.v1.custom_pages_service import (
    ORDER_NOT_SAVED_MESSAGE,
    REPEATED_CUSTOM_PAGE_MESSAGE,
    TAB_NOT_FOUND_MESSAGE,
    TAB_NOT_HIDEABLE_MESSAGE,
    UNKNOWN_CUSTOM_PAGE_MESSAGE,
    list_custom_pages,
    reorder_custom_pages,
    update_tab,
)
from cms.djangoapps.contentstore.rest_api.v1.serializers.custom_pages import (
    NO_TAB_CHANGE_MESSAGE,
    NOT_A_USAGE_KEY_MESSAGE,
    CourseTabSerializer,
    CourseTabUpdateSerializer,
    CustomPageListQuerySerializer,
    CustomPageOrderSerializer,
    CustomPageSerializer,
)
from cms.djangoapps.contentstore.views.permissions import (
    CanManagePagesAndResources,
    CanViewPagesAndResources,
    IsExistingCourse,
)

_COURSE_KEY_PARAMETER = OpenApiParameter(
    name="course_key",
    description="Key of the course, such as course-v1:edX+DemoX+2026.",
    location=OpenApiParameter.PATH,
    type=str,
    pattern=f"^{CourseKeyConverter.regex}$",
)

_COURSE_KEY_QUERY_PARAMETER = OpenApiParameter(
    name="course_key",
    description=CustomPageListQuerySerializer().fields["course_key"].help_text,
    location=OpenApiParameter.QUERY,
    required=True,
    type=str,
)

_TAB_ID_PARAMETER = OpenApiParameter(
    name="tab_id",
    description="Identifier of the tab within the course, such as progress, dates or wiki.",
    location=OpenApiParameter.PATH,
    type=str,
)


def _error(description):
    return OpenApiResponse(response=ErrorResponseSerializer, description=description)


_CUSTOM_PAGE = {
    "type": "static_tab",
    "title": None,
    "is_hideable": False,
    "is_hidden": False,
    "is_movable": True,
    "course_staff_only": False,
    "name": "Syllabus",
    "tab_id": "static_tab_4f1d7a2c9b3e4d5f8a6b7c8d9e0f1a2b",
    "settings": {"url_slug": "4f1d7a2c9b3e4d5f8a6b7c8d9e0f1a2b"},
    "id": "block-v1:edX+DemoX+2026+type@static_tab+block@4f1d7a2c9b3e4d5f8a6b7c8d9e0f1a2b",
}

_CUSTOM_PAGE_EXAMPLE = OpenApiExample(
    "Custom page",
    summary="One custom page of a course",
    response_only=True,
    status_codes=["200"],
    value=_CUSTOM_PAGE,
)

_ORDER_EXAMPLE = OpenApiExample(
    "New order",
    summary="Show the second custom page first",
    request_only=True,
    value={
        "custom_pages": [
            "block-v1:edX+DemoX+2026+type@static_tab+block@9e0f1a2b4f1d7a2c9b3e4d5f8a6b7c8d",
            "block-v1:edX+DemoX+2026+type@static_tab+block@4f1d7a2c9b3e4d5f8a6b7c8d9e0f1a2b",
        ],
    },
)

_HIDE_EXAMPLE = OpenApiExample(
    "Hide", summary="Hide the tab from learners", request_only=True, value={"is_hidden": True},
)

_TAB_EXAMPLE = OpenApiExample(
    "Progress tab",
    summary="The progress tab, hidden",
    response_only=True,
    status_codes=["200"],
    value={
        "type": "progress",
        "title": "Progress",
        "is_hideable": True,
        "is_hidden": True,
        "is_movable": False,
        "course_staff_only": False,
        "name": "Progress",
        "tab_id": "progress",
        "settings": {},
    },
)


class _CourseKeyFromQuery(BasePermission):
    """
    Read the course from the required ``course_key`` query parameter into the view's ``course_key`` argument.

    The course checks that follow read that argument, as they do on addresses
    that carry the course in the path. Listed after the authentication check,
    so an anonymous caller is refused (401) before the query is read, and
    before the course checks, so a missing or unparsable key is refused (400)
    before any course is looked up.
    """

    def has_permission(self, request, view):
        query = CustomPageListQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        view.kwargs["course_key"] = query.validated_data["course_key"]
        return True


@extend_schema(tags=["openedx-platform-sdk"])
class CourseCustomPagesViewSet(StandardizedErrorMixin, IterablePaginationMixin, viewsets.ViewSet):
    """
    The custom pages of courses: the tabs whose content course authors write,
    each addressed by its own usage key, listed per course in the order of
    the course's tabs.

    The course is named by the required ``course_key`` query parameter.
    Built-in tabs (progress, dates, wiki and the like) are not custom pages
    and are never listed; they are changed one at a time through
    ``CourseTabsViewSet``.

    Anyone who may view the course's pages and resources may list them. The
    course key is read first (400 when missing or invalid), then the course is
    looked up, so a signed-in caller learns that a course does not exist (404)
    before being refused (403). An action this viewset was not written for is
    refused with a server error instead of being served with these checks.

    A plain ``ViewSet``: the pages are entries of the course's tab list in the
    modulestore, not database rows.
    """

    # An inactive account's browser session stays authenticated, so the course permission decides: staff status
    # admits it, and its roles count only on courses where policy-based permissions are on.
    authentication_classes = (JwtAuthentication, SessionAuthenticationAllowInactiveUser)
    permission_classes = (IsAuthenticated, _CourseKeyFromQuery, IsExistingCourse, CanViewPagesAndResources)
    # The actions the permissions above were written for. A method with no
    # route (answered 405) has no action; OPTIONS is ``metadata``.
    checked_actions = (None, "list", "metadata")
    serializer_class = CustomPageSerializer
    pagination_class = DocumentedDefaultPagination

    def get_permissions(self):
        """Return the viewset's permissions, or refuse an action they were not written for."""
        if self.action not in self.checked_actions:
            raise ImproperlyConfigured(f"No permission is declared for the {self.action!r} action.")
        return super().get_permissions()

    def get_serializer(self, *args, **kwargs):
        """Instantiate the custom page serializer declared on this viewset."""
        return self.serializer_class(*args, **kwargs)

    @extend_schema(
        summary="List a course's custom pages",
        description=(
            "Returns one page of the custom pages of the course named by course_key, in the order learners "
            "see them among the course's tabs. Built-in tabs such as progress or wiki are not custom pages "
            "and are not listed. A missing course_key, or one that is not the key of a course, is refused "
            "with 400 and an error under course_key."
        ),
        parameters=[_COURSE_KEY_QUERY_PARAMETER],
        responses={
            200: OpenApiResponse(response=CustomPageSerializer, description="A page of the course's custom pages."),
            400: _error("course_key is missing or is not the key of a course."),
            401: _error("The requester is not authenticated."),
            403: _error("The requester may not view this course's pages and resources."),
            404: _error("The course does not exist, or the requested page is out of range."),
        },
        examples=[_CUSTOM_PAGE_EXAMPLE],
    )
    def list(self, request, **kwargs):
        """Return one page of the custom pages of the course named in the query."""
        return self.paginate_iterable(request, list_custom_pages(self.kwargs["course_key"], request.user))


@extend_schema(tags=["openedx-platform-sdk"])
class CourseCustomPageOrderViewSet(StandardizedErrorMixin, viewsets.ViewSet):
    """
    The order of one course's custom pages.

    A reorder puts the pages it lists first, in the order listed; pages it
    leaves out follow them in their current order.

    Anyone who may change the course's pages and resources may reorder them.
    The course is looked up first, so a signed-in caller learns that a course
    does not exist (404) before being refused (403).

    A plain ``ViewSet``: the order is that of the course's tab list in the
    modulestore, not of database rows.
    """

    # An inactive account's browser session stays authenticated, so the course permission decides: staff status
    # admits it, and its roles count only on courses where policy-based permissions are on.
    authentication_classes = (JwtAuthentication, SessionAuthenticationAllowInactiveUser)
    permission_classes = (IsAuthenticated, IsExistingCourse, CanManagePagesAndResources)

    @extend_schema(
        summary="Reorder a course's custom pages",
        description=(
            "Puts the course's custom pages in the order of their usage keys in custom_pages. Custom pages "
            "left out follow those listed, in their current order; built-in tabs keep their usual places. "
            "When a request is refused, nothing is changed. Besides a body that is not an object holding "
            "the list custom_pages, a request is refused with 400 and one of these messages: under "
            f"custom_pages, '{NOT_A_USAGE_KEY_MESSAGE}' when an item is not a string holding a usage key, "
            f"'{UNKNOWN_CUSTOM_PAGE_MESSAGE}' when an item names no custom page of this course, and "
            f"'{REPEATED_CUSTOM_PAGE_MESSAGE}' when a page is named twice; under non_field_errors, "
            f"'{ORDER_NOT_SAVED_MESSAGE}' when the course's tabs cannot be saved in the new order."
        ),
        parameters=[_COURSE_KEY_PARAMETER],
        request={"application/json": CustomPageOrderSerializer},
        responses={
            204: OpenApiResponse(description="The custom pages are in the new order."),
            400: _error("The body is invalid, names a page twice or one that is not a custom page of this "
                        "course, or the course's tabs cannot be saved in the new order."),
            401: _error("The requester is not authenticated."),
            403: _error("The requester may not change this course's pages and resources."),
            404: _error("The course does not exist."),
        },
        examples=[_ORDER_EXAMPLE],
    )
    def update(self, request, course_key):
        """Store the new order of the course's custom pages."""
        order = CustomPageOrderSerializer(data=request.data)
        order.is_valid(raise_exception=True)
        reorder_custom_pages(course_key, order.validated_data["custom_pages"], request.user)
        return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema(tags=["openedx-platform-sdk"])
class CourseTabsViewSet(StandardizedErrorMixin, viewsets.ViewSet):
    """
    The tabs of one course's navigation, each addressed by its ``tab_id``.

    A partial update shows or hides one tab from learners; a body without
    ``is_hidden`` is refused rather than accepted as no change. Only tabs whose
    ``is_hideable`` is true accept it: built-in tabs such as progress, dates
    and wiki do, custom pages do not.

    Anyone who may change the course's pages and resources may change a tab.
    The course is looked up first, so a signed-in caller learns that a course
    does not exist (404) before being refused (403).

    A plain ``ViewSet``: the tabs are entries of the course's tab list in the
    modulestore, not database rows.
    """

    # An inactive account's browser session stays authenticated, so the course permission decides: staff status
    # admits it, and its roles count only on courses where policy-based permissions are on.
    authentication_classes = (JwtAuthentication, SessionAuthenticationAllowInactiveUser)
    permission_classes = (IsAuthenticated, IsExistingCourse, CanManagePagesAndResources)
    serializer_class = CourseTabSerializer

    def get_serializer(self, *args, **kwargs):
        """Instantiate the tab serializer declared on this viewset."""
        return self.serializer_class(*args, **kwargs)

    @extend_schema(
        summary="Show or hide one of a course's tabs",
        description=(
            "Hides the tab from learners when is_hidden is true and shows it when false. A body without "
            f"is_hidden is refused under non_field_errors with the message '{NO_TAB_CHANGE_MESSAGE}'. "
            "Returns the tab as stored. A tab that may not be hidden or shown "
            f"refuses is_hidden with the message '{TAB_NOT_HIDEABLE_MESSAGE}'; a tab_id the course does not "
            f"have is answered with 404 and the message '{TAB_NOT_FOUND_MESSAGE}'. Custom pages are among "
            "the course's tabs, under tab_id static_tab_ followed by their url_slug, but cannot be hidden "
            "or shown here: is_hidden is refused for them."
        ),
        parameters=[_COURSE_KEY_PARAMETER, _TAB_ID_PARAMETER],
        request={"application/json": CourseTabUpdateSerializer},
        responses={
            200: OpenApiResponse(response=CourseTabSerializer, description="The tab, as stored."),
            400: _error("The body is invalid or lacks is_hidden, or the tab may not be hidden or shown."),
            401: _error("The requester is not authenticated."),
            403: _error("The requester may not change this course's pages and resources."),
            404: _error("The course does not exist, or has no tab with this identifier."),
        },
        examples=[_HIDE_EXAMPLE, _TAB_EXAMPLE],
    )
    def partial_update(self, request, course_key, tab_id):
        """Apply the change to the tab and return it as stored."""
        changes = CourseTabUpdateSerializer(data=request.data, partial=True)
        changes.is_valid(raise_exception=True)
        tab = update_tab(course_key, tab_id, changes.validated_data, request.user)
        return Response(self.get_serializer(tab).data)
