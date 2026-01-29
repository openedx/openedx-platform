""" Instructor apis serializers. """
import re

from django.contrib.auth.models import User  # lint-amnesty, pylint: disable=imported-auth-user
from django.core.exceptions import ValidationError
from django.utils.translation import gettext as _
from rest_framework import serializers

from lms.djangoapps.certificates.data import CertificateStatuses
from lms.djangoapps.instructor.access import ROLES
from openedx.core.djangoapps.django_comment_common.models import (
    FORUM_ROLE_ADMINISTRATOR,
    FORUM_ROLE_COMMUNITY_TA,
    FORUM_ROLE_GROUP_MODERATOR,
    FORUM_ROLE_MODERATOR,
    Role
)
from lms.djangoapps.discussion.django_comment_client.utils import (
    get_group_id_for_user,
    get_group_name
)

from .tools import get_student_from_identifier


class RoleNameSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """
    Serializer that describes the response of the problem response report generation API.
    """

    rolename = serializers.CharField(help_text=_("Role name"))

    def validate_rolename(self, value):
        """
        Check that the rolename is valid.
        """
        if value not in ROLES:
            raise ValidationError(_("Invalid role name."))
        return value


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ['username', 'email', 'first_name', 'last_name']


class UniqueStudentIdentifierSerializer(serializers.Serializer):
    """
    Serializer for identifying unique_student.
    """
    unique_student_identifier = serializers.CharField(
        max_length=255,
        help_text="Email or username of user to change access"
    )

    def validate_unique_student_identifier(self, value):
        """
        Validate that the unique_student_identifier corresponds to an existing user.
        """
        try:
            user = get_student_from_identifier(value)
        except User.DoesNotExist:
            return None

        return user


class AccessSerializer(UniqueStudentIdentifierSerializer):
    """
    Serializer for managing user access changes.
    This serializer validates and processes the data required to modify
    user access within a system.
    """
    rolename = serializers.CharField(
        help_text="Role name to assign to the user"
    )
    action = serializers.ChoiceField(
        choices=['allow', 'revoke'],
        help_text="Action to perform on the user's access"
    )


class ForumRoleNameSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """
    Serializer for forum rolename.
    """

    rolename = serializers.CharField(help_text=_("Role name"))
    users = serializers.SerializerMethodField()

    def validate_rolename(self, value):
        """
        Check that the rolename is valid.
        """
        if value not in [
            FORUM_ROLE_ADMINISTRATOR, FORUM_ROLE_COMMUNITY_TA, FORUM_ROLE_GROUP_MODERATOR, FORUM_ROLE_MODERATOR
        ]:
            raise ValidationError(_("Invalid role name."))
        return value

    def get_users(self, obj):
        """
        Retrieve a list of users associated with the specified role and course.

        Args:
            obj (dict): A dictionary containing the 'rolename' for which to retrieve users.
                        This dictionary is the data passed to the serializer.

        Returns:
            list: A list of dictionaries, each representing a user associated with the specified role.
                  Each user dictionary contains 'username', 'email', 'first_name', 'last_name', and 'group_name'.
                  If no users are found, an empty list is returned.

        """
        course_id = self.context.get('course_id')
        rolename = obj['rolename']
        try:
            role = Role.objects.get(name=rolename, course_id=course_id)
            users = role.users.all().order_by('username')
        except Role.DoesNotExist:
            users = []

        return [extract_user_info(user, self.context.get('course_discussion_settings')) for user in users]


def extract_user_info(user, course_discussion_settings):
    """ utility method to convert user into dict for JSON rendering. """
    group_id = get_group_id_for_user(user, course_discussion_settings)
    group_name = get_group_name(group_id, course_discussion_settings)

    return {
        'username': user.username,
        'email': user.email,
        'first_name': user.first_name,
        'last_name': user.last_name,
        'group_name': group_name,
    }


class ListInstructorTaskInputSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """
    Serializer for handling the input data for the problem response report generation API.

Attributes:
    unique_student_identifier (str): The email or username of the student.
                                      This field is optional, but if provided, the `problem_location_str`
                                      must also be provided.
    problem_location_str (str): The string representing the location of the problem within the course.
                                This field is optional, unless `unique_student_identifier` is provided.
    """
    unique_student_identifier = serializers.CharField(
        max_length=255,
        help_text="Email or username of student",
        required=False
    )
    problem_location_str = serializers.CharField(
        help_text="Problem location",
        required=False
    )

    def validate(self, data):
        """
        Validate the data to ensure that if unique_student_identifier is provided,
        problem_location_str must also be provided.
        """
        unique_student_identifier = data.get('unique_student_identifier')
        problem_location_str = data.get('problem_location_str')

        if unique_student_identifier and not problem_location_str:
            raise serializers.ValidationError(
                "unique_student_identifier must accompany problem_location_str"
            )

        return data


class ShowStudentExtensionSerializer(serializers.Serializer):
    """
    Serializer for validating and processing the student identifier.
    """
    student = serializers.CharField(write_only=True, required=True)

    def validate_student(self, value):
        """
        Validate that the student corresponds to an existing user.
        """
        try:
            user = get_student_from_identifier(value)
        except User.DoesNotExist:
            return None

        return user


class ShowUnitExtensionsSerializer(serializers.Serializer):
    """
    Serializer for showing all students who have due date extensions
    for a specific unit (block).

    Fields:
        url (str): The URL (block ID) of the unit for which student extensions should be retrieved.
    """
    url = serializers.CharField(
        required=True,
        max_length=2048,
        help_text="The unit URL (block ID) to retrieve student extensions for."
    )


class StudentAttemptsSerializer(serializers.Serializer):
    """
    Serializer for resetting a students attempts counter or starts a task to reset all students
    attempts counters.
    """
    problem_to_reset = serializers.CharField(
        help_text="The identifier or description of the problem that needs to be reset."
    )

    # following are optional params.
    unique_student_identifier = serializers.CharField(
        help_text="Email or username of student.", required=False
    )
    all_students = serializers.CharField(required=False)
    delete_module = serializers.CharField(required=False)

    def validate_all_students(self, value):
        """
        converts the all_student params value to bool.
        """
        return self.verify_bool(value)

    def validate_delete_module(self, value):
        """
        converts the all_student params value.
        """
        return self.verify_bool(value)

    def validate_unique_student_identifier(self, value):
        """
        Validate that the student corresponds to an existing user.
        """
        try:
            user = get_student_from_identifier(value)
        except User.DoesNotExist:
            return None

        return user

    def verify_bool(self, value):
        """
        Returns the value of the boolean parameter with the given
        name in the POST request. Handles translation from string
        values to boolean values.
        """
        if value is not None:
            return value in ['true', 'True', True]


class UpdateForumRoleMembershipSerializer(AccessSerializer):
    """
    Serializer for managing user's forum role.

    This serializer extends the AccessSerializer to allow for different action
    choices specific to this API. It validates and processes the data required
    to modify user access within a system.

    Attributes:
        unique_student_identifier (str): The email or username of the user whose access is being modified.
        rolename (str): The role name to assign to the user.
        action (str): The specific action to perform on the user's access, with options 'activate' or 'deactivate'.
    """
    rolename = serializers.ChoiceField(
        choices=[
            FORUM_ROLE_ADMINISTRATOR, FORUM_ROLE_MODERATOR,
            FORUM_ROLE_GROUP_MODERATOR, FORUM_ROLE_COMMUNITY_TA
        ],
        help_text="Rolename assign to given user."
    )


class SendEmailSerializer(serializers.Serializer):
    """
    Serializer for sending an email with optional scheduling.

    Fields:
        send_to (str): The email address of the recipient. This field is required.
        subject (str): The subject line of the email. This field is required.
        message (str): The body of the email. This field is required.
        schedule (str, optional):
        An optional field to specify when the email should be sent.
        If provided, this should be a string that can be parsed into a
        datetime format or some other scheduling logic.
    """
    send_to = serializers.CharField(write_only=True, required=True)

    # set max length as per model field.
    subject = serializers.CharField(max_length=128, write_only=True, required=True)
    message = serializers.CharField(required=True)
    schedule = serializers.CharField(required=False)


