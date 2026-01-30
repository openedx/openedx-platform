"""
Serializers for Instructor API v2.

These serializers handle data validation and business logic for instructor dashboard endpoints.
Following REST best practices, serializers encapsulate most of the data processing logic.
"""

from django.conf import settings
from django.db.models import Count
from django.utils.html import escape
from django.utils.translation import gettext as _
from edx_when.api import is_enabled_for_course
from rest_framework import serializers

from common.djangoapps.student.models import CourseEnrollment
from common.djangoapps.student.roles import (
    CourseFinanceAdminRole,
    CourseInstructorRole,
    CourseSalesAdminRole,
    CourseStaffRole,
)
from lms.djangoapps.bulk_email.api import is_bulk_email_feature_enabled
from lms.djangoapps.certificates.models import (
    CertificateGenerationConfiguration
)
from lms.djangoapps.courseware.access import has_access
from lms.djangoapps.courseware.courses import get_studio_url
from lms.djangoapps.discussion.django_comment_client.utils import has_forum_access
from lms.djangoapps.instructor import permissions
from lms.djangoapps.instructor.views.instructor_dashboard import get_analytics_dashboard_message
from openedx.core.djangoapps.django_comment_common.models import FORUM_ROLE_ADMINISTRATOR
from xmodule.modulestore.django import modulestore

from .tools import get_student_from_identifier, parse_datetime, DashboardError


