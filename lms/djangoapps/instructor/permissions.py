"""
Permissions for the instructor dashboard and associated actions.

This module defines permissions for various instructor dashboard actions and provides
permission classes for REST framework views.
"""
from bridgekeeper import perms
from bridgekeeper.rules import is_staff
from opaque_keys.edx.keys import CourseKey
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import BasePermission

from lms.djangoapps.courseware.access import has_access
from lms.djangoapps.courseware.rules import HasAccessRule, HasRolesRule
from lms.djangoapps.discussion.django_comment_client.utils import has_forum_access
from openedx.core.djangoapps.django_comment_common.models import FORUM_ROLE_ADMINISTRATOR
from openedx.core.lib.courses import get_course_by_id

# ============================================================================
# PERMISSION CONSTANTS
# ============================================================================
# Certificate Management Permissions
ENABLE_CERTIFICATE_GENERATION = 'instructor.enable_certificate_generation'
GENERATE_CERTIFICATE_EXCEPTIONS = 'instructor.generate_certificate_exceptions'
GENERATE_BULK_CERTIFICATE_EXCEPTIONS = 'instructor.generate_bulk_certificate_exceptions'
START_CERTIFICATE_GENERATION = 'instructor.start_certificate_generation'
START_CERTIFICATE_REGENERATION = 'instructor.start_certificate_regeneration'
CERTIFICATE_EXCEPTION_VIEW = 'instructor.certificate_exception_view'
CERTIFICATE_INVALIDATION_VIEW = 'instructor.certificate_invalidation_view'
VIEW_ISSUED_CERTIFICATES = 'instructor.view_issued_certificates'

# Enrollment and Student Management Permissions
CAN_ENROLL = 'instructor.enroll'
CAN_BETATEST = 'instructor.enroll_beta'
ENROLLMENT_REPORT = 'instructor.enrollment_report'
VIEW_ENROLLMENTS = 'instructor.view_enrollments'
VIEW_REGISTRATION = 'instructor.view_registration'
ASSIGN_TO_COHORTS = 'instructor.assign_to_cohorts'
ALLOW_STUDENT_TO_BYPASS_ENTRANCE_EXAM = 'instructor.allow_student_to_bypass_entrance_exam'
GIVE_STUDENT_EXTENSION = 'instructor.give_student_extension'

# Course Access and Roles Permissions
EDIT_COURSE_ACCESS = 'instructor.edit_course_access'
EDIT_FORUM_ROLES = 'instructor.edit_forum_roles'
VIEW_FORUM_MEMBERS = 'instructor.view_forum_members'

# Exam and Grading Permissions
EXAM_RESULTS = 'instructor.view_exam_results'
OVERRIDE_GRADES = 'instructor.override_grades'
RESCORE_EXAMS = 'instructor.rescore_exams'

# Data and Research Permissions
CAN_RESEARCH = 'instructor.research'
VIEW_COUPONS = 'instructor.view_coupons'
EDIT_INVOICE_VALIDATION = 'instructor.edit_invoice_validation'

# Communication Permissions
EMAIL = 'instructor.email'

# Dashboard and Task Permissions
VIEW_DASHBOARD = 'instructor.dashboard'
SHOW_TASKS = 'instructor.show_tasks'

# ============================================================================
# PERMISSION RULE ASSIGNMENTS
# ============================================================================
# Define reusable predicates
is_course_staff = HasAccessRule('staff')
is_instructor = HasAccessRule('instructor')
is_data_researcher = HasRolesRule('data_researcher')

# --- Basic Access ---
perms.update({
    ALLOW_STUDENT_TO_BYPASS_ENTRANCE_EXAM: is_course_staff,
    ASSIGN_TO_COHORTS: is_course_staff,
    EDIT_COURSE_ACCESS: is_instructor,
    EDIT_FORUM_ROLES: is_course_staff,
    EDIT_INVOICE_VALIDATION: is_course_staff,
    GIVE_STUDENT_EXTENSION: is_course_staff,
    CAN_ENROLL: is_course_staff,
    CAN_BETATEST: is_instructor,
    EMAIL: is_course_staff,
    EXAM_RESULTS: is_course_staff,
    OVERRIDE_GRADES: is_course_staff,
    VIEW_REGISTRATION: is_course_staff,
    VIEW_ENROLLMENTS: is_course_staff,
    VIEW_FORUM_MEMBERS: is_course_staff,
})

# --- Certificate Management ---
certificate_rule = is_staff | is_instructor
perms.update({
    ENABLE_CERTIFICATE_GENERATION: certificate_rule,
    GENERATE_CERTIFICATE_EXCEPTIONS: certificate_rule,
    GENERATE_BULK_CERTIFICATE_EXCEPTIONS: certificate_rule,
    START_CERTIFICATE_GENERATION: certificate_rule,
    START_CERTIFICATE_REGENERATION: certificate_rule,
    CERTIFICATE_EXCEPTION_VIEW: certificate_rule,
    CERTIFICATE_INVALIDATION_VIEW: certificate_rule,
})

# --- Research & Data Access ---
# Research permissions require either:
# - Global staff access, OR
# - Data researcher role
# This ensures consistent access control for all research-related operations
perms.update({
    CAN_RESEARCH: is_staff | is_data_researcher,
    VIEW_ISSUED_CERTIFICATES: is_staff | is_data_researcher,
    ENROLLMENT_REPORT: is_staff | is_data_researcher,
    VIEW_COUPONS: is_staff | is_data_researcher,
    SHOW_TASKS: is_staff | is_data_researcher,
})

# --- Grade Management ---
perms[RESCORE_EXAMS] = is_instructor

# --- Dashboard ---
dashboard_rule = (
    HasRolesRule('staff', 'instructor', 'data_researcher')
    | is_course_staff
    | is_instructor
)
perms[VIEW_DASHBOARD] = dashboard_rule


class InstructorPermission(BasePermission):
    """Generic permissions"""
    def has_permission(self, request, view):
        course = get_course_by_id(CourseKey.from_string(view.kwargs.get('course_id')))
        permission = getattr(view, 'permission_name', None)
        return request.user.has_perm(permission, course)


class ForumAdminRequiresInstructorAccess(BasePermission):
    """
    default roles require either (staff & forum admin) or (instructor)
    User should be forum-admin and staff to access this endpoint.

    But if request rolename is  FORUM_ROLE_ADMINISTRATOR, then user must also have
    instructor-level access to proceed.
    """
    def has_permission(self, request, view):
        """
        Permission class for forum endpoints.

        Only allow if:
        - User is an instructor, OR
        - User is staff AND forum admin.

        Special case:
        - If the action relates to forum admin (FORUM_ROLE_ADMINISTRATOR), user must be instructor.
       """
        rolename = request.data.get('rolename')
        course_id = view.kwargs.get('course_id')
        course = get_course_by_id(CourseKey.from_string(course_id))

        has_instructor_access = has_access(request.user, 'instructor', course)
        has_forum_admin = has_forum_access(
            request.user, course_id, FORUM_ROLE_ADMINISTRATOR
        )

        # Special case first: if role is FORUM_ROLE_ADMINISTRATOR
        if rolename == FORUM_ROLE_ADMINISTRATOR:
            if has_instructor_access:
                return True
            raise PermissionDenied("Operation requires instructor access.")

        # default roles require either (staff & forum admin) or (instructor)
        if has_instructor_access or has_forum_admin:
            return True

        raise PermissionDenied("Operation requires staff & forum admin or instructor access")
