"""API views for a course's certificate settings."""

from drf_spectacular.utils import OpenApiResponse, extend_schema
from edx_rest_framework_extensions.errors import ErrorResponseSerializer
from edx_rest_framework_extensions.mixins import StandardizedErrorMixin
from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from cms.djangoapps.contentstore.rest_api.v2.certificates_service import get_certificate_settings
from cms.djangoapps.contentstore.rest_api.v2.serializers.certificates import CertificateSettingsSerializer
from cms.djangoapps.contentstore.views.permissions import CanViewCertificates


@extend_schema(tags=["openedx-platform-sdk"])
class CourseCertificateSettingsViewSet(StandardizedErrorMixin, viewsets.ViewSet):
    """
    The certificate configuration of one course.

    Returns the certificates configured for the course together with the
    context Studio needs to present them: the course's certificate-awarding
    modes, whether the certificates are active, and whether the requester may
    manage them.

    Callable by anyone who may view the course's certificate configuration.
    Permission is checked before the course is looked up, so a caller without
    access is refused whether or not the course exists.

    A plain ``ViewSet``: the data comes from the modulestore, not from database
    rows.
    """

    permission_classes = (IsAuthenticated, CanViewCertificates)
    serializer_class = CertificateSettingsSerializer

    def get_serializer(self, *args, **kwargs):
        """Instantiate the settings serializer declared on this viewset."""
        return self.serializer_class(*args, **kwargs)

    @extend_schema(
        summary="Retrieve a course's certificate settings",
        description=(
            "Returns the certificates configured for the course, their signatories, the enrollment "
            "modes that can award a certificate, whether the certificates are active, and whether "
            "the requester may manage them."
        ),
        responses={
            200: OpenApiResponse(
                response=CertificateSettingsSerializer,
                description="The course's certificate settings.",
            ),
            401: OpenApiResponse(
                response=ErrorResponseSerializer,
                description="The requester is not authenticated.",
            ),
            403: OpenApiResponse(
                response=ErrorResponseSerializer,
                description="The requester may not view this course's certificate settings.",
            ),
            404: OpenApiResponse(
                response=ErrorResponseSerializer,
                description="The course does not exist.",
            ),
        },
    )
    def retrieve(self, request, course_key):
        """Return the course's certificate settings."""
        certificate_settings = get_certificate_settings(course_key, request.user)
        return Response(self.get_serializer(certificate_settings).data)
