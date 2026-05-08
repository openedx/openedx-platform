"""
Basic admin screens to search and edit InstructorTasks.

This will mostly involve searching by course_id or task_id and manually failing
a task.

"""
from config_models.admin import ConfigurationModelAdmin
from django.contrib import admin

from .config.models import GradeReportSetting
from .models import InstructorTask


def mark_tasks_as_failed(modeladmin, request, queryset):  # pylint: disable=unused-argument
    queryset.update(
        task_state='FAILURE',
        task_output='{}',
        task_key='dummy_task_key',
    )

mark_tasks_as_failed.short_description = "Mark Tasks as Failed"


class InstructorTaskAdmin(admin.ModelAdmin):  # pylint: disable=missing-class-docstring
    actions = [mark_tasks_as_failed]
    list_display = [
        'task_id',
        'task_state',
        'task_type',
        'course_id',
        'username',
        'email',
        'created',
        'updated',
    ]
    list_filter = ['task_type', 'task_state']
    search_fields = [
        'task_id', 'course_id', 'requester__email', 'requester__username'
    ]
    raw_id_fields = ['requester']  # avoid trying to make a select dropdown

    def email(self, task):
        return task.requester.email
    email.admin_order_field = 'requester__email'

    def username(self, task):
        return task.requester.username
    email.admin_order_field = 'requester__username'

admin.site.register(InstructorTask, InstructorTaskAdmin)
admin.site.register(GradeReportSetting, ConfigurationModelAdmin)
