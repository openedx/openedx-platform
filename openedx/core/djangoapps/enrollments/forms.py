"""
Forms for validating user input to the Course Enrollment related views.
"""


from django.core.exceptions import ValidationError
from django.forms import CharField, Form
from opaque_keys import InvalidKeyError
from opaque_keys.edx.keys import CourseKey

from openedx.core.djangoapps.user_authn.views.registration_form import validate_username


class CourseEnrollmentsApiListForm(Form):
    """
    A form that validates the query string parameters for the CourseEnrollmentsApiListView.
    """
    MAX_INPUT_COUNT = 100
    username = CharField(required=False)
    course_id = CharField(required=False)
    course_ids = CharField(required=False)
    email = CharField(required=False)

    def clean_course_id(self):
        """
        Validate and return a course ID.
        """
        course_id = self.cleaned_data.get('course_id')
        if course_id:
            try:
                return CourseKey.from_string(course_id)
            except InvalidKeyError:
                raise ValidationError(f"'{course_id}' is not a valid course id.")  # pylint: disable=raise-missing-from  # noqa: B904
        return course_id

    def clean_username(self):
        """
        Validate a string of comma-separated usernames and return a list of usernames.
        """
        usernames_csv_string = self.cleaned_data.get('username')
        if usernames_csv_string:
            usernames = usernames_csv_string.split(',')
            if len(usernames) > self.MAX_INPUT_COUNT:
                raise ValidationError(
                    "Too many usernames in a single request - {}. A maximum of {} is allowed".format(  # noqa: UP032
                        len(usernames),
                        self.MAX_INPUT_COUNT,
                    )
                )
            for username in usernames:
                validate_username(username)
            return usernames
        return usernames_csv_string

    def clean_course_ids(self):
        """
        Validate a string of comma-separated course IDs and return a list of course IDs.
        """
        course_ids_csv_string = self.cleaned_data.get('course_ids')
        if course_ids_csv_string:
            course_ids = course_ids_csv_string.split(',')
            if len(course_ids) > self.MAX_INPUT_COUNT:
                raise ValidationError(
                    "Too many course_ids in a single request - {}. A maximum of {} is allowed".format(  # noqa: UP032
                        len(course_ids),
                        self.MAX_INPUT_COUNT,
                    )
                )
            return course_ids

        return course_ids_csv_string

    def clean_email(self):
        """
        Validate a string of comma-separated emails and return a list of emails.
        """
        emails_csv_string = self.cleaned_data.get('email')
        if emails_csv_string:
            emails = emails_csv_string.split(',')
            if len(emails) > self.MAX_INPUT_COUNT:
                raise ValidationError(
                    "Too many emails in a single request - {}. A maximum of {} is allowed".format(  # noqa: UP032
                        len(emails),
                        self.MAX_INPUT_COUNT,
                    )
                )
            return emails
        return emails_csv_string
