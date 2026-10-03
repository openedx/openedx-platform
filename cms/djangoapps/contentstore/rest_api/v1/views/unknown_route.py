"""API view answering addresses that no endpoint serves."""

from drf_spectacular.utils import extend_schema
from edx_rest_framework_extensions.mixins import StandardizedErrorMixin
from rest_framework.exceptions import NotFound
from rest_framework.permissions import AllowAny
from rest_framework.views import APIView


@extend_schema(exclude=True)
class UnknownRouteView(StandardizedErrorMixin, APIView):
    """
    Answer a video address whose course key the conforming routes refuse.

    A key that is malformed or in the deprecated slash-separated form fails to
    resolve, and would otherwise be answered by the site's HTML error page, which
    an API client cannot read. Nothing here needs the caller's identity, so the
    answer is the same for everyone: the address holds no resource.
    """

    permission_classes = (AllowAny,)

    def initial(self, request, *args, **kwargs):
        """Refuse the request before any handler runs; the address holds nothing."""
        raise NotFound()
