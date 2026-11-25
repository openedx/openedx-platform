"""API views for course PDF textbooks."""

from drf_spectacular.utils import OpenApiResponse, extend_schema
from edx_rest_framework_extensions.auth.jwt.authentication import JwtAuthentication
from edx_rest_framework_extensions.auth.session.authentication import SessionAuthenticationAllowInactiveUser
from edx_rest_framework_extensions.errors import ErrorResponseSerializer
from edx_rest_framework_extensions.mixins import StandardizedErrorMixin
from edx_rest_framework_extensions.paginators import DefaultPagination, IterablePaginationMixin
from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated

from cms.djangoapps.contentstore.rest_api.v2.serializers.textbooks import CourseTextbookSerializer
from cms.djangoapps.contentstore.rest_api.v2.textbooks_service import get_course_textbooks
from cms.djangoapps.contentstore.views.permissions import CanViewPagesAndResources


@extend_schema(tags=["openedx-platform-sdk"])
class CourseTextbooksViewSet(StandardizedErrorMixin, IterablePaginationMixin, viewsets.ViewSet):
    """
    The PDF textbooks of one course, in the order the course stores them.

    Callable by anyone allowed to view the course's pages and resources: on
    courses using policy-based authoring permissions that policy decides,
    elsewhere Studio read access to the course is required. Permission is
    checked before the course is looked up, so a caller without access is
    refused whether or not the course exists.

    A plain ``ViewSet``: textbooks are a settings field of the course in the
    modulestore, not database rows.
    """

    # Studio authors reach this endpoint from the authoring UI while their
    # account is still inactive, which the default session class rejects.
    authentication_classes = (JwtAuthentication, SessionAuthenticationAllowInactiveUser)
    permission_classes = (IsAuthenticated, CanViewPagesAndResources)
    serializer_class = CourseTextbookSerializer
    pagination_class = DefaultPagination

    def get_serializer(self, *args, **kwargs):
        """Instantiate the textbook serializer declared on this viewset."""
        return self.serializer_class(*args, **kwargs)

    @extend_schema(
        summary="List a course's PDF textbooks",
        description=(
            "Returns one page of the course's PDF textbooks, each with its chapters, in the "
            "order the course stores them, which is also the order of the course's textbook tabs."
        ),
        responses={
            200: OpenApiResponse(
                response=CourseTextbookSerializer,
                description="A page of the course's textbooks.",
            ),
            401: OpenApiResponse(
                response=ErrorResponseSerializer,
                description="The requester is not authenticated.",
            ),
            403: OpenApiResponse(
                response=ErrorResponseSerializer,
                description="The requester cannot view the pages and resources of this course.",
            ),
            404: OpenApiResponse(
                response=ErrorResponseSerializer,
                description="The course does not exist, or the requested page is out of range.",
            ),
        },
    )
    def list(self, request, course_key):
        """Return one page of the course's textbooks."""
        return self.paginate_iterable(request, get_course_textbooks(course_key))
