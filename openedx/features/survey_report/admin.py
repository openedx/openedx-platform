"""
Django Admin page for SurveyReport.
"""


from django.conf import settings
from django.contrib import admin

from .api import send_report_to_external_api
from .models import SurveyReport


class SurveyReportAdmin(admin.ModelAdmin):
    """
    Admin to manage survey reports.
    """
    change_list_template = "survey_report/change_list.html"

    readonly_fields = (
        'courses_offered', 'learners', 'registered_learners',
        'enrollments', 'generated_certificates', 'extra_data',
        'created_at', 'state',
    )

    list_display = (
        'id', 'summary', 'created_at', 'report_state'
    )

    actions = ['send_report']

    @admin.action(description='Send report to external API')
    def send_report(self, request, queryset):
        """
        Add custom actions to send the reports to the external API.
        """
        selected_reports = queryset.values_list('id', flat=True)
        for report_id in selected_reports:
            send_report_to_external_api(report_id=report_id)

    def summary(self, obj) -> str:
        """
        Show a summary of the survey report.
        info:
        - Courses: Total number of active unique courses.
        - Learners: Recently active users with login in some weeks.
        - Enrollments: Total number of active enrollments in the platform.
        """
        return f"Total Courses: {obj.courses_offered}," \
               f"Total Learners: {obj.learners}, Total Enrollments: {obj.enrollments}"

    def has_add_permission(self, request):
        """
        Removes the "add" button from admin.
        """
        return False

    def has_delete_permission(self, request, obj=None):
        """
        Removes the "delete" options from admin.
        """
        return False

    def changeform_view(self, request, object_id=None, form_url='', extra_context=None):
        """
        Removes the "save" buttons from admin change view.
        """
        extra_context = extra_context or {}

        extra_context['show_save'] = False
        extra_context['show_save_and_continue'] = False
        extra_context['show_save_and_add_another'] = False

        return super().changeform_view(request, object_id, form_url, extra_context)

    def get_actions(self, request):
        """
        Removes the default bulk delete option provided by Django,
        it doesn't do what we need for this model.
        """
        actions = super().get_actions(request)
        if 'delete_selected' in actions:
            del actions['delete_selected']
        return actions

    def report_state(self, obj):
        """
        Method to define the custom State column with the new "send" state,
        to avoid modifying the current models.
        """
        try:
            if obj.surveyreportupload_set.last().is_uploaded():
                return "Sent"
        except AttributeError:
            return obj.state.capitalize()
    report_state.short_description = 'State'


if settings.SURVEY_REPORT_ENABLE:
    admin.site.register(SurveyReport, SurveyReportAdmin)
