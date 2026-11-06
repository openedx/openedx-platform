"""API view for the Studio documentation links."""

from drf_spectacular.utils import OpenApiResponse, extend_schema
from edx_rest_framework_extensions.auth.jwt.authentication import JwtAuthentication
from edx_rest_framework_extensions.auth.session.authentication import SessionAuthenticationAllowInactiveUser
from edx_rest_framework_extensions.errors import ErrorResponseSerializer
from edx_rest_framework_extensions.mixins import StandardizedErrorMixin
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from cms.djangoapps.contentstore.rest_api.v2.serializers.help_urls import HelpUrlsSerializer
from cms.djangoapps.contentstore.utils import get_help_urls


@extend_schema(tags=["openedx-platform-sdk"])
class HelpUrlsView(StandardizedErrorMixin, APIView):
    """
    The documentation link of each Studio page, keyed by help token.

    The links come from the deployment's help token configuration and are the
    same for every caller. Callable by any authenticated user.

    A plain ``APIView``: the response is one object with no members to
    address, so a viewset ``list`` would publish it as an array.
    """

    # Studio users reach the help links from the authoring UI while their
    # account is still inactive, which the default session class rejects.
    authentication_classes = (JwtAuthentication, SessionAuthenticationAllowInactiveUser)
    permission_classes = (IsAuthenticated,)

    @extend_schema(
        summary="Retrieve the Studio documentation links",
        description=(
            "Returns the documentation link of each Studio page, keyed by help token. "
            "The links are the same for every caller; they depend only on the deployment's "
            "documentation settings."
        ),
        responses={
            200: OpenApiResponse(
                response=HelpUrlsSerializer,
                description="The documentation link of each Studio page.",
            ),
            401: OpenApiResponse(
                response=ErrorResponseSerializer,
                description="The requester is not authenticated.",
            ),
        },
    )
    def get(self, request: Request) -> Response:
        """Return the documentation link of each Studio page."""
        return Response(HelpUrlsSerializer(get_help_urls()).data)