class BlockDueDateSerializer(serializers.Serializer):
    """
    Serializer for handling block due date updates for a specific student.
    Fields:
        url (str): The URL related to the block that needs the due date update.
        due_datetime (str): The new due date and time for the block.
        student (str): The email or username of the student whose access is being modified.
        reason (str): Reason why updating this.
    """
    url = serializers.CharField()
    due_datetime = serializers.CharField()
    student = serializers.CharField(
        max_length=255,
        help_text="Email or username of user to change access"
    )
    reason = serializers.CharField(required=False)

    def validate_student(self, value):
        """
        Validate that the student corresponds to an existing user.
        """
        try:
            user = get_student_from_identifier(value)
        except User.DoesNotExist:
            return None

        return user

    def __init__(self, *args, **kwargs):
        # Get context to check if `due_datetime` should be optional
        disable_due_datetime = kwargs.get('context', {}).get('disable_due_datetime', False)
        super().__init__(*args, **kwargs)
        if disable_due_datetime:
            self.fields['due_datetime'].required = False


class EnrollmentOverviewSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """
    Light-weight serializer used by the enrollment overview export endpoint.

    The serializer intentionally keeps validation lax so instructors can try out
    different filters without getting blocked by strict requirements.
    """
    start = serializers.CharField(required=False, help_text="Optional ISO-8601 start date filter")
    end = serializers.CharField(required=False, help_text="Optional ISO-8601 end date filter")
    search = serializers.CharField(required=False, help_text="Partial username or email")
    mode = serializers.CharField(required=False, help_text="Enrollment track slug")
    limit = serializers.IntegerField(required=False, default=2500)
    order_by = serializers.CharField(required=False, default='enrollment_date')

    def validate_limit(self, value):
        # Keep things permissive: treat negative values as unbounded
        if value is None or value <= 0:
            return 0
        return value


class ProblemResetSerializer(UniqueStudentIdentifierSerializer):
    """
    serializer for resetting problem.
    """
    problem_to_reset = serializers.CharField(
        help_text=_("The URL name of the problem to reset."),
        error_messages={
            'blank': _("Problem URL name cannot be blank."),
        }
    )
    all_students = serializers.BooleanField(
        default=False,
        help_text=_("Whether to reset the problem for all students."),
    )
    only_if_higher = serializers.BooleanField(
        default=False,
    )

    # Override the unique_student_identifier field to make it optional
    unique_student_identifier = serializers.CharField(
        required=False,  # Make this field optional
        allow_null=True,
        help_text=_("unique student identifier.")
    )


class ModifyAccessSerializer(serializers.Serializer):
    """
    serializers for enroll or un-enroll users in beta testing program.
    """
    identifiers = serializers.CharField(
        help_text="A comma separated list of emails or usernames.",
        required=True
    )
    action = serializers.ChoiceField(
        choices=["add", "remove"],
        help_text="Action to perform: add or remove.",
        required=True
    )

    email_students = serializers.BooleanField(
        default=False,
        help_text="Boolean flag to indicate if students should be emailed."
    )

    auto_enroll = serializers.BooleanField(
        default=False,
        help_text="Boolean flag to indicate if the user should be auto-enrolled."
    )

    def validate_identifiers(self, value):
        """
        Validate the 'identifiers' field which is now a list of strings.
        """
        # Iterate over the list of identifiers and validate each one
        validated_list = _split_input_list(value)
        if not validated_list:
            raise serializers.ValidationError("The identifiers list cannot be empty.")

        return validated_list

    def validate_email_students(self, value):
        """
        handle string values like 'true' or 'false'.
        """
        if isinstance(value, str):
            return value.lower() == 'true'
        return bool(value)

    def validate_auto_enroll(self, value):
        """
        handle string values like 'true' or 'false'.
        """
        if isinstance(value, str):
            return value.lower() == 'true'
        return bool(value)


def _split_input_list(str_list):
    """
    Separate out individual student email from the comma, or space separated string.

    e.g.
    in: "Lorem@ipsum.dolor, sit@amet.consectetur\nadipiscing@elit.Aenean\r convallis@at.lacus\r, ut@lacinia.Sed"
    out: ['Lorem@ipsum.dolor', 'sit@amet.consectetur', 'adipiscing@elit.Aenean', 'convallis@at.lacus', 'ut@lacinia.Sed']

    `str_list` is a string coming from an input text area
    returns a list of separated values
    """
    new_list = re.split(r'[,\s\n\r]+', str_list)
    new_list = [s.strip() for s in new_list]
    new_list = [s for s in new_list if s != '']

    return new_list


