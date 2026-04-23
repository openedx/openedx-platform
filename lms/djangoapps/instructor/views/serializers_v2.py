"""
Serializers for Instructor API v2.

These serializers handle data validation and business logic for instructor dashboard endpoints.
Following REST best practices, serializers encapsulate most of the data processing logic.
"""

import logging
from urllib.parse import urlparse

from django.conf import settings
from django.utils.html import escape
from django.utils.translation import gettext as _
from edx_when.api import is_enabled_for_course
from rest_framework import serializers

from common.djangoapps.course_modes.models import CourseMode
from common.djangoapps.student.models import CourseEnrollment
from common.djangoapps.student.roles import (
    CourseFinanceAdminRole,
    CourseInstructorRole,
    CourseSalesAdminRole,
    CourseStaffRole,
)
from lms.djangoapps.bulk_email.api import is_bulk_email_feature_enabled
from lms.djangoapps.certificates.models import CertificateGenerationConfiguration
from lms.djangoapps.courseware.access import has_access
from lms.djangoapps.courseware.courses import get_studio_url
from lms.djangoapps.discussion.django_comment_client.utils import has_forum_access
from lms.djangoapps.grades.api import is_writable_gradebook_enabled
from lms.djangoapps.instructor import permissions
from lms.djangoapps.instructor.access import FORUM_ROLES, ROLES
from lms.djangoapps.instructor.views.instructor_dashboard import get_analytics_dashboard_message
from openedx.core.djangoapps.django_comment_common.models import FORUM_ROLE_ADMINISTRATOR
from xmodule.modulestore.django import modulestore

from .tools import DashboardError, get_student_from_identifier, parse_datetime

