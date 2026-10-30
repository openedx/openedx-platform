"""API views for the course optimizer's link checks and previous-run link updates."""

from django.core.exceptions import ImproperlyConfigured
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, extend_schema
from edx_rest_framework_extensions.auth.jwt.authentication import JwtAuthentication
from edx_rest_framework_extensions.auth.session.authentication import SessionAuthenticationAllowInactiveUser
from edx_rest_framework_extensions.errors import ErrorResponseSerializer
from edx_rest_framework_extensions.mixins import StandardizedErrorMixin
from edx_rest_framework_extensions.shaping import project
from edx_rest_framework_extensions.url_converters import CourseKeyConverter
from rest_framework import status, viewsets
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from cms.djangoapps.contentstore.rest_api.v1.course_optimizer_service import (
    LINK_CHECK_ERROR_MESSAGE,
    LINK_NOT_UPDATED_MESSAGE,
    ensure_previous_run_link_updates_enabled,
    link_check_status,
    previous_run_link_update_status,
    start_link_check,
    start_previous_run_link_update,
)
from cms.djangoapps.contentstore.rest_api.v1.serializers.course_optimizer import (
    FULL_VIEW,
    MINIMAL_VIEW,
    LinkCheckQuerySerializer,
    LinkCheckSerializer,
    PreviousRunLinkUpdateQuerySerializer,
    PreviousRunLinkUpdateRequestSerializer,
    PreviousRunLinkUpdateSerializer,
    QueuedRunSerializer,
)
from cms.djangoapps.contentstore.views.permissions import CanEditCourseContent, CanStartLinkCheck, IsExistingCourse

_COURSE_KEY_PARAMETER = OpenApiParameter(
    name="course_key",
    description="Key of the course, such as course-v1:edX+DemoX+2026.",
    location=OpenApiParameter.PATH,
    type=str,
    pattern=f"^{CourseKeyConverter.regex}$",
)


def _view_parameter(query_serializer_class):
    return OpenApiParameter(
        name="view",
        description=query_serializer_class().fields["view"].help_text,
        required=False,
        location=OpenApiParameter.QUERY,
        type=str,
        enum=[MINIMAL_VIEW, FULL_VIEW],
        default=MINIMAL_VIEW,
    )


def _error(description):
    return OpenApiResponse(response=ErrorResponseSerializer, description=description)


_BLOCK_KEY = "block-v1:edX+DemoX+2026+type@{}+block@{}"
_REPORT_EXAMPLE = OpenApiExample(
    "Full report",
    summary="A link check that succeeded, read with view=full",
    response_only=True,
    status_codes=["200"],
    value={
        "status": "Succeeded",
        "created_at": "2026-10-25T14:32:01.294587Z",
        "error": None,
        "output": {
            "sections": [{
                "id": _BLOCK_KEY.format("chapter", "week1"),
                "display_name": "Week 1",
                "subsections": [{
                    "id": _BLOCK_KEY.format("sequential", "lesson"),
                    "display_name": "Lesson",
                    "units": [{
                        "id": _BLOCK_KEY.format("vertical", "unit"),
                        "display_name": "Unit",
                        "blocks": [{
                            "id": _BLOCK_KEY.format("html", "reading"),
                            "display_name": "Reading",
                            "url": "/course/course-v1:edX+DemoX+2026/editor/html/"
                                   + _BLOCK_KEY.format("html", "reading"),
                            "broken_links": ["https://example.com/missing"],
                            "locked_links": ["/static/handout.pdf"],
                            "external_forbidden_links": [],
                            "previous_run_links": [{
                                "original_link": "https://studio.example.com/course/course-v1:edX+DemoX+2025/info",
                                "is_updated": False,
                                "updated_link": None,
                            }],
                        }],
                    }],
                }],
            }],
            "course_updates": [],
            "custom_pages": [],
        },
    },
)

_QUEUED = OpenApiResponse(response=QueuedRunSerializer, description="The run was queued.")
_UNAUTHENTICATED = _error("The requester is not authenticated.")
_COURSE_NOT_FOUND = _error("The course does not exist.")


