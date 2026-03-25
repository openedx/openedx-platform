"""
API Serializers
"""


from rest_framework import serializers  # noqa: I001

from lms.djangoapps.program_enrollments.api import is_course_staff_enrollment
from lms.djangoapps.program_enrollments.models import ProgramCourseEnrollment, ProgramEnrollment

from .constants import CourseRunProgressStatuses

# pylint: disable=abstract-method


class InvalidStatusMixin:
    """
    Mixin to provide has_invalid_status method
    """
    def has_invalid_status(self):
        """
        Returns whether or not this serializer has an invalid error choice on
        the "status" field.
        """
        for status_error in self.errors.get('status', []):
            if status_error.code == 'invalid_choice':
                return True
        return False


class ProgramEnrollmentSerializer(serializers.Serializer):
    """
    Serializer for displaying enrollments in a program.
    """
    student_key = serializers.CharField(source='external_user_key')
    status = serializers.CharField()
    account_exists = serializers.SerializerMethodField()
    curriculum_uuid = serializers.UUIDField()
    username = serializers.SerializerMethodField()
    email = serializers.SerializerMethodField()

    class Meta:
        model = ProgramEnrollment

    def get_account_exists(self, obj):
        return bool(obj.user)

    def get_username(self, obj):
        if obj.user:
            return obj.user.username
        return ""

    def get_email(self, obj):
        if obj.user:
            return obj.user.email
        return ""


class ProgramEnrollmentRequestMixin(InvalidStatusMixin, serializers.Serializer):
    """
    Base fields for all program enrollment related serializers.
    """
    student_key = serializers.CharField(allow_blank=False, source='external_user_key')
    # We could have made this a ChoiceField on ProgramEnrollmentStatuses.__ALL__;
    # however, we instead check statuses in api/writing.py,
    # returning INVALID_STATUS for individual bad statuses instead of raising
    # a ValidationError for the entire request.
    status = serializers.CharField(allow_blank=False)


class ProgramEnrollmentCreateRequestSerializer(ProgramEnrollmentRequestMixin):
    """
    Serializer for program enrollment creation requests.
    """
    curriculum_uuid = serializers.UUIDField()


class ProgramEnrollmentUpdateRequestSerializer(ProgramEnrollmentRequestMixin):
    """
    Serializer for program enrollment update requests.
    """


class ProgramCourseEnrollmentSerializer(serializers.Serializer):
    """
    Serializer for displaying program-course enrollments.
    """
    student_key = serializers.SerializerMethodField()
    status = serializers.CharField()
    account_exists = serializers.SerializerMethodField()
    curriculum_uuid = serializers.SerializerMethodField()
    course_staff = serializers.SerializerMethodField()

    class Meta:
        model = ProgramCourseEnrollment

    def get_student_key(self, obj):
        return obj.program_enrollment.external_user_key

    def get_account_exists(self, obj):
        return bool(obj.program_enrollment.user)

    def get_curriculum_uuid(self, obj):
        return str(obj.program_enrollment.curriculum_uuid)

    def get_course_staff(self, obj):
        return is_course_staff_enrollment(obj)


class ProgramCourseEnrollmentRequestSerializer(serializers.Serializer, InvalidStatusMixin):
    """
    Serializer for request to create a ProgramCourseEnrollment
    """
    student_key = serializers.CharField(allow_blank=False, source='external_user_key')
    # We could have made this a ChoiceField on ProgramCourseEnrollmentStatuses.__ALL__;
    # however, we instead check statuses in api/writing.py,
    # returning INVALID_STATUS for individual bad statuses instead of raising
    # a ValidationError for the entire request.
    status = serializers.CharField(allow_blank=False)
    course_staff = serializers.BooleanField(required=False, default=None)


class ProgramCourseGradeSerializer(serializers.Serializer):
    """
    Serializer for a user's grade in a program courserun.

    Meant to be used with BaseProgramCourseGrade.
    """
    # Required
    student_key = serializers.SerializerMethodField()

    # From ProgramCourseGradeOk only
    passed = serializers.BooleanField(required=False)
    percent = serializers.FloatField(required=False)
    letter_grade = serializers.CharField(required=False)

    # From ProgramCourseGradeError only
    error = serializers.CharField(required=False)

    def get_student_key(self, obj):
        return obj.program_course_enrollment.program_enrollment.external_user_key


class DueDateSerializer(serializers.Serializer):
    """
    Serializer for a due date.
    """
    name = serializers.CharField()
    url = serializers.CharField()
    date = serializers.DateTimeField()


class CourseRunOverviewSerializer(serializers.Serializer):
    """
    Serializer for a course run overview.
    """
    STATUS_CHOICES = [
        CourseRunProgressStatuses.IN_PROGRESS,
        CourseRunProgressStatuses.UPCOMING,
        CourseRunProgressStatuses.COMPLETED
    ]

    course_run_id = serializers.CharField(
        help_text="ID for the course run.",
    )
    display_name = serializers.CharField(
        help_text="Display name of the course run.",
    )
    resume_course_run_url = serializers.CharField(
        required=False,
        help_text=(
            "The absolute url that takes the user back to their position in the "
            "course run; if absent, user has not made progress in the course."
        ),
    )
    course_run_url = serializers.CharField(
        help_text="The absolute url for the course run.",
    )
    start_date = serializers.DateTimeField(
        help_text="Start date for the course run; null if no start date.",
    )
    end_date = serializers.DateTimeField(
        help_text="End date for the course run; null if no end date.",
    )
    course_run_status = serializers.ChoiceField(
        allow_blank=False,
        choices=STATUS_CHOICES,
        help_text="The user's status of the course run.",
    )
    emails_enabled = serializers.BooleanField(
        required=False,
        help_text=(
            "Boolean representing whether emails are enabled for the course;"
            "if absent, the bulk email feature is either not enable at the platform"
            "level or is not enabled for the course; if True or False, bulk email"
            "feature is enabled, and value represents whether or not user wants"
            "to receive emails."
        ),
    )
    due_dates = serializers.ListField(
        child=DueDateSerializer(),
        help_text=(
            "List of subsection due dates for the course run. "
            "Due dates are only returned if the course run is in progress."
        ),
    )
    micromasters_title = serializers.CharField(
        required=False,
        help_text=(
            "Title of the MicroMasters program that the course run is a part of; "
            "if absent, the course run is not a part of a MicroMasters program."
        ),
    )
    certificate_download_url = serializers.CharField(
        required=False,
        help_text=(
            "URL to download a certificate, if available; "
            "if absent, certificate is not downloadable."
        ),
    )


class CourseRunOverviewListSerializer(serializers.Serializer):
    """
    Serializer for a list of course run overviews.
    """
    course_runs = serializers.ListField(child=CourseRunOverviewSerializer())
