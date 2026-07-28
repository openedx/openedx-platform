"""
Course API Forms
"""


from django.contrib.auth.models import AnonymousUser, User  # pylint: disable=imported-auth-user
from django.core.exceptions import ValidationError
from django.forms import CharField, ChoiceField, Form, IntegerField
from django.http import Http404
from opaque_keys import InvalidKeyError
from opaque_keys.edx.keys import UsageKey
from rest_framework.exceptions import PermissionDenied

from openedx.core.djangoapps.util.forms import ExtendedNullBooleanField, MultiValueField
from xmodule.modulestore.django import modulestore  # pylint: disable=wrong-import-order

from . import permissions


class BlockListGetForm(Form):
    """
    A form to validate query parameters in the block list retrieval endpoint
    """
    all_blocks = ExtendedNullBooleanField(required=False)
    block_counts = MultiValueField(required=False)
    depth = CharField(required=False)
    nav_depth = IntegerField(required=False, min_value=0)
    requested_fields = MultiValueField(required=False)
    return_type = ChoiceField(
        required=False,
        choices=[(choice, choice) for choice in ['dict', 'list']],
    )
    student_view_data = MultiValueField(required=False)
    usage_key = CharField(required=True)
    username = CharField(required=False)
    block_types_filter = MultiValueField(required=False)

    def clean_depth(self):
        """
        Get the appropriate depth.  No provided value will be treated as a
        depth of 0, while a value of "all" will be treated as unlimited depth.
        """
        value = self.cleaned_data['depth']
        if not value:
            return 0
        elif value == "all":
            return None
        try:
            return int(value)
        except ValueError:
            raise ValidationError(f"'{value}' is not a valid depth value.")  # pylint: disable=raise-missing-from  # noqa: B904

    def clean_requested_fields(self):
        """
        Return a set of `requested_fields`, merged with defaults of `type`
        and `display_name`
        """
        requested_fields = self.cleaned_data['requested_fields']

        # add default requested_fields
        return (requested_fields or set()) | {'type', 'display_name'}

    def clean_return_type(self):
        """
        Return valid 'return_type' or default value of 'dict'
        """
        return self.cleaned_data['return_type'] or 'dict'

    def clean_usage_key(self):
        """
        Ensure a valid `usage_key` was provided.
        """
        usage_key = self.cleaned_data['usage_key']

        try:
            usage_key = UsageKey.from_string(usage_key)
        except InvalidKeyError:
            raise ValidationError(f"'{str(usage_key)}' is not a valid usage key.")  # pylint: disable=raise-missing-from  # noqa: B904

        return usage_key.replace(course_key=modulestore().fill_in_run(usage_key.course_key))

    def clean(self):
        """
        Return cleaned data, including additional requested fields.
        """
        cleaned_data = super().clean()

        # Add additional requested_fields that are specified as separate
        # parameters, if they were requested.
        additional_requested_fields = [
            'student_view_data',
            'block_counts',
            'nav_depth',
            'block_types_filter',
        ]
        for additional_field in additional_requested_fields:
            field_value = cleaned_data.get(additional_field)
            if field_value or field_value == 0:  # allow 0 as a requested value
                cleaned_data['requested_fields'].add(additional_field)

        usage_key = cleaned_data.get('usage_key')
        if not usage_key:
            return

        cleaned_data['user'] = self._clean_requested_user(cleaned_data, usage_key.course_key)
        return cleaned_data

    def clean_username(self):
        """
        Return cleaned username.

        Overrides the default behaviour that maps an empty string to None. This
        allows us to differentiate between no username being provided (None) vs
        an empty username being provided ('').
        """
        # In case all_blocks is specified, ignore the username.
        if self.cleaned_data.get('all_blocks', False):
            return None

        # See if 'username' was provided as a parameter in the raw data.
        # If so, we return the already-cleaned version of that, otherwise we
        # return None
        if 'username' in self.data:
            return self.cleaned_data['username']
        return None

    def _clean_requested_user(self, cleaned_data, course_key):
        """
        Validates and returns the requested_user, while checking permissions.
        """
        requesting_user = self.initial['requesting_user']
        requested_username = cleaned_data.get('username', None)
        all_blocks = cleaned_data.get('all_blocks', False)

        if requested_username is None and not all_blocks:
            raise ValidationError({'username': ["This field is required unless all_blocks is requested."]})

        if requesting_user.is_anonymous:
            return self._verify_anonymous_user(requested_username, course_key, all_blocks)

        if all_blocks:
            if requesting_user.has_perm('instructor.research', course_key):
                return requesting_user
            return self._verify_all_blocks(requesting_user, course_key)
        elif requesting_user.username.lower() == requested_username.lower():
            return self._verify_requesting_user(requesting_user, course_key)
        else:
            return self._verify_other_user(requesting_user, requested_username, course_key)

    @staticmethod
    def _verify_anonymous_user(username, course_key, all_blocks):
        """
        Verifies form for when the requesting user is anonymous.
        """
        if all_blocks:
            raise PermissionDenied(
                "Anonymous users do not have permission to access all blocks in '{course_key}'.".format(  # noqa: UP032
                    course_key=str(course_key),
                )
            )

        # Check for '' and explicitly '' since the only valid option for anonymous users is
        # an empty string that corresponds to an anonymous user.
        if username != '':
            raise PermissionDenied("Anonymous users cannot access another user's blocks.")

        if not permissions.is_course_public(course_key):
            raise PermissionDenied(
                "Course blocks for '{course_key}' cannot be accessed anonymously.".format(  # noqa: UP032
                    course_key=course_key,
                )
            )

        return AnonymousUser()

    @staticmethod
    def _verify_all_blocks(requesting_user, course_key):  # pylint: disable=useless-return
        """
        Verifies form for when no username is specified, including permissions.
        """
        # Verify all blocks can be accessed for the course.
        if not permissions.can_access_all_blocks(requesting_user, course_key):
            raise PermissionDenied(
                "'{requesting_username}' does not have permission to access all blocks in '{course_key}'.".format(  # noqa: UP032  # pylint: disable=line-too-long
                    requesting_username=requesting_user.username,
                    course_key=str(course_key),
                )
            )

        return None

    @staticmethod
    def _verify_requesting_user(requesting_user, course_key):
        """
        Verifies whether the requesting user can access blocks in the course.
        """
        if not permissions.can_access_self_blocks(requesting_user, course_key):
            raise PermissionDenied(
                "Course blocks for '{requesting_username}' cannot be accessed.".format(  # noqa: UP032
                    requesting_username=requesting_user.username,
                )
            )
        return requesting_user

    @staticmethod
    def _verify_other_user(requesting_user, requested_username, course_key):
        """
        Verifies whether the requesting user can access another user's view of
        the blocks in the course.
        """
        # If accessing a public course, and requesting only content available publicly,
        # we can allow the request.
        if requested_username == '' and permissions.is_course_public(course_key):
            return AnonymousUser()

        # Verify requesting user can access the user's blocks.
        if not permissions.can_access_others_blocks(requesting_user, course_key):
            raise PermissionDenied(
                "'{requesting_username}' does not have permission to access view for '{requested_username}'.".format(  # noqa: UP032  # pylint: disable=line-too-long
                    requesting_username=requesting_user.username,
                    requested_username=requested_username,
                )
            )

        # Verify user exists.
        try:
            return User.objects.get(username=requested_username)
        except User.DoesNotExist:
            raise Http404(  # pylint: disable=raise-missing-from  # noqa: B904
                "Requested user '{requested_username}' does not exist.".format(  # noqa: UP032
                    requested_username=requested_username,
                )
            )
