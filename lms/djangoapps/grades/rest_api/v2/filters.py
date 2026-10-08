"""Filter sets for the Grades API v2 list endpoints."""

from django import forms
from django.db.models import Case, Exists, F, OuterRef, Q, When
from django_filters import rest_framework as filters
from opaque_keys import InvalidKeyError
from opaque_keys.edx.keys import CourseKey, UsageKey

from common.djangoapps.student.models import CourseAccessRole, CourseEnrollment
from lms.djangoapps.grades.models import (
    PersistentCourseGrade,
    PersistentSubsectionGrade,
    PersistentSubsectionGradeOverride,
)
from openedx.core.djangoapps.course_groups.models import CourseUserGroup

#: Most learners one request may name in the ``username`` filter.
MAX_USERNAMES = 100

#: Value of ``excluded_course_roles`` that leaves out learners holding any role.
ALL_COURSE_ROLES = 'all'


class CharInFilter(filters.BaseInFilter, filters.CharFilter):
    """Matches any of a comma-separated list of values."""


class StableOrderingFilter(filters.OrderingFilter):
    """An ordering filter that breaks ties on the primary key, so pages never overlap."""

    def filter(self, qs, value):
        """Order by the requested fields, then by id."""
        qs = super().filter(qs, value)
        if value:
            qs = qs.order_by(*qs.query.order_by, 'id')
        return qs


class HistoryOrderingFilter(filters.OrderingFilter):
    """An ordering filter for history records that breaks ties on the record id, in the same direction."""

    def filter(self, qs, value):
        """Order by the requested fields, then by history id, descending when the last field is."""
        qs = super().filter(qs, value)
        if value:
            last = qs.query.order_by[-1]
            qs = qs.order_by(*qs.query.order_by, '-history_id' if last.startswith('-') else 'history_id')
        return qs


def parse_course_key(value):
    """Return the CourseKey ``value`` names, or raise a form validation error."""
    try:
        course_key = CourseKey.from_string(value)
    except InvalidKeyError as exc:
        raise forms.ValidationError('Not a valid course key.', code='invalid') from exc
    if course_key.deprecated:
        raise forms.ValidationError('Not a valid course key.', code='invalid')
    return course_key


class CourseKeyField(forms.CharField):
    """A form field that accepts a course key and cleans it to a CourseKey."""

    def clean(self, value):
        """Return the parsed key, or None when no value was sent."""
        value = super().clean(value)
        if not value:
            return None
        return parse_course_key(value)


class CourseKeyFilter(filters.Filter):
    """Narrows to the rows of one course, named by its course key."""

    field_class = CourseKeyField


class UsageKeyField(forms.CharField):
    """A form field that accepts a usage key and cleans it to a UsageKey."""

    def clean(self, value):
        """Return the parsed key, or None when no value was sent."""
        value = super().clean(value)
        if not value:
            return None
        try:
            usage_key = UsageKey.from_string(value)
        except InvalidKeyError as exc:
            raise forms.ValidationError('Not a valid usage key.', code='invalid') from exc
        if usage_key.deprecated:
            raise forms.ValidationError('Not a valid usage key.', code='invalid')
        return usage_key


class UsageKeyFilter(filters.CharFilter):
    """A filter whose value is a usage key."""

    field_class = UsageKeyField


class IntegerFilter(filters.NumberFilter):
    """A filter whose value is a whole number."""

    field_class = forms.IntegerField


class RepeatedCharField(forms.Field):
    """A form field that collects every value of a query parameter sent more than once."""

    widget = forms.MultipleHiddenInput

    def to_python(self, value):
        """Return the non-empty values sent, as a list of strings."""
        return [str(item) for item in value or () if item]


class RepeatedCharFilter(filters.MultipleChoiceFilter):
    """A filter whose parameter may be repeated, each value free text rather than one of fixed choices."""

    field_class = RepeatedCharField


class CourseGradeFilterForm(forms.Form):
    """Validates the course-grade filters that need more than a per-field check."""

    def clean_username(self):
        """Refuse more usernames than one request may name."""
        usernames = self.cleaned_data.get('username')
        if usernames and len(usernames) > MAX_USERNAMES:
            raise forms.ValidationError(
                f'Name at most {MAX_USERNAMES} usernames.', code='max_usernames',
            )
        return usernames


