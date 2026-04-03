""" Django admins for support models """
from django import forms
from django.contrib import admin

from lms.djangoapps.support.models import CourseResetAudit, CourseResetCourseOptIn
from openedx.core.lib.courses import clean_course_id


class CourseResetCourseOptInAdminForm(forms.ModelForm):
    """ Form for the CourseResetCourseOptIn Django admin page """
    class Meta:
        model = CourseResetCourseOptIn
        fields = ['course_id', 'active']

    def clean_course_id(self):
        return clean_course_id(self)


class CourseResetCourseOptInAdmin(admin.ModelAdmin):
    """ Django admin for CourseResetCourseOptIn model """
    form = CourseResetCourseOptInAdminForm
    list_display = ['course_id', 'active', 'created', 'modified']

    def get_readonly_fields(self, request, obj=None):
        """
        Ensure that 'course_id' cannot be edited after creation.
        """
        if obj:
            return ['course_id']
        return []


class CourseResetAuditAdmin(admin.ModelAdmin):
    """ Django admin for CourseResetAudit model """

    list_display = ['course', 'user', 'status', 'created', 'completed_at', 'reset_by']
    fields = [
        'created',
        'modified',
        'status',
        'completed_at',
        'course',
        'user',
        'course_enrollment',
        'reset_by',
        'comment'
    ]
    actions = ['mark_failed']

    def get_readonly_fields(self, request, obj=None):
        """
        If we are editing an existing model, we should only be able to change the status, for potential debugging
        """
        if obj:
            return [
                'created',
                'modified',
                'completed_at',
                'course',
                'user',
                'course_enrollment',
                'reset_by',
                'comment'
            ]
        else:
            return ['created', 'modified', 'user']

    @admin.display(description="user")
    def user(self, obj):
        return obj.course_enrollment.user

    @admin.action(description="Fail selected reset attempts")
    def mark_failed(self, request, queryset):
        queryset.update(status=CourseResetAudit.CourseResetStatus.FAILED)


admin.site.register(CourseResetCourseOptIn, CourseResetCourseOptInAdmin)
admin.site.register(CourseResetAudit, CourseResetAuditAdmin)