class _CourseOptimizerViewSet(StandardizedErrorMixin, viewsets.ViewSet):
    """
    Checks the course, then the permission the requested action needs.

    Subclasses restate the checks every request passes in
    ``permission_classes``, map each action to its own in
    ``action_permission_classes``, and name the members of the minimal view
    in ``minimal_fields``. An action with no permission declared is refused
    with a server error instead of being served unchecked.
    """

    # Kept from the legacy endpoints: an inactive account's browser session stays authenticated,
    # and the course permission decides whether it may call.
    authentication_classes = (JwtAuthentication, SessionAuthenticationAllowInactiveUser)
    permission_classes = (IsAuthenticated, IsExistingCourse)
    action_permission_classes = {}
    # A method with no route (answered 405) and OPTIONS read nothing of the
    # course's runs, so only the course is checked for them.
    unrouted_actions = (None, "metadata")

    def get_permissions(self):
        """Check the course, then the permission the requested action needs."""
        if self.action in self.unrouted_actions:
            return super().get_permissions()
        if self.action not in self.action_permission_classes:
            raise ImproperlyConfigured(f"No permission is declared for the {self.action!r} action.")
        specific = self.action_permission_classes[self.action]
        return [*super().get_permissions(), *(permission() for permission in specific)]

    def get_serializer(self, *args, **kwargs):
        """Instantiate the response serializer declared on this viewset."""
        return self.serializer_class(*args, **kwargs)

    @staticmethod
    def _full_view_requested(request, query_serializer_class):
        """Validate the query parameters and return whether ``view=full`` was requested."""
        query = query_serializer_class(data=request.query_params)
        query.is_valid(raise_exception=True)
        return query.validated_data["view"] == FULL_VIEW

    def _shaped(self, run, full):
        """Serialize ``run``, keeping only ``minimal_fields`` unless the full view was requested."""
        data = self.get_serializer(run).data
        return data if full else project(data, self.minimal_fields)


@extend_schema(tags=["openedx-platform-sdk"])
class CourseLinkCheckViewSet(_CourseOptimizerViewSet):
    """
    The latest check of the links in a course.

    Starting a check queues a run that follows every link in the course's
    content, updates, handouts and custom pages, and records those that are
    broken, refuse access, or point at the course's previous run. A read
    returns the latest run the requester may see: their own, or anyone's for
    a superuser. By default (``view=minimal``) only the state of the run is
    returned; ``view=full`` adds the links it flagged.

    Both need the permission to edit course content. Where policy-based
    authoring permissions are off, starting a check needs only Studio read
    access to the course while reading one needs write access; the two checks
    are kept separate on purpose. The course is looked up first, so a signed-in caller learns that a course does
    not exist (404) before being refused (403).

    A plain ``ViewSet``: runs are queued as tasks and read from their recorded
    status, not addressed as rows.
    """

    permission_classes = (IsAuthenticated, IsExistingCourse)
    action_permission_classes = {
        "create": (CanStartLinkCheck,),
        "retrieve": (CanEditCourseContent,),
    }
    serializer_class = LinkCheckSerializer
    minimal_fields = ("status", "created_at", "error")

    @extend_schema(
        summary="Start a check of a course's links",
        description=(
            "Queues a check of every link in the course and returns the state of the queued run. "
            "Read the result from this address."
        ),
        parameters=[_COURSE_KEY_PARAMETER],
        request=None,
        responses={
            202: _QUEUED,
            401: _UNAUTHENTICATED,
            403: _error("The requester may not check this course's links."),
            404: _COURSE_NOT_FOUND,
        },
    )
    def create(self, request, course_key):
        """Queue a link check of the course as the requester."""
        queued = start_link_check(course_key, request.user, request.LANGUAGE_CODE)
        return Response(QueuedRunSerializer(queued).data, status=status.HTTP_202_ACCEPTED)

    @extend_schema(
        summary="Get the latest check of a course's links",
        description=(
            "Returns the state of the latest link check of the course that the requester may see: their own "
            "runs, or anyone's for a superuser. The state is Uninitiated when there is none. The error member "
            f"of a run that failed is the fixed message '{LINK_CHECK_ERROR_MESSAGE}'. view=full adds the links "
            "the run flagged, for a run that succeeded."
        ),
        parameters=[_COURSE_KEY_PARAMETER, _view_parameter(LinkCheckQuerySerializer)],
        responses={
            200: OpenApiResponse(response=LinkCheckSerializer, description="The latest link check."),
            400: _error("A query parameter has an invalid value."),
            401: _UNAUTHENTICATED,
            403: _error("The requester may not read this course's link checks."),
            404: _COURSE_NOT_FOUND,
        },
        examples=[_REPORT_EXAMPLE],
    )
    def retrieve(self, request, course_key):
        """Return the latest link check the requester may see, in the requested view."""
        full = self._full_view_requested(request, LinkCheckQuerySerializer)
        return Response(self._shaped(link_check_status(request, course_key, full=full), full))


