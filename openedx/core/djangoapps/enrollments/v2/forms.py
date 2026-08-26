"""
Forms for validating user input to the Course Enrollment v2 views.

ADR 0033 (OEP-68 parameter naming standardization) — accepts both the
preferred parameter names (``course_key``, ``course_keys``) and the legacy
aliases (``course_id``, ``course_ids``). When both are present, the
preferred name wins. Use :meth:`legacy_param_aliases_used` from the view
layer to emit the ADR 0033 ``Deprecation`` HTTP header when a legacy alias
was sent.

Internally the cleaned_data continues to expose ``course_id`` /
``course_ids`` (the names the queryset code reads) — the form coalesces
the preferred values onto those fields before the rest of validation runs.
"""

from django.core.exceptions import ValidationError
from django.forms import CharField, Form
from opaque_keys import InvalidKeyError
from opaque_keys.edx.keys import CourseKey

from openedx.core.djangoapps.user_authn.views.registration_form import validate_username


class EnrollmentsAdminListForm(Form):
    """
    Validates the query string parameters for the v2 admin enrollments list
    endpoint (``GET /api/enrollment/v2/enrollments/``).
    """

    MAX_INPUT_COUNT = 100
    # Legacy / OEP-68 alias pairs: (legacy, preferred).
    _LEGACY_PARAM_ALIASES = (
        ("course_id", "course_key"),
        ("course_ids", "course_keys"),
    )

    username = CharField(required=False)
    course_id = CharField(required=False)
    course_key = CharField(required=False)
    course_ids = CharField(required=False)
    course_keys = CharField(required=False)
    email = CharField(required=False)

    def __init__(self, query_params, *args, **kwargs):
        # Capture the raw param names supplied on the wire (before Django's
        # form layer resolves aliases) so :meth:`legacy_param_aliases_used`
        # can later report exactly which legacy names were used.
        try:
            raw_keys = set(query_params.keys())
        except AttributeError:
            raw_keys = set()
        self._raw_param_names = raw_keys

        # Coalesce OEP-68 preferred names onto the legacy field names so the
        # downstream queryset code keeps reading ``course_id`` / ``course_ids``
        # without changes. The preferred name wins when both are sent.
        if hasattr(query_params, "copy"):
            data = query_params.copy()
        else:
            data = dict(query_params)
        for legacy_name, preferred_name in self._LEGACY_PARAM_ALIASES:
            preferred_value = data.get(preferred_name)
            if preferred_value:
                data[legacy_name] = preferred_value

        super().__init__(data, *args, **kwargs)

    def legacy_param_aliases_used(self):
        """
        Return the list of legacy parameter names that were actually present
        in the request, in declaration order. The view layer uses this to
        emit the ADR 0033 ``Deprecation`` header.
        """
        return [
            legacy for legacy, _preferred in self._LEGACY_PARAM_ALIASES
            if legacy in self._raw_param_names
        ]

    def clean_course_id(self):
        """Parse and validate the ``course_id`` (or aliased ``course_key``) parameter."""
        course_id = self.cleaned_data.get("course_id")
        if course_id:
            try:
                return CourseKey.from_string(course_id)
            except InvalidKeyError as exc:
                raise ValidationError(f"'{course_id}' is not a valid course id.") from exc
        return course_id

    def clean_course_ids(self):
        """Split the ``course_ids`` CSV (or aliased ``course_keys``) and enforce MAX_INPUT_COUNT."""
        course_ids_csv = self.cleaned_data.get("course_ids")
        if course_ids_csv:
            course_ids = course_ids_csv.split(",")
            if len(course_ids) > self.MAX_INPUT_COUNT:
                raise ValidationError(
                    f"Too many course_ids in a single request - {len(course_ids)}. "
                    f"A maximum of {self.MAX_INPUT_COUNT} is allowed"
                )
            return course_ids
        return course_ids_csv

    def clean_username(self):
        """Split the ``username`` CSV, validate each entry, and enforce MAX_INPUT_COUNT."""
        usernames_csv = self.cleaned_data.get("username")
        if usernames_csv:
            usernames = usernames_csv.split(",")
            if len(usernames) > self.MAX_INPUT_COUNT:
                raise ValidationError(
                    f"Too many usernames in a single request - {len(usernames)}. "
                    f"A maximum of {self.MAX_INPUT_COUNT} is allowed"
                )
            for username in usernames:
                validate_username(username)
            return usernames
        return usernames_csv

    def clean_email(self):
        """Split the ``email`` CSV and enforce MAX_INPUT_COUNT."""
        emails_csv = self.cleaned_data.get("email")
        if emails_csv:
            emails = emails_csv.split(",")
            if len(emails) > self.MAX_INPUT_COUNT:
                raise ValidationError(
                    f"Too many emails in a single request - {len(emails)}. "
                    f"A maximum of {self.MAX_INPUT_COUNT} is allowed"
                )
            return emails
        return emails_csv
