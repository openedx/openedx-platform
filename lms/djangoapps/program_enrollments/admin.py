"""
Admin tool for the Program Enrollments models
"""
from django.contrib import admin
from django.urls import reverse
from django.utils.html import format_html

from lms.djangoapps.program_enrollments.models import (
    CourseAccessRoleAssignment,
    ProgramCourseEnrollment,
    ProgramEnrollment,
)


class ProgramEnrollmentAdmin(admin.ModelAdmin):
    """
    Admin tool for the ProgramEnrollment model
    """
    # Config for instance listing.
    list_display = (
        'id',
        'status',
        'user',
        'external_user_key',
        'program_uuid',
        'curriculum_uuid',
    )
    list_filter = ('status',)
    search_fields = ('user__username', 'external_user_key', 'program_uuid')

    # Config for instance editor.
    raw_id_fields = ('user',)


def _pce_pe_id(pce):
    """
    Generate a link to edit program enrollment, with ID and status in link text.
    """
    pe = pce.program_enrollment
    if not pe:
        return None
    link_url = reverse(
        "admin:program_enrollments_programenrollment_change",
        args=[pe.id],
    )
    link_text = "id={pe.id:05} ({pe.status})".format(pe=pe)
    return format_html("<a href={}>{}</a>", link_url, link_text)


def _pce_pe_user(pce):
    return pce.program_enrollment.user


def _pce_pe_external_user_key(pce):
    return pce.program_enrollment.external_user_key


def _pce_pe_program_uuid(pce):
    return pce.program_enrollment.program_uuid


def _pce_ce(pce):
    """
    Generate text for course enrollment, including ID and is_active value.
    """
    enrollment = pce.course_enrollment
    if not enrollment:
        return None
    active_string = "Active" if enrollment.is_active else "Inactive"
    return "id={enrollment.id:09} ({active_string})".format(
        enrollment=enrollment, active_string=active_string
    )

_pce_pe_id.short_description = "Program Enrollment"
_pce_pe_user.short_description = "Pgm Enrollment: User"
_pce_pe_external_user_key.short_description = "Pgm Enrollment: Ext User Key"
_pce_pe_program_uuid.short_description = "Pgm Enrollment: Pgm UUID"
_pce_ce.short_description = "Course Enrollment"


class ProgramCourseEnrollmentAdmin(admin.ModelAdmin):
    """
    Admin tool for the ProgramCourseEnrollment model
    """
    # Config for instance listing.
    list_display = (
        'id',
        'status',
        _pce_pe_id,
        _pce_pe_user,
        _pce_pe_external_user_key,
        _pce_pe_program_uuid,
        _pce_ce,
        'course_key',
    )
    list_filter = ('status', 'course_key')
    search_fields = (
        'program_enrollment__user__username',
        'program_enrollment__external_user_key',
        'program_enrollment__program_uuid',
        'course_key',
    )

    # Config for instance editor.
    raw_id_fields = ('program_enrollment', 'course_enrollment')


def _pending_role_assignment_enrollment_id(pending_role_assignment):
    """
    Generate a link to edit enrollment, with ID in link text.
    """
    pce = pending_role_assignment.enrollment
    if not pce:
        return None
    link_url = reverse(
        "admin:program_enrollments_programcourseenrollment_change",
        args=[pce.id],
    )
    link_text = f"id={pce.id:05}"
    return format_html("<a href={}>{}</a>", link_url, link_text)


def _pending_role_assignment_external_user_key(pending_role_assignment):
    """
    Generate the external user key for a pending role assignment
    """
    pce = pending_role_assignment.enrollment
    return _pce_pe_external_user_key(pce)

_pending_role_assignment_enrollment_id.short_description = "Program Course Enrollment"
_pending_role_assignment_external_user_key.short_description = "Pgm Enrollment: Ext User Key"


class CourseAccessRoleAssignmentAdmin(admin.ModelAdmin):
    """
    Admin tool for the CourseAccessRoleAssignment model
    """
    list_display = (
        'id',
        'role',
        _pending_role_assignment_enrollment_id,
        _pending_role_assignment_external_user_key
    )
    list_filter = ('role',)
    raw_id_fields = ('enrollment',)

admin.site.register(ProgramEnrollment, ProgramEnrollmentAdmin)
admin.site.register(ProgramCourseEnrollment, ProgramCourseEnrollmentAdmin)
admin.site.register(CourseAccessRoleAssignment, CourseAccessRoleAssignmentAdmin)