_UPDATE_ALL_EXAMPLE = OpenApiExample(
    "Every link",
    summary="Change every link to the previous run",
    request_only=True,
    value={"action": "all"},
)
_UPDATE_SINGLE_EXAMPLE = OpenApiExample(
    "Some links",
    summary="Change the links a link check reported",
    request_only=True,
    value={
        "action": "single",
        "data": [
            {
                "url": "https://studio.example.com/course/course-v1:edX+DemoX+2025/course",
                "type": "course_updates",
                "id": "1",
            },
        ],
    },
)


@extend_schema(tags=["openedx-platform-sdk"])
class CoursePreviousRunLinkUpdateViewSet(_CourseOptimizerViewSet):
    """
    The latest update of a course's links that point at its previous run.

    Starting an update queues a run that changes such links to point at this
    run: every one the course holds (``action`` ``all``) or those listed
    (``single``). A read returns the latest run the requester may see, as for
    link checks. By default (``view=minimal``) only the state of the run is
    returned; ``view=full`` adds the outcome of each link.

    Both need the permission to edit course content (Studio write access
    where policy-based authoring permissions are off). Where updating links
    to a previous run is not enabled for the course, both answer 404 once the
    caller's permission is established.

    A plain ``ViewSet``, as for link checks.
    """

    permission_classes = (IsAuthenticated, IsExistingCourse)
    action_permission_classes = {
        "create": (CanEditCourseContent,),
        "retrieve": (CanEditCourseContent,),
    }
    serializer_class = PreviousRunLinkUpdateSerializer
    minimal_fields = ("status",)

    @extend_schema(
        summary="Start an update of a course's links to its previous run",
        description=(
            "Queues a run that changes links to the course's previous run so that they point at this run. "
            "With action 'all' every such link is changed; with 'single', the links in data. Returns the "
            "state of the queued run; read the result from this address."
        ),
        parameters=[_COURSE_KEY_PARAMETER],
        request={"application/json": PreviousRunLinkUpdateRequestSerializer},
        responses={
            202: _QUEUED,
            400: _error("The body names no valid action, or names 'single' without links."),
            401: _UNAUTHENTICATED,
            403: _error("The requester may not change this course's content."),
            404: _error("The course does not exist, or updating links to its previous run is not enabled."),
        },
        examples=[_UPDATE_ALL_EXAMPLE, _UPDATE_SINGLE_EXAMPLE],
    )
    def create(self, request, course_key):
        """Queue an update of the course's links to its previous run as the requester."""
        ensure_previous_run_link_updates_enabled(course_key)
        body = PreviousRunLinkUpdateRequestSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        queued = start_previous_run_link_update(
            course_key,
            request.user,
            body.validated_data["action"],
            body.validated_data.get("data", []),
            request.LANGUAGE_CODE,
        )
        return Response(QueuedRunSerializer(queued).data, status=status.HTTP_202_ACCEPTED)

    @extend_schema(
        summary="Get the latest update of a course's links to its previous run",
        description=(
            "Returns the state of the latest such update of the course that the requester may see: their own "
            "runs, or anyone's for a superuser. The state is Uninitiated when there is none. view=full adds "
            "the outcome of each link of a run that succeeded; a link that could not be changed carries the "
            f"fixed message '{LINK_NOT_UPDATED_MESSAGE}'."
        ),
        parameters=[_COURSE_KEY_PARAMETER, _view_parameter(PreviousRunLinkUpdateQuerySerializer)],
        responses={
            200: OpenApiResponse(response=PreviousRunLinkUpdateSerializer, description="The latest update."),
            400: _error("A query parameter has an invalid value."),
            401: _UNAUTHENTICATED,
            403: _error("The requester may not read this course's link updates."),
            404: _error("The course does not exist, or updating links to its previous run is not enabled."),
        },
    )
    def retrieve(self, request, course_key):
        """Return the latest update the requester may see, in the requested view."""
        ensure_previous_run_link_updates_enabled(course_key)
        full = self._full_view_requested(request, PreviousRunLinkUpdateQuerySerializer)
        return Response(self._shaped(previous_run_link_update_status(request, course_key, full=full), full))