class CourseInformationSerializerV2(serializers.Serializer):
    """
    Serializer for comprehensive course information.

    This serializer handles the business logic for gathering all course metadata,
    enrollment statistics, permissions, and dashboard configuration.
    """
    course_id = serializers.SerializerMethodField(help_text="Course run key")
    display_name = serializers.SerializerMethodField(help_text="Course display name")
    org = serializers.SerializerMethodField(help_text="Organization identifier")
    course_number = serializers.SerializerMethodField(help_text="Course number")
    course_run = serializers.SerializerMethodField(help_text="Course run identifier")
    enrollment_start = serializers.SerializerMethodField(help_text="Enrollment start date (ISO 8601 with timezone)")
    enrollment_end = serializers.SerializerMethodField(help_text="Enrollment end date (ISO 8601 with timezone)")
    start = serializers.SerializerMethodField(help_text="Course start date (ISO 8601 with timezone)")
    end = serializers.SerializerMethodField(help_text="Course end date (ISO 8601 with timezone)")
    pacing = serializers.SerializerMethodField(help_text="Course pacing type (self or instructor)")
    has_started = serializers.SerializerMethodField(help_text="Whether the course has started based on current time")
    has_ended = serializers.SerializerMethodField(help_text="Whether the course has ended based on current time")
    total_enrollment = serializers.SerializerMethodField(help_text="Total number of enrollments across all modes")
    enrollment_counts = serializers.SerializerMethodField(help_text="Enrollment count breakdown by mode")
    num_sections = serializers.SerializerMethodField(help_text="Number of sections/chapters in the course")
    grade_cutoffs = serializers.SerializerMethodField(help_text="Formatted string of grade cutoffs")
    course_errors = serializers.SerializerMethodField(help_text="List of course validation errors from modulestore")
    studio_url = serializers.SerializerMethodField(help_text="URL to view/edit course in Studio")
    permissions = serializers.SerializerMethodField(help_text="User permissions for instructor dashboard features")
    tabs = serializers.SerializerMethodField(help_text="List of course tabs with configuration and display information")
    disable_buttons = serializers.SerializerMethodField(
        help_text="Whether to disable certain bulk action buttons due to large course size"
    )
    analytics_dashboard_message = serializers.SerializerMethodField(
        help_text="Message about analytics dashboard availability"
    )

    def get_tabs(self, data):
        """Get serialized course tabs."""
        request = data['request']
        course = data['course']
        course_key = course.id
        mfe_base_url = settings.INSTRUCTOR_MICROFRONTEND_URL

        access = {
            'admin': request.user.is_staff,
            'instructor': bool(has_access(request.user, 'instructor', course)),
            'finance_admin': CourseFinanceAdminRole(course_key).has_user(request.user),
            'sales_admin': CourseSalesAdminRole(course_key).has_user(request.user),
            'staff': bool(has_access(request.user, 'staff', course)),
            'forum_admin': has_forum_access(request.user, course_key, FORUM_ROLE_ADMINISTRATOR),
            'data_researcher': request.user.has_perm(permissions.CAN_RESEARCH, course_key),
        }
        tabs = []

        # NOTE: The Instructor experience can be extended via FE plugins that insert tabs
        # dynamically using explicit priority values. The sort_order field provides a stable
        # ordering contract so plugins created via the FE can reliably position themselves
        # relative to backend-defined tabs (e.g., "insert between Grading and Course Team").
        # Without explicit sort_order values, there's no deterministic way to interleave
        # backend tabs with plugin-inserted tabs, and tab order could shift based on
        # load/config timing.
        if access['staff']:
            tabs.extend([
                {
                    'tab_id': 'course_info',
                    'title': _('Course Info'),
                    'url': f'{mfe_base_url}/instructor/{str(course_key)}/course_info',
                    'sort_order': 10,
                },
                {
                    'tab_id': 'enrollments',
                    'title': _('Enrollments'),
                    'url': f'{mfe_base_url}/instructor/{str(course_key)}/enrollments',
                    'sort_order': 20,
                },
                {
                    "tab_id": "course_team",
                    "title": "Course Team",
                    "url": f'{mfe_base_url}/instructor/{str(course_key)}/course_team',
                    'sort_order': 30,
                },
                {
                    'tab_id': 'grading',
                    'title': _('Grading'),
                    'url': f'{mfe_base_url}/instructor/{str(course_key)}/grading',
                    'sort_order': 40,
                },
                {
                    'tab_id': 'cohorts',
                    'title': _('Cohorts'),
                    'url': f'{mfe_base_url}/instructor/{str(course_key)}/cohorts',
                    'sort_order': 90,
                },
            ])

        if access['staff'] and is_bulk_email_feature_enabled(course_key):
            tabs.append({
                'tab_id': 'bulk_email',
                'title': _('Bulk Email'),
                'url': f'{mfe_base_url}/instructor/{str(course_key)}/bulk_email',
                'sort_order': 100,
            })

        if access['instructor'] and is_enabled_for_course(course_key):
            tabs.append({
                'tab_id': 'date_extensions',
                'title': _('Date Extensions'),
                'url': f'{mfe_base_url}/instructor/{str(course_key)}/date_extensions',
                'sort_order': 50,
            })

        if access['data_researcher']:
            tabs.append({
                'tab_id': 'data_downloads',
                'title': _('Data Downloads'),
                'url': f'{mfe_base_url}/instructor/{str(course_key)}/data_downloads',
                'sort_order': 60,
            })

        openassessment_blocks = modulestore().get_items(
            course_key, qualifiers={'category': 'openassessment'}
        )
        # filter out orphaned openassessment blocks
        openassessment_blocks = [
            block for block in openassessment_blocks if block.parent is not None
        ]
        if len(openassessment_blocks) > 0 and access['staff']:
            tabs.append({
                'tab_id': 'open_responses',
                'title': _('Open Responses'),
                'url': f'{mfe_base_url}/instructor/{str(course_key)}/open_responses',
                'sort_order': 70,
            })

        # Note: This is hidden for all CCXs
        certs_enabled = CertificateGenerationConfiguration.current().enabled and not hasattr(course_key, 'ccx')
        certs_instructor_enabled = settings.FEATURES.get('ENABLE_CERTIFICATES_INSTRUCTOR_MANAGE', False)

        if certs_enabled and access['admin'] or (access['instructor'] and certs_instructor_enabled):
            tabs.append({
                'tab_id': 'certificates',
                'title': _('Certificates'),
                'url': f'{mfe_base_url}/instructor/{str(course_key)}/certificates',
                'sort_order': 80,
            })

        user_has_access = any([
            access['admin'],
            CourseStaffRole(course_key).has_user(request.user),
            access['instructor'],
        ])
        course_has_special_exams = course.enable_proctored_exams or course.enable_timed_exams
        can_see_special_exams = course_has_special_exams and user_has_access and settings.FEATURES.get(
            'ENABLE_SPECIAL_EXAMS', False)

        if can_see_special_exams:
            tabs.append({
                'tab_id': 'special_exams',
                'title': _('Special Exams'),
                'url': f'{mfe_base_url}/instructor/{str(course_key)}/special_exams',
                'sort_order': 110,
            })

        # We provide the tabs in a specific order based on how it was
        # historically presented in the frontend.  The frontend can use
        # this info or choose to ignore the ordering.
        tabs_order = [
            'course_info',
            'enrollments',
            'course_team',
            'grading',
            'date_extensions',
            'data_downloads',
            'open_responses',
            'certificates',
            'cohorts',
            'bulk_email',
            'special_exams',
        ]
        order_index = {tab: i for i, tab in enumerate(tabs_order)}
        tabs = sorted(tabs, key=lambda x: order_index.get(x['tab_id'], float("inf")))
        return tabs

    def get_course_id(self, data):
        """Get course ID as string."""
        return str(data['course'].id)

    def get_display_name(self, data):
        """Get course display name."""
        return data['course'].display_name

    def get_org(self, data):
        """Get organization identifier."""
        return data['course'].id.org

    def get_course_number(self, data):
        """Get course number."""
        return data['course'].id.course

    def get_course_run(self, data):
        """Get course run identifier"""
        course_id = data['course'].id
        return course_id.run if course_id.run is not None else ''

    def get_enrollment_start(self, data):
        """Get enrollment start date."""
        return data['course'].enrollment_start

    def get_enrollment_end(self, data):
        """Get enrollment end date."""
        return data['course'].enrollment_end

    def get_start(self, data):
        """Get course start date."""
        return data['course'].start

    def get_end(self, data):
        """Get course end date."""
        return data['course'].end

    def get_pacing(self, data):
        """Get course pacing type (self or instructor)."""
        return 'self' if data['course'].self_paced else 'instructor'

    def get_has_started(self, data):
        """Check if course has started."""
        return data['course'].has_started()

    def get_has_ended(self, data):
        """Check if course has ended."""
        return data['course'].has_ended()

    def get_total_enrollment(self, data):
        """Get total enrollment count."""
        total_enrollments = CourseEnrollment.objects.filter(
            course_id=data['course'].id,
            is_active=True
        ).count()
        return total_enrollments

    def get_enrollment_counts(self, data):
        """Get enrollment counts by mode."""
        course = data['course']
        total_enrollments = self.get_total_enrollment(data)
        enrollments_by_mode = CourseEnrollment.objects.filter(
            course_id=course.id,
            is_active=True
        ).values('mode').annotate(count=Count('mode'))

        by_mode = {item['mode']: item['count'] for item in enrollments_by_mode}
        by_mode['total'] = total_enrollments

        return by_mode

    def get_num_sections(self, data):
        """Get number of sections in the course."""
        course = data['course']
        return len(course.get_children()) if hasattr(course, 'get_children') else 0

    def get_permissions(self, data):
        """Get user permissions for the course."""
        user = data['user']
        course_key = data['course'].id
        return {
            'admin': user.is_staff,
            'instructor': CourseInstructorRole(course_key).has_user(user),
            'finance_admin': CourseFinanceAdminRole(course_key).has_user(user),
            'sales_admin': CourseSalesAdminRole(course_key).has_user(user),
            'staff': CourseStaffRole(course_key).has_user(user),
            'forum_admin': has_forum_access(user, course_key, FORUM_ROLE_ADMINISTRATOR),
            'data_researcher': user.has_perm(permissions.CAN_RESEARCH, course_key),
        }

    def get_grade_cutoffs(self, data):
        """
        Format grade cutoffs as a human-readable string.

        Args:
            data: Dictionary containing course object

        Returns:
            str: Formatted grade cutoffs (e.g., "A is 0.9, B is 0.8, C is 0.7")
        """
        course = data['course']
        if not hasattr(course, 'grading_policy') or not course.grading_policy:
            return ""

        grading_policy = course.grading_policy
        if 'GRADER' not in grading_policy:
            return ""

        grade_cutoffs = grading_policy.get('GRADE_CUTOFFS', {})
        if not grade_cutoffs:
            return ""

        # Sort by cutoff value descending
        sorted_cutoffs = sorted(grade_cutoffs.items(), key=lambda x: x[1], reverse=True)

        # Format as "A is 0.9, B is 0.8, ..."
        formatted = ", ".join([f"{grade} is {cutoff}" for grade, cutoff in sorted_cutoffs])
        return formatted

    def get_course_errors(self, data):
        """Get course validation errors from modulestore."""
        course = data['course']
        try:
            errors = modulestore().get_course_errors(course.id)
            course_errors = [(escape(str(error)), '') for (error, _) in errors]
        except (AttributeError, KeyError):
            course_errors = []
        return course_errors

    def get_studio_url(self, data):
        """Get Studio URL for the course."""
        return get_studio_url(data['course'], 'course')

    def get_disable_buttons(self, data):
        """Check if buttons should be disabled for large courses."""
        return not CourseEnrollment.objects.is_small_course(data['course'].id)

    def get_analytics_dashboard_message(self, data):
        """Get analytics dashboard availability message."""
        return get_analytics_dashboard_message(data['course'].id)