log = logging.getLogger(__name__)


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
    learner_count = serializers.SerializerMethodField(
        help_text="Number of enrolled learners (excludes staff and admins)"
    )
    staff_count = serializers.SerializerMethodField(help_text="Number of enrolled staff and admins")
    enrollment_counts = serializers.SerializerMethodField(help_text="Enrollment count breakdown by mode")
    num_sections = serializers.SerializerMethodField(help_text="Number of sections/chapters in the course")
    grade_cutoffs = serializers.SerializerMethodField(help_text="Formatted string of grade cutoffs")
    course_errors = serializers.SerializerMethodField(help_text="List of course validation errors from modulestore")
    studio_url = serializers.SerializerMethodField(help_text="URL to view/edit course in Studio")
    gradebook_url = serializers.SerializerMethodField(
        help_text="URL to the MFE gradebook for the course (null if not configured)"
    )
    studio_grading_url = serializers.SerializerMethodField(
        help_text="URL to the Studio grading settings page for the course (null if not configured)"
    )
    permissions = serializers.SerializerMethodField(help_text="User permissions for instructor dashboard features")
    tabs = serializers.SerializerMethodField(help_text="List of course tabs with configuration and display information")
    disable_buttons = serializers.SerializerMethodField(
        help_text="Whether to disable certain bulk action buttons due to large course size"
    )
    analytics_dashboard_message = serializers.SerializerMethodField(
        help_text="Message about analytics dashboard availability"
    )

    @staticmethod
    def _build_tab_url(setting_name, *path_parts, strip_url=True):
        """
        Build a tab URL from a Django setting and path parts.

        Retrieves the base URL from `setting_name`, optionally strips the protocol and host,
        then joins the provided path parts (stripping their leading/trailing
        slashes) with `/` separators — behaving like ``os.path.join`` for URLs.

        Logs a warning and falls back to a relative URL if the setting is unset.

        Args:
            setting_name: Django setting name containing the base URL
            *path_parts: Path components to append to the base URL
            strip_url: If True, strips protocol/host and uses only the path component.
                      If False, uses the full URL. Defaults to True.

        Example:

            _build_tab_url('INSTRUCTOR_MICROFRONTEND_URL', course_key, 'grading')
            # => '/instructor-dashboard/course-v1:.../grading' (with strip_url=True)

            _build_tab_url('COMMUNICATIONS_MICROFRONTEND_URL', 'courses', course_key, 'bulk_email', strip_url=False)
            # => 'http://localhost:1984/communications/courses/course-v1:.../bulk_email'
        """
        base_url = getattr(settings, setting_name, None)
        if base_url is None:
            log.warning("%s is not configured.", setting_name)
            base_part = ""
        elif strip_url and base_url:
            # Extract only the path component from the URL
            base_part = urlparse(base_url).path
        else:
            # Use the full URL as-is
            base_part = base_url

        parts = [base_part.rstrip("/")] + [str(part).strip("/") for part in path_parts]
        return "/".join(parts)

    def get_tabs(self, data):
        """Get serialized course tabs."""
        request = data['request']
        course = data['course']
        course_key = course.id

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
                    'url': self._build_tab_url(
                        'INSTRUCTOR_MICROFRONTEND_URL',
                        course_key,
                        'course_info'
                    ),
                    'sort_order': 10,
                },
                {
                    'tab_id': 'enrollments',
                    'title': _('Enrollments'),
                    'url': self._build_tab_url(
                        'INSTRUCTOR_MICROFRONTEND_URL',
                        course_key,
                        'enrollments'
                    ),
                    'sort_order': 20,
                },
                {
                    'tab_id': 'course_team',
                    'title': _('Course Team'),
                    'url': self._build_tab_url(
                        'INSTRUCTOR_MICROFRONTEND_URL',
                        course_key,
                        'course_team'
                    ),
                    'sort_order': 30,
                },
                {
                    'tab_id': 'grading',
                    'title': _('Grading'),
                    'url': self._build_tab_url(
                        'INSTRUCTOR_MICROFRONTEND_URL',
                        course_key,
                        'grading'
                    ),
                    'sort_order': 40,
                },
                {
                    'tab_id': 'cohorts',
                    'title': _('Cohorts'),
                    'url': self._build_tab_url(
                        'INSTRUCTOR_MICROFRONTEND_URL',
                        course_key,
                        'cohorts'
                    ),
                    'sort_order': 90,
                },
            ])

        if access['staff'] and is_bulk_email_feature_enabled(course_key):
            tabs.append(
                {
                    "tab_id": "bulk_email",
                    "title": _("Bulk Email"),
                    "url": self._build_tab_url(
                        "COMMUNICATIONS_MICROFRONTEND_URL", "courses", course_key, "bulk_email", strip_url=False
                    ),
                    "sort_order": 100,
                }
            )

        if access['instructor'] and is_enabled_for_course(course_key):
            tabs.append({
                'tab_id': 'date_extensions',
                'title': _('Date Extensions'),
                'url': self._build_tab_url(
                    'INSTRUCTOR_MICROFRONTEND_URL',
                    course_key,
                    'date_extensions'
                ),
                'sort_order': 50,
            })

        if access['data_researcher']:
            tabs.append({
                'tab_id': 'data_downloads',
                'title': _('Data Downloads'),
                'url': self._build_tab_url(
                    'INSTRUCTOR_MICROFRONTEND_URL',
                    course_key,
                    'data_downloads'
                ),
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
                'url': self._build_tab_url(
                    'INSTRUCTOR_MICROFRONTEND_URL',
                    course_key,
                    'open_responses'
                ),
                'sort_order': 70,
            })

        # Note: This is hidden for all CCXs
        certs_enabled = CertificateGenerationConfiguration.current().enabled and not hasattr(course_key, 'ccx')
        certs_instructor_enabled = settings.FEATURES.get('ENABLE_CERTIFICATES_INSTRUCTOR_MANAGE', False)

        if certs_enabled and access['admin'] or (access['instructor'] and certs_instructor_enabled):
            tabs.append({
                'tab_id': 'certificates',
                'title': _('Certificates'),
                'url': self._build_tab_url(
                    'INSTRUCTOR_MICROFRONTEND_URL',
                    course_key,
                    'certificates'
                ),
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
                'url': self._build_tab_url(
                    'INSTRUCTOR_MICROFRONTEND_URL',
                    course_key,
                    'special_exams'
                ),
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
        return self.get_enrollment_counts(data)['total']

    def get_learner_count(self, data):
        """Get enrollment count excluding staff and admins."""
        return CourseEnrollment.objects.num_enrolled_in_exclude_admins(data['course'].id)

    def get_staff_count(self, data):
        """Get enrollment count for staff and admins only."""
        return self.get_total_enrollment(data) - self.get_learner_count(data)

    def get_enrollment_counts(self, data):
        """Get enrollment counts for all configured course modes."""
        course_id = data['course'].id
        counts = CourseEnrollment.objects.enrollment_counts(course_id)
        configured_modes = CourseMode.modes_for_course(course_id)
        result = {mode.slug: counts[mode.slug] for mode in configured_modes}
        result['total'] = counts['total']
        return result

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

    def get_gradebook_url(self, data):
        """Get MFE gradebook URL for the course."""
        course_key = data['course'].id
        if is_writable_gradebook_enabled(course_key) and settings.WRITABLE_GRADEBOOK_URL:
            return f'{settings.WRITABLE_GRADEBOOK_URL}/gradebook/{course_key}'
        return None

    def get_studio_grading_url(self, data):
        """Get Studio MFE grading settings URL for the course."""
        course_key = data['course'].id
        mfe_base_url = getattr(settings, 'COURSE_AUTHORING_MICROFRONTEND_URL', None)
        if mfe_base_url:
            return f'{mfe_base_url}/course/{course_key}/settings/grading'
        return None

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


class IssuedCertificateSerializer(serializers.Serializer):
    """
    Serializer for issued certificates with allowlist and invalidation information.
    Accepts GeneratedCertificate instances and pulls related data from context.
    """
    username = serializers.CharField(source='user.username', help_text="Username of the learner")
    email = serializers.EmailField(source='user.email', help_text="Email address of the learner")
    enrollment_track = serializers.SerializerMethodField(
        allow_null=True,
        help_text="Enrollment track/mode (e.g., verified, audit)"
    )
    certificate_status = serializers.CharField(
        source='status',
        help_text="Certificate status (e.g., downloadable, notpassing)"
    )
    special_case = serializers.SerializerMethodField(
        allow_null=True,
        help_text="Special case type (Exception or Invalidation)"
    )
    exception_granted = serializers.SerializerMethodField(
        allow_null=True,
        help_text="Date when exception was granted in ISO 8601 format"
    )
    exception_notes = serializers.SerializerMethodField(
        allow_null=True,
        help_text="Notes about the exception"
    )
    invalidated_by = serializers.SerializerMethodField(
        allow_null=True,
        help_text="Email of user who invalidated the certificate"
    )
    invalidation_date = serializers.SerializerMethodField(
        allow_null=True,
        help_text="Date when certificate was invalidated in ISO 8601 format"
    )

    def get_enrollment_track(self, obj):
        """Get enrollment track from context."""
        enrollment_dict = self.context.get('enrollment_dict', {})
        return enrollment_dict.get(obj.user_id)

    def get_special_case(self, obj):
        """Determine special case from allowlist and invalidation data in context."""
        allowlist_dict = self.context.get('allowlist_dict', {})
        invalidation_dict = self.context.get('invalidation_dict', {})

        if obj.user_id in allowlist_dict:
            return "Exception"
        elif obj.user_id in invalidation_dict:
            return "Invalidation"
        return None

    def get_exception_granted(self, obj):
        """Get exception granted date from allowlist data in context."""
        allowlist_dict = self.context.get('allowlist_dict', {})
        allowlist_info = allowlist_dict.get(obj.user_id)
        return allowlist_info['created'] if allowlist_info else None

    def get_exception_notes(self, obj):
        """Get exception notes from allowlist data in context."""
        allowlist_dict = self.context.get('allowlist_dict', {})
        allowlist_info = allowlist_dict.get(obj.user_id)
        return allowlist_info['notes'] if allowlist_info else None

    def get_invalidated_by(self, obj):
        """Get invalidated by email from invalidation data in context."""
        invalidation_dict = self.context.get('invalidation_dict', {})
        invalidation_info = invalidation_dict.get(obj.user_id)
        return invalidation_info['invalidated_by'] if invalidation_info else None

    def get_invalidation_date(self, obj):
        """Get invalidation date from invalidation data in context."""
        invalidation_dict = self.context.get('invalidation_dict', {})
        invalidation_info = invalidation_dict.get(obj.user_id)
        return invalidation_info['created'] if invalidation_info else None


class CertificateGenerationHistorySerializer(serializers.Serializer):
    """
    Serializer for certificate generation history.
    Accepts CertificateGenerationHistory model instances.
    """
    task_name = serializers.SerializerMethodField(
        help_text="Task name (Generated or Regenerated)"
    )
    date = serializers.DateTimeField(
        source='created',
        help_text="Date when the task was created in ISO 8601 format"
    )
    details = serializers.SerializerMethodField(
        help_text="Details about the certificate generation (e.g., 'audit not passing states', 'For exceptions')"
    )

    def get_task_name(self, obj):
        """Determine task name based on whether it's a regeneration."""
        return "Regenerated" if obj.is_regeneration else "Generated"

    def get_details(self, obj):
        """Get details about what was generated/regenerated."""
        return str(obj.get_certificate_generation_candidates())


class RegenerateCertificatesSerializer(serializers.Serializer):
    """
    Serializer for regenerating certificates request.
    """
    statuses = serializers.ListField(
        child=serializers.ChoiceField(
            choices=[
                'deleted', 'deleting', 'downloadable', 'error', 'generating',
                'notpassing', 'restricted', 'unavailable', 'auditing',
                'audit_passing', 'audit_notpassing', 'honor_passing',
                'unverified', 'invalidated', 'requesting'
            ]
        ),
        required=False,
        help_text="Certificate statuses to regenerate"
    )
    student_set = serializers.ChoiceField(
        choices=['all', 'allowlisted'],
        required=False,
        default='all',
        help_text="Student set filter"
    )


class CourseEnrollmentSerializerV2(serializers.Serializer):
    """
    Serializer for course enrollment data.

    Serializes CourseEnrollment instances with derived fields for
    the user's full name and beta tester status.
    """
    username = serializers.CharField(source='user.username')
    full_name = serializers.SerializerMethodField()
    email = serializers.EmailField(source='user.email')
    mode = serializers.CharField()
    is_beta_tester = serializers.SerializerMethodField()

    def get_full_name(self, enrollment):
        """Get the user's full name from their profile."""
        user = enrollment.user
        profile = getattr(user, 'profile', None)
        return profile.name if profile else ''

    def get_is_beta_tester(self, enrollment):
        """Check if the user is a beta tester for this course."""
        beta_tester_ids = self.context.get('beta_tester_ids', set())
        return enrollment.user_id in beta_tester_ids


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
    full_name = serializers.CharField(
        help_text="Learner's full name from their Open edX profile"
    )
    progress_url = serializers.CharField(
        allow_null=True,
        required=False,
        help_text="URL to learner's progress page"
    )
    is_enrolled = serializers.BooleanField(
        help_text="Whether the learner has an active enrollment in the course"
    )


class GraderSerializer(serializers.Serializer):
    """Serializer for a single grader configuration entry."""
    type = serializers.CharField(
        help_text="Assignment type (e.g. Homework, Lab, Midterm Exam)"
    )
    short_label = serializers.CharField(
        required=False,
        allow_null=True,
        help_text="Short label used when displaying assignment names"
    )
    min_count = serializers.IntegerField(
        help_text="Minimum number of assignments counted in this category"
    )
    drop_count = serializers.IntegerField(
        help_text="Number of lowest scores dropped from this category"
    )
    weight = serializers.FloatField(
        help_text="Weight of this assignment type in the final grade (0.0 to 1.0)"
    )


class GradingConfigSerializer(serializers.Serializer):
    """
    Serializer for course grading configuration.

    Returns structured grading policy data including assignment type weights
    and grade cutoff thresholds.
    """
    graders = GraderSerializer(
        many=True,
        help_text="List of grader configurations by assignment type"
    )
    grade_cutoffs = serializers.DictField(
        child=serializers.FloatField(),
        help_text="Grade cutoffs mapping letter grades to minimum score thresholds (0.0 to 1.0)"
    )


class ProblemSerializer(serializers.Serializer):
    """
    Serializer for problem metadata and location.

    Provides problem information including display name and course hierarchy.
    Optionally includes learner-specific score and attempt data when a learner
    query parameter is provided.
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
    current_score = serializers.DictField(
        allow_null=True,
        required=False,
        help_text="Learner's current score with 'score' and 'total' fields. Null if no learner specified."
    )
    attempts = serializers.DictField(
        allow_null=True,
        required=False,
        help_text="Learner's attempt data with 'current' and 'total' (max) fields. Null if no learner specified."
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


class EnrollmentModifyRequestSerializerV2(serializers.Serializer):
    """Validates request body for enrolling/unenrolling one or more learners."""
    identifier = serializers.ListField(
        child=serializers.CharField(max_length=255, allow_blank=False),
        allow_empty=False,
        help_text="List of email addresses or usernames of learners to enroll/unenroll.",
    )
    action = serializers.ChoiceField(
        choices=('enroll', 'unenroll'),
        help_text="The enrollment action to perform: 'enroll' or 'unenroll'.",
    )
    auto_enroll = serializers.BooleanField(
        default=False,
        help_text="Whether to auto-enroll in the verified track (enroll action only).",
    )
    email_students = serializers.BooleanField(
        default=False,
        help_text="Whether to send an email notification.",
    )
    reason = serializers.CharField(
        required=False,
        allow_blank=True,
        default='',
        help_text="Reason for the change (for audit trail).",
    )


class EnrollmentStateSerializerV2(serializers.Serializer):
    """Documents the before/after enrollment state shape (mirrors EmailEnrollmentState.to_dict)."""
    user = serializers.BooleanField()
    enrollment = serializers.BooleanField()
    allowed = serializers.BooleanField()
    auto_enroll = serializers.BooleanField()


class EnrollmentModifyResultSerializerV2(serializers.Serializer):
    """Documents the per-identifier result shape for enrollment modifications (mirrors v1)."""
    identifier = serializers.CharField()
    before = EnrollmentStateSerializerV2(required=False)
    after = EnrollmentStateSerializerV2(required=False)
    invalid_identifier = serializers.BooleanField(required=False)
    error = serializers.BooleanField(required=False)


class EnrollmentModifyResponseSerializerV2(serializers.Serializer):
    """Documents the response shape for the bulk enroll/unenroll endpoint (mirrors v1)."""
    action = serializers.CharField()
    auto_enroll = serializers.BooleanField()
    results = EnrollmentModifyResultSerializerV2(many=True)


class BetaTesterModifyRequestSerializerV2(serializers.Serializer):
    """Validates request body for adding/removing one or more beta testers."""
    identifier = serializers.ListField(
        child=serializers.CharField(max_length=255, allow_blank=False),
        allow_empty=False,
        help_text="List of email addresses or usernames of learners to add/remove as beta testers.",
    )
    action = serializers.ChoiceField(
        choices=('add', 'remove'),
        help_text="The beta tester action to perform: 'add' or 'remove'.",
    )
    email_students = serializers.BooleanField(
        default=False,
        help_text="Whether to send an email notification.",
    )
    auto_enroll = serializers.BooleanField(
        default=False,
        help_text="Whether to auto-enroll the user in the course (add action only).",
    )


class BetaTesterModifyResultSerializerV2(serializers.Serializer):
    """Documents the per-identifier result shape for beta tester modifications (mirrors v1)."""
    identifier = serializers.CharField()
    error = serializers.BooleanField()
    user_does_not_exist = serializers.BooleanField()
    is_active = serializers.BooleanField(allow_null=True)


class BetaTesterModifyResponseSerializerV2(serializers.Serializer):
    """Documents the response shape for the bulk beta tester add/remove endpoint (mirrors v1)."""
    action = serializers.CharField()
    results = BetaTesterModifyResultSerializerV2(many=True)


class CourseTeamModifySerializer(serializers.Serializer):
    """Input serializer for granting or revoking a course team role."""
    identifiers = serializers.ListField(
        child=serializers.CharField(max_length=255, allow_blank=False),
        allow_empty=False,
        help_text="List of usernames or emails of users to modify"
    )
    role = serializers.ChoiceField(
        choices=list(ROLES.keys()) + list(FORUM_ROLES),
        help_text="The role to grant or revoke (course access role or forum role)"
    )
    action = serializers.ChoiceField(
        choices=['allow', 'revoke'],
        help_text="Whether to grant ('allow') or revoke ('revoke') the role"
    )


class CourseTeamRevokeSerializer(serializers.Serializer):
    """Input serializer for revoking course team roles."""
    roles = serializers.ListField(
        child=serializers.ChoiceField(choices=list(ROLES.keys()) + list(FORUM_ROLES)),
        allow_empty=False,
        help_text="One or more roles to revoke (course access role or forum role)"
    )
