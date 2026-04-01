"""
Filters for the Instructor API v2.
"""

from django.db.models import Q
from django_filters import rest_framework as filters
from opaque_keys.edx.keys import CourseKey

from common.djangoapps.student.models import CourseEnrollment
from common.djangoapps.student.roles import CourseBetaTesterRole


class CourseEnrollmentFilter(filters.FilterSet):
    """
    FilterSet for filtering course enrollments.

    Supports filtering by:
    - search: case-insensitive partial match on username, email, first name, or last name
    - is_beta_tester: filter by beta tester role membership
    """
    search = filters.CharFilter(method='filter_search', label='Search')
    is_beta_tester = filters.BooleanFilter(method='filter_is_beta_tester', label='Is Beta Tester')

    class Meta:
        model = CourseEnrollment
        fields = ['search', 'is_beta_tester']

    def _get_course_key(self):
        """Extract the course key from the view's URL kwargs."""
        return CourseKey.from_string(self.request.resolver_match.kwargs['course_id'])

    def filter_search(self, queryset, name, value):
        """Filter enrollments by username, email, first name, or last name."""
        if not value:
            return queryset
        return queryset.filter(
            Q(user__username__icontains=value)
            | Q(user__email__icontains=value)
            | Q(user__first_name__icontains=value)
            | Q(user__last_name__icontains=value)
        )

    def filter_is_beta_tester(self, queryset, name, value):
        """Filter enrollments by beta tester role membership."""
        course_key = self._get_course_key()
        beta_tester_ids = set(
            CourseBetaTesterRole(course_key).users_with_role().values_list('id', flat=True)
        )
        if value:
            return queryset.filter(user__id__in=beta_tester_ids)
        return queryset.exclude(user__id__in=beta_tester_ids)