class InstructorTaskSerializer(serializers.Serializer):
    """Serializer for instructor task details."""
    task_id = serializers.UUIDField()
    task_type = serializers.CharField()
    task_state = serializers.ChoiceField(choices=["PENDING", "PROGRESS", "SUCCESS", "FAILURE", "REVOKED"])
    status = serializers.CharField()
    created = serializers.DateTimeField()
    duration_sec = serializers.CharField()
    task_message = serializers.CharField()
    requester = serializers.CharField()
    task_input = serializers.CharField()
    task_output = serializers.CharField(allow_null=True)


class InstructorTaskListSerializer(serializers.Serializer):
    tasks = InstructorTaskSerializer(many=True)


class BlockDueDateSerializerV2(serializers.Serializer):
    """
    Serializer for handling block due date updates for a specific student.
    Fields:
        block_id (str): The ID related to the block that needs the due date update.
        due_datetime (str): The new due date and time for the block.
        email_or_username (str): The email or username of the student whose access is being modified.
        reason (str): Reason why updating this.
    """
    block_id = serializers.CharField()
    due_datetime = serializers.CharField()
    email_or_username = serializers.CharField(
        max_length=255,
        help_text="Email or username of user to change access"
    )
    reason = serializers.CharField(required=False)

    def validate_email_or_username(self, value):
        """
        Validate that the email_or_username corresponds to an existing user.
        """
        try:
            user = get_student_from_identifier(value)
        except Exception as exc:
            raise serializers.ValidationError(
                _('Invalid learner identifier: {0}').format(value)
            ) from exc

        return user

    def validate_due_datetime(self, value):
        """
        Validate and parse the due_datetime string into a datetime object.
        """
        try:
            parsed_date = parse_datetime(value)
            return parsed_date
        except DashboardError as exc:
            raise serializers.ValidationError(
                _('The extension due date and time format is incorrect')
            ) from exc


