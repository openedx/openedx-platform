"""Filter sets for version 2 of the User API."""

import logging

from django import forms
from django.contrib.auth.models import User  # pylint: disable=imported-auth-user
from django.core.exceptions import ValidationError as FormValidationError
from django_filters import rest_framework as filters
from opaque_keys import InvalidKeyError
from opaque_keys.edx.keys import CourseKey
from rest_framework.exceptions import ValidationError
from rest_framework.filters import OrderingFilter

from openedx.core.djangoapps.user_api.models import UserPreference

log = logging.getLogger(__name__)


class StrictOrderingFilter(OrderingFilter):
    """
    Refuses a sort field the view does not offer, and completes the sort it accepts.

    The default backend drops an unusable field silently and answers 200 in
    the default order, which a caller reads as a page sorted the way it asked.
    It also replaces the view's own order rather than adding to it, so a sort
    on a column several rows share leaves their relative order to the database
    and the pages of one listing need not agree on it; the fields named by the
    view's ``ordering_tiebreak`` are appended to settle those rows.
    """

    def get_ordering(self, request, queryset, view):
        """Return the requested sort, followed by the fields that make it total."""
        ordering = super().get_ordering(request, queryset, view)
        if not ordering:
            return ordering
        chosen = {field.lstrip("-") for field in ordering}
        tiebreak = [field for field in getattr(view, "ordering_tiebreak", ()) if field not in chosen]
        return [*ordering, *tiebreak]

    def remove_invalid_fields(self, queryset, fields, view, request):
        """Return the requested sort fields, refusing the request when one is not offered."""
        allowed = super().remove_invalid_fields(queryset, fields, view, request)
        rejected = [field for field in fields if field not in allowed]
        if rejected:
            log.info("Sort refused on %s: %r", type(view).__name__, rejected)
            offered = sorted(
                name for name, _label in self.get_valid_fields(queryset, view, {"request": request})
            )
            raise ValidationError({
                "ordering": [
                    f"Sort by any of: {', '.join(offered)}, each optionally prefixed with '-'.",
                ],
            })
        return allowed


class _BooleanQueryField(forms.NullBooleanField):
    """Reads a boolean out of the query string, refusing a value that names neither side."""

    def to_python(self, value):
        """Return the boolean this value names, or refuse the request."""
        if value in self.empty_values:
            return None
        parsed = super().to_python(value)
        if parsed is None:
            raise FormValidationError("Enter true or false.", code="invalid")
        return parsed


class StrictBooleanFilter(filters.BooleanFilter):
    """
    Refuses a value that names neither true nor false instead of ignoring it.

    The default field reads an unrecognised value as "no preference" and
    answers 200 with the filter dropped, handing the caller the rows it asked
    to leave out, which is the opposite of what it asked for.
    """

    field_class = _BooleanQueryField

    def __init__(self, *args, **kwargs):
        """Pass the raw value through to the field, which is what judges it."""
        # The default widget maps anything it does not recognise to "no
        # preference" before the field ever sees it; a text widget hands the
        # value over untouched.
        kwargs.setdefault("widget", forms.TextInput)
        super().__init__(*args, **kwargs)


class UserFilterSet(filters.FilterSet):
    """
    Narrows the user directory.

    ``forum_role`` and ``course_key`` name a role within one course and are
    only meaningful together, so either on its own is rejected. A role that no
    course has defined matches no users and is not created.
    """

    username = filters.CharFilter(help_text="Return the user with exactly this username.")
    email = filters.CharFilter(help_text="Return the user with exactly this email address.")
    is_active = StrictBooleanFilter(
        help_text="Return only users whose account is active, or only inactive ones. true or false.",
    )
    preference_key = filters.CharFilter(
        method="filter_preference_key",
        help_text="Return only users who have stored a preference with this key.",
    )
    forum_role = filters.CharFilter(
        method="filter_forum_role",
        help_text="Return only users holding this course forum role. Requires course_key.",
    )
    course_key = filters.CharFilter(
        method="filter_course_key",
        help_text=(
            "Course the forum role belongs to, percent-encoded, for example "
            "course-v1:edX%2BDemoX%2BDemo. A plus sign that is not encoded arrives as a "
            "space and the key is refused. Requires forum_role."
        ),
    )

    class Meta:
        model = User
        fields = ("username", "email", "is_active", "preference_key", "forum_role", "course_key")

    def filter_queryset(self, queryset):
        """Reject a forum role without its course, or a course without its role, before filtering."""
        cleaned = self.form.cleaned_data
        forum_role = cleaned.get("forum_role")
        course_key = cleaned.get("course_key")
        if forum_role and not course_key:
            raise ValidationError({"course_key": ["This parameter is required when forum_role is supplied."]})
        if course_key and not forum_role:
            raise ValidationError({"forum_role": ["This parameter is required when course_key is supplied."]})
        return super().filter_queryset(queryset)

    def filter_preference_key(self, queryset, name, value):  # pylint: disable=unused-argument
        """Keep users who have stored a preference under ``value``."""
        return queryset.filter(preferences__key=value)

    def filter_forum_role(self, queryset, name, value):  # pylint: disable=unused-argument
        """
        Keep users holding the role ``value`` in the course named by ``course_key``.

        Nothing stops a course from carrying the same role under several rows,
        and a user may hold any number of them, so the join is collapsed to one
        entry per user.
        """
        raw_course_key = self.form.cleaned_data.get("course_key")
        try:
            course_key = CourseKey.from_string(raw_course_key)
        except InvalidKeyError as exc:
            log.info("User directory filtered with an unparsable course key: %r", raw_course_key)
            raise ValidationError({"course_key": ["Enter a valid course key."]}) from exc
        return queryset.filter(roles__name=value, roles__course_id=course_key).distinct()

    def filter_course_key(self, queryset, name, value):  # pylint: disable=unused-argument
        """Narrowing happens in ``filter_forum_role``, which needs both values at once."""
        return queryset


class UserPreferenceFilterSet(filters.FilterSet):
    """
    Narrows the stored-preference list by preference name or by owner.

    An owner is named by username: the list is keyed the way the addresses of
    its members are, and publishes no database identifier for a caller to
    filter on.
    """

    key = filters.CharFilter(help_text="Return only preferences stored under exactly this key.")
    username = filters.CharFilter(
        field_name="user__username",
        help_text="Return only preferences belonging to this user.",
    )

    class Meta:
        model = UserPreference
        fields = ("key", "username")
