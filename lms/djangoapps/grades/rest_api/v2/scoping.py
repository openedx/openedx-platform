"""Record-visibility policies for the Grades API v2 list endpoints."""

from edx_rest_framework_extensions.auth.jwt.authentication import is_jwt_authenticated
from edx_rest_framework_extensions.auth.jwt.decoder import decode_jwt_filters, decode_jwt_is_restricted

CONTENT_ORG_FILTER = 'content_org'
USER_FILTER = 'user'
CURRENT_USER_FILTER_VALUE = 'me'


class CourseGradeScopingPolicy:
    """
    Narrows course-grade rows to the enrollments the caller is allowed to read.

    A token issued to a restricted application sees only rows whose course
    organization is named by one of the token's organization filters; a
    restricted token carrying no organization filter sees nothing. A user
    filter narrows those rows to one learner. As in the token checks the rest
    of the platform applies, only the first user filter counts, and it is
    compared with the learner's username in lower case, so a filter naming a
    username with capitals matches nobody. Global staff see every row. Every
    other caller sees their own enrollments only.

    The policy is built per request because the token filters it reads live on
    the request, not on the user.
    """

    def __init__(self, request):
        self.request = request

    def scope(self, queryset, subject):
        """Return ``queryset`` narrowed to the rows ``subject`` may see."""
        if self._is_restricted_token():
            return self._apply_token_filters(queryset)
        if subject.is_staff:
            return queryset
        return queryset.filter(user=subject)

    def _is_restricted_token(self):
        """Return True when the request was authenticated by a restricted application token."""
        return is_jwt_authenticated(self.request) and decode_jwt_is_restricted(self.request.auth)

    def _apply_token_filters(self, queryset):
        """Return ``queryset`` narrowed to the organizations and learner the token names."""
        token_filters = decode_jwt_filters(self.request.auth)
        organizations = [value for kind, value in token_filters if kind == CONTENT_ORG_FILTER]
        if not organizations:
            return queryset.none()
        queryset = queryset.filter(course__org__in=organizations)
        username = next((value for kind, value in token_filters if kind == USER_FILTER), None)
        if username is None:
            return queryset
        if username == CURRENT_USER_FILTER_VALUE:
            username = self.request.user.username.lower()
        if username != username.lower():
            return queryset.none()
        return queryset.filter(user__username__iexact=username)