class UnitExtensionSerializer(serializers.Serializer):
    """
    Serializer for unit extension data.

    This serializer formats the data returned by get_overrides_for_course
    for the paginated list API endpoint.
    """
    username = serializers.CharField(
        help_text="Username of the learner who has the extension"
    )
    full_name = serializers.CharField(
        help_text="Full name of the learner"
    )
    email = serializers.EmailField(
        help_text="Email address of the learner"
    )
    unit_title = serializers.CharField(
        help_text="Display name or URL of the unit"
    )
    unit_location = serializers.CharField(
        help_text="Block location/ID of the unit"
    )
    extended_due_date = serializers.DateTimeField(
        help_text="The extended due date for the learner"
    )


class ORASerializer(serializers.Serializer):
    """Serializer for Open Response Assessments (ORAs) in a course."""

    block_id = serializers.CharField(source="id")
    unit_name = serializers.CharField(source="parent_name")
    display_name = serializers.CharField(source="name")

    # Metrics fields
    total_responses = serializers.IntegerField(source="total")
    training = serializers.IntegerField()
    peer = serializers.IntegerField()
    self = serializers.IntegerField()
    waiting = serializers.IntegerField()
    staff = serializers.IntegerField()
    final_grade_received = serializers.IntegerField(source="done")
    staff_ora_grading_url = serializers.URLField(allow_null=True)


