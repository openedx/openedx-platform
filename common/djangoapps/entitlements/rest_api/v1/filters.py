"""
Filters for the Entitlements API.
"""

from django_filters import rest_framework as filters

from common.djangoapps.entitlements.models import CourseEntitlement


class CharListFilter(filters.CharFilter):
    """ Filters a field via a comma-delimited list of values. """

    def filter(self, qs, value):
        if value not in (None, ''):
            value = value.split(',')

        return super().filter(qs, value)


class UUIDListFilter(CharListFilter):
    """ Filters a field via a comma-delimited list of UUIDs. """

    def __init__(self, field_name='uuid', label=None, widget=None, method=None, lookup_expr='in', required=False,
                 distinct=False, exclude=False, **kwargs):
        super().__init__(
            field_name=field_name,
            label=label,
            widget=widget,
            method=method,
            lookup_expr=lookup_expr,
            required=required,
            distinct=distinct,
            exclude=exclude,
            **kwargs
        )


class CourseEntitlementFilter(filters.FilterSet):
    """Filter for CourseEntitlements"""

    uuid = UUIDListFilter()
    user = filters.CharFilter(field_name='user__username')
    course_uuid = UUIDListFilter(field_name='course_uuid')
    expired_at__isnull = filters.BooleanFilter(field_name='expired_at', lookup_expr='isnull')

    class Meta:
        model = CourseEntitlement
        fields = ('uuid', 'user', 'course_uuid', 'order_number')
