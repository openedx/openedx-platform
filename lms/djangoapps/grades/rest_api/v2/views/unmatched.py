"""Catch-all view for addresses under the Grades API v2 collections that match no route."""

from drf_spectacular.utils import extend_schema
from edx_rest_framework_extensions.mixins import StandardizedErrorMixin
from rest_framework.exceptions import NotFound
from rest_framework.permissions import AllowAny
from rest_framework.views import APIView


@extend_schema(exclude=True)
class UnmatchedRouteView(StandardizedErrorMixin, APIView):
    """
    Answers every request with a JSON not-found error.

    The key converters reject a malformed or retired course or usage key by
    not matching the route at all, which would otherwise reach the site's
    HTML 404 page. This view is mounted after the real routes of each
    collection so those addresses get the same error body as the rest of the
    API. It refuses before authentication and permission checks run, so the
    answer is the same whoever asks and whatever the method.
    """

    permission_classes = (AllowAny,)

    def initial(self, request, *args, **kwargs):
        """Refuse the request before any handler runs."""
        self.format_kwarg = self.get_format_suffix(**kwargs)  # pylint: disable=attribute-defined-outside-init
        raise NotFound()