class CourseGradeFilterSet(filters.FilterSet):
    """Narrows a course-grade list to a course and a set of learners, and orders it."""

    course_key = CourseKeyFilter(
        field_name='course_id',
        help_text='Return only grades in the course with this key.',
    )
    username = CharInFilter(
        field_name='user__username',
        lookup_expr='in',
        help_text=(
            'Return only the grades of these learners: a comma-separated list of '
            f'at most {MAX_USERNAMES} usernames.'
        ),
    )
    ordering = StableOrderingFilter(
        fields=(
            ('user__username', 'username'),
            ('created', 'created'),
            ('id', 'id'),
        ),
        help_text='Order the results by username, created or id. Prefix with "-" to reverse. Defaults to id.',
    )

    class Meta:
        model = CourseEnrollment
        fields = []
        form = CourseGradeFilterForm


class GradebookEntryFilterSet(filters.FilterSet):
    """
    Narrows the gradebook of one course to the learners a caller asked for.

    The queryset is already limited to the course, so every filter only narrows
    it. The assignment and course-grade bounds qualify one another, so they are
    applied together after the single-value filters rather than one by one.
    """

    user_contains = filters.CharFilter(
        method='filter_user_contains',
        help_text='Return learners whose username, email address or program key contains this text.',
    )
    username_contains = filters.CharFilter(
        field_name='user__username',
        lookup_expr='icontains',
        help_text='Return learners whose username contains this text.',
    )
    cohort_id = IntegerFilter(
        method='filter_cohort_id',
        help_text='Return learners in the cohort of this course with this id.',
    )
    enrollment_mode = filters.CharFilter(
        field_name='mode',
        help_text="Return learners enrolled in this mode, for example 'masters'.",
    )
    excluded_course_roles = RepeatedCharFilter(
        method='filter_excluded_course_roles',
        help_text=(
            "Leave out learners who hold this role in the course. Repeat the parameter for several "
            f"roles, or send '{ALL_COURSE_ROLES}' to leave out every learner who holds any role."
        ),
    )
    assignment_usage_key = UsageKeyFilter(
        method='filter_applied_together',
        help_text='Key of the subsection that assignment_grade_min and assignment_grade_max apply to.',
    )
    assignment_grade_min = filters.NumberFilter(
        method='filter_applied_together',
        help_text='Lowest percentage, from 0 to 100, scored on the subsection named by assignment_usage_key.',
    )
    assignment_grade_max = filters.NumberFilter(
        method='filter_applied_together',
        help_text='Highest percentage, from 0 to 100, scored on the subsection named by assignment_usage_key.',
    )
    course_grade_min = filters.NumberFilter(
        method='filter_applied_together',
        help_text=(
            'Lowest course percentage, from 0 to 100. A bound of 0, or no bound, '
            'also returns learners who have no stored course grade yet.'
        ),
    )
    course_grade_max = filters.NumberFilter(
        method='filter_applied_together',
        help_text='Highest course percentage, from 0 to 100.',
    )
    ordering = StableOrderingFilter(
        fields=(
            ('user__username', 'username'),
            ('created', 'created'),
            ('id', 'id'),
        ),
        help_text='Order the results by username, created or id. Prefix with "-" to reverse. Defaults to id.',
    )

    class Meta:
        model = CourseEnrollment
        fields = []

    def filter_queryset(self, queryset):
        """Apply the single-value filters, then the bounds that qualify each other."""
        queryset = super().filter_queryset(queryset)
        queryset = self._filter_by_assignment_grade(queryset)
        return self._filter_by_course_grade(queryset)

    def filter_applied_together(self, queryset, name, value):  # pylint: disable=unused-argument
        """Leave the queryset alone; ``filter_queryset`` applies these values together."""
        return queryset

    def filter_user_contains(self, queryset, name, value):  # pylint: disable=unused-argument
        """Narrow to learners whose username, email address or program key contains the text."""
        return queryset.filter(
            Q(user__username__icontains=value)
            | Q(programcourseenrollment__program_enrollment__external_user_key__icontains=value)
            | Q(user__email__icontains=value)
        )

    def filter_cohort_id(self, queryset, name, value: int):  # pylint: disable=unused-argument
        """Narrow to learners in the given cohort of the enrollment's own course."""
        return queryset.filter(
            user__course_groups__id=value,
            user__course_groups__course_id=F('course_id'),
            user__course_groups__group_type=CourseUserGroup.COHORT,
        )

    def filter_excluded_course_roles(self, queryset, name, value: list[str]):  # pylint: disable=unused-argument
        """Leave out learners holding one of the named roles in the course, or any role."""
        role_filters = {'user': OuterRef('user'), 'course_id': OuterRef('course_id')}
        if ALL_COURSE_ROLES not in value:
            role_filters['role__in'] = value
        return queryset.annotate(
            has_excluded_role=Exists(CourseAccessRole.objects.filter(**role_filters)),
        ).filter(has_excluded_role=False)

    def _filter_by_assignment_grade(self, queryset):
        """Narrow to learners whose score on one subsection falls within the requested bounds."""
        usage_key = self.form.cleaned_data.get('assignment_usage_key')
        minimum = self.form.cleaned_data.get('assignment_grade_min')
        maximum = self.form.cleaned_data.get('assignment_grade_max')
        if not usage_key or (minimum is None and maximum is None):
            return queryset
        effective_percentage = Case(
            When(
                override__isnull=False,
                then=(F('override__earned_graded_override') / F('override__possible_graded_override')) * 100,
            ),
            default=(F('earned_graded') / F('possible_graded')) * 100,
        )
        subsection_grades = PersistentSubsectionGrade.objects.annotate(
            effective_grade_percentage=effective_percentage,
        ).filter(
            course_id=OuterRef('course_id'),
            user_id=OuterRef('user_id'),
            usage_key=usage_key,
            effective_grade_percentage__range=(
                0 if minimum is None else minimum,
                100 if maximum is None else maximum,
            ),
        )
        return queryset.annotate(
            selected_assignment_grade_in_range=Exists(subsection_grades),
        ).filter(selected_assignment_grade_in_range=True)

    def _filter_by_course_grade(self, queryset):
        """Narrow to learners whose course grade falls within the requested bounds."""
        minimum = self.form.cleaned_data.get('course_grade_min')
        maximum = self.form.cleaned_data.get('course_grade_max')
        if minimum is None and maximum is None:
            return queryset
        bounds = {}
        if minimum is not None:
            bounds['percent_grade__gte'] = float(minimum) / 100
        if maximum is not None:
            bounds['percent_grade__lte'] = float(maximum) / 100
        annotations = {
            'course_grade_in_range': Exists(PersistentCourseGrade.objects.filter(
                course_id=OuterRef('course_id'), user_id=OuterRef('user_id'), **bounds,
            )),
        }
        matches = Q(course_grade_in_range=True)
        if not minimum:
            # A learner with no stored course grade has a grade of zero.
            annotations['course_grade_absent'] = ~Exists(PersistentCourseGrade.objects.filter(
                course_id=OuterRef('course_id'), user_id=OuterRef('user_id'),
            ))
            matches |= Q(course_grade_absent=True)
        return queryset.annotate(**annotations).filter(matches)


class SubsectionGradeOverrideHistoryFilterSet(filters.FilterSet):
    """Orders the override history of one subsection grade."""

    ordering = HistoryOrderingFilter(
        fields=(('history_date', 'history_date'),),
        help_text='Order the records by history_date. Prefix with "-" to reverse. Defaults to newest first.',
    )

    class Meta:
        model = PersistentSubsectionGradeOverride.history.model
        fields = []


class SubmissionHistoryFilterSet(filters.FilterSet):
    """Narrows the submission histories of one course to a set of learners, and orders them."""

    username = CharInFilter(
        field_name='user__username',
        lookup_expr='in',
        help_text=(
            'Return only the histories of these learners: a comma-separated list of '
            f'at most {MAX_USERNAMES} usernames.'
        ),
    )
    ordering = StableOrderingFilter(
        fields=(
            ('user__username', 'username'),
            ('id', 'id'),
        ),
        help_text='Order the results by username or id. Prefix with "-" to reverse. Defaults to id.',
    )

    class Meta:
        model = CourseEnrollment
        fields = []
        form = CourseGradeFilterForm