class ORASummarySerializer(serializers.Serializer):
    """
    Aggregated ORA statistics for a course
    """
    total_units = serializers.IntegerField()
    total_assessments = serializers.IntegerField()
    total_responses = serializers.IntegerField()
    training = serializers.IntegerField()
    peer = serializers.IntegerField()
    self = serializers.IntegerField()
    waiting = serializers.IntegerField()
    staff = serializers.IntegerField()
    final_grade_received = serializers.IntegerField()


class LearnerSerializer(serializers.Serializer):
    """
    Serializer for learner information.

    Provides comprehensive learner data including profile, enrollment status,
    and current progress in a course.
    """
    username = serializers.CharField(
        help_text="Learner's username"
    )
    email = serializers.EmailField(
        help_text="Learner's email address"
    )
    first_name = serializers.CharField(
        help_text="Learner's first name"
    )
    last_name = serializers.CharField(
        help_text="Learner's last name"
    )
    progress_url = serializers.URLField(
        allow_null=True,
        required=False,
        help_text="URL to learner's progress page"
    )
    gradebook_url = serializers.URLField(
        allow_null=True,
        required=False,
        help_text="URL to learner's gradebook view"
    )
    current_score = serializers.DictField(
        allow_null=True,
        required=False,
        help_text="Current score information with 'score' and 'total' fields"
    )
    attempts = serializers.DictField(
        allow_null=True,
        required=False,
        help_text="Attempts information with 'current' and 'total' fields"
    )


class ProblemSerializer(serializers.Serializer):
    """
    Serializer for problem metadata and location.

    Provides problem information including display name and course hierarchy.
    """
    id = serializers.CharField(
        help_text="Problem usage key"
    )
    name = serializers.CharField(
        help_text="Problem display name"
    )
    breadcrumbs = serializers.ListField(
        child=serializers.DictField(),
        help_text="Course hierarchy breadcrumbs showing problem location"
    )


class TaskStatusSerializer(serializers.Serializer):
    """
    Serializer for background task status.

    Provides status and progress information for asynchronous operations.
    """
    task_id = serializers.CharField(
        help_text="Task identifier"
    )
    state = serializers.ChoiceField(
        choices=['pending', 'running', 'completed', 'failed'],
        help_text="Current state of the task"
    )
    progress = serializers.DictField(
        allow_null=True,
        required=False,
        help_text="Progress information with 'current' and 'total' fields"
    )
    result = serializers.DictField(
        allow_null=True,
        required=False,
        help_text="Task result (present when state is 'completed')"
    )
    error = serializers.DictField(
        allow_null=True,
        required=False,
        help_text="Error information (present when state is 'failed')"
    )
    created_at = serializers.DateTimeField(
        help_text="Task creation timestamp"
    )
    updated_at = serializers.DateTimeField(
        help_text="Last update timestamp"
    )
