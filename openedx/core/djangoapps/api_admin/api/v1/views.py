"""
API Views.
"""

from django_filters.rest_framework import DjangoFilterBackend
from edx_rest_framework_extensions.auth.jwt.authentication import JwtAuthentication
from rest_framework.authentication import SessionAuthentication
from rest_framework.generics import ListAPIView
from rest_framework.permissions import IsAuthenticated

from openedx.core.djangoapps.api_admin.api.filters import IsOwnerOrStaffFilterBackend
from openedx.core.djangoapps.api_admin.api.v1 import serializers as api_access_serializers
from openedx.core.djangoapps.api_admin.models import ApiAccessRequest
from openedx.core.lib.api.authentication import BearerAuthentication


class ApiAccessRequestView(ListAPIView):
    """
    Return `API Access Requests` in the form of a paginated list.

    Raises:
        NotFound: Raised if user with `username` provided in `GET` parameters does not exist.
        PermissionDenied: Raised if `username` is provided in `GET` parameters but the requesting
            user does not have access rights to filter results.

    Example:
        `GET: /api-admin/api/v1/api_access_request/`
        {
            "count": 1,
            "num_pages": 1,
            "current_page": 1,
            "results": [
                {
                    "id": 1,
                    "created": "2017-09-25T08:41:48.934364Z",
                    "modified": "2017-09-25T08:42:04.185209Z",
                    "user": 6,
                    "status": "denied",
                    "website": "https://www.example.com/",
                    "reason": "Example",
                    "company_name": "Example Name",
                    "company_address": "Silicon Valley",
                    "site": 1,
                    "contacted": true
                }
            ],
            "next": null,
            "start": 0,
            "previous": null
        }
    """
    authentication_classes = (JwtAuthentication, BearerAuthentication, SessionAuthentication,)
    permission_classes = (IsAuthenticated, )
    serializer_class = api_access_serializers.ApiAccessRequestSerializer
    filter_backends = (IsOwnerOrStaffFilterBackend, DjangoFilterBackend)

    queryset = ApiAccessRequest.objects.all()

    filterset_fields = ('user__username', 'status', 'company_name', 'site__domain', 'contacted')