class CertificateStatusesSerializer(serializers.Serializer):
    """
    Serializer for validating and serializing certificate status inputs.

    This serializer is used to ensure that the provided certificate statuses
    conform to the predefined set of valid statuses defined in the
    `CertificateStatuses` enumeration.
    """
    certificate_statuses = serializers.ListField(
        child=serializers.ChoiceField(choices=[
            CertificateStatuses.downloadable,
            CertificateStatuses.error,
            CertificateStatuses.notpassing,
            CertificateStatuses.audit_passing,
            CertificateStatuses.audit_notpassing,
        ]),
        allow_empty=False  # Set to True if you want to allow empty lists
    )


class CertificateSerializer(serializers.Serializer):
    """
    Serializer for multiple operations related with certificates.
    resetting a students attempts counter or starts a task to reset all students
    attempts counters
    Also Add/Remove students to/from the certificate allowlist.
    Also For resetting a students attempts counter or starts a task to reset all students
    attempts counters.
    """
    user = serializers.CharField(
        help_text="Email or username of student.", required=True
    )
    notes = serializers.CharField(required=False, allow_null=True, allow_blank=True)

    def validate_user(self, value):
        """
        Validate that the user corresponds to an existing user.
        """
        try:
            user = get_student_from_identifier(value)
        except User.DoesNotExist:
            return None

        return user


class RescoreEntranceExamSerializer(serializers.Serializer):
    """Serializer for entrance exam rescoring"""
    unique_student_identifier = serializers.CharField(required=False, allow_null=True)
    all_students = serializers.BooleanField(required=False)
    only_if_higher = serializers.BooleanField(required=False, allow_null=True)


class ResetEntranceExamAttemptsSerializer(UniqueStudentIdentifierSerializer):
    """
    Serializer for resetting entrance exam attempts or deleting entrance exam state.
    Inherits user validation from UniqueStudentIdentifierSerializer.
    """
    all_students = serializers.BooleanField(
        required=False,
        default=False,
        help_text=_("Whether to reset for all students."),
    )
    delete_module = serializers.BooleanField(
        required=False,
        default=False,
        help_text=_("Whether to delete entrance exam state for the student."),
    )

    # Override the unique_student_identifier field to make it optional
    unique_student_identifier = serializers.CharField(
        required=False,  # Make this field optional
        allow_null=True,
        help_text=_("unique student identifier.")
    )

    def validate_unique_student_identifier(self, value):
        """
        Validate that the unique_student_identifier corresponds to an existing user,
        if a value is provided.
        """
        if not value:
            return None

        user = super().validate_unique_student_identifier(value)
        if user is None:
            raise serializers.ValidationError(
                "No user found with the provided identifier."
            )

        return user

    def validate(self, attrs):
        all_students = attrs.get("all_students", False)
        student = attrs.get("unique_student_identifier")
        delete_module = attrs.get("delete_module", False)

        errors = []

        if all_students and student:
            errors.append(_("all_students and unique_student_identifier are mutually exclusive."))

        if all_students and delete_module:
            errors.append(_(
                "all_students and delete_module are mutually exclusive."
            ))

        if not (student or all_students):
            errors.append(_("You must provide either unique_student_identifier or set all_students to True."))

        if errors:
            raise serializers.ValidationError({"non_field_errors": errors})

        return attrs


class StudentsUpdateEnrollmentSerializer(serializers.Serializer):
    """Serializer for student enroll/unenroll actions."""
    action = serializers.ChoiceField(choices=["enroll", "unenroll"])
    identifiers = serializers.CharField()
    auto_enroll = serializers.BooleanField(default=False)
    email_students = serializers.BooleanField(default=False)
    reason = serializers.CharField(required=False, allow_blank=True)


class OverrideProblemScoreSerializer(UniqueStudentIdentifierSerializer):
    """
    Serializer for overriding a student's score for a specific problem.
    """
    problem_to_reset = serializers.CharField(
        help_text=_("The URL name of the problem to override the score for."),
        error_messages={
            'blank': _("Problem URL name cannot be blank."),
        }
    )
    score = serializers.FloatField(
        help_text=_("The overriding score to set."),
    )
