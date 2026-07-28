# pylint: disable=missing-module-docstring

import functools

from django import forms
from django.contrib import admin
from django.db.models import F
from django.urls import reverse
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _
from opaque_keys.edx.keys import CourseKey

from openedx.core.djangoapps.content.course_overviews.models import CourseOverview

from . import models


class ScheduleExperienceAdminInline(admin.StackedInline):
    model = models.ScheduleExperience


def _set_experience(db_name, human_name, modeladmin, request, queryset):  # pylint: disable=redefined-outer-name
    """
    A django action which will set all selected schedules to the supplied experience.
    The intended usage is with functools.partial to generate the action for each experience type
    dynamically.

    Arguments:
        db_name: the database name of the experience being selected
        human_name: the human name of the experience being selected
        modeladmin: The ModelAdmin subclass, passed by django as part of the standard Action interface
        request: The current request, passed by django as part of the standard Action interface
        queryset: The queryset selecting schedules, passed by django as part of the standard Action interface
    """
    rows_updated = models.ScheduleExperience.objects.filter(
        schedule__in=list(queryset)
    ).update(
        experience_type=db_name
    )
    modeladmin.message_user(
        request,
        "{} schedule(s) were changed to use the {} experience".format(  # noqa: UP032
            rows_updated,
            human_name,
        )
    )


# Generate a list of all "set_experience_to_X" actions
experience_actions = []
for (db_name, human_name) in models.ScheduleExperience.EXPERIENCES:
    partial = functools.partial(_set_experience, db_name, human_name)
    partial.short_description = f"Convert the selected schedules to the {human_name} experience"
    partial.__name__ = f"set_experience_to_{db_name}"
    experience_actions.append(partial)


class KnownErrorCases(admin.SimpleListFilter):
    """
    Filter schedules by a list of known error cases.
    """
    title = _('Known Error Case')

    parameter_name = 'error'

    def lookups(self, request, model_admin):
        return (
            ('schedule_start_date', _('Schedule start < course start')),
        )

    def queryset(self, request, queryset):
        if self.value() == 'schedule_start_date':
            return queryset.filter(start_date__lt=F('enrollment__course__start'))


class CourseIdFilter(admin.SimpleListFilter):
    """
    Filter schedules to by course id using a dropdown list.
    """
    template = "dropdown_filter.html"
    title = _("Course Id")
    parameter_name = "course_id"

    def __init__(self, request, params, model, model_admin):
        super().__init__(request, params, model, model_admin)
        self.unused_parameters = params.copy()
        self.unused_parameters.pop(self.parameter_name, None)

    def value(self):
        value = super().value()
        if value == "None" or value is None:
            return None
        else:
            return CourseKey.from_string(value)

    def lookups(self, request, model_admin):
        return (
            (overview.id, str(overview.id)) for overview in CourseOverview.objects.all().order_by('id')
        )

    def queryset(self, request, queryset):
        value = self.value()
        if value is None:
            return queryset
        else:
            return queryset.filter(enrollment__course_id=value)

    def choices(self, changelist):
        yield {
            'selected': self.value() is None,
            'value': None,
            'display': _('All'),
        }
        for lookup, title in self.lookup_choices:
            yield {
                'selected': self.value() == lookup,
                'value': str(lookup),
                'display': title,
            }


@admin.register(models.Schedule)
class ScheduleAdmin(admin.ModelAdmin):  # pylint: disable=missing-class-docstring
    list_display = ('username', 'course_id', 'start_date', 'upgrade_deadline', 'experience_display')
    list_display_links = ('start_date', 'upgrade_deadline', 'experience_display')
    list_filter = (
        CourseIdFilter,
        'experience__experience_type',
        KnownErrorCases
    )
    raw_id_fields = ('enrollment',)
    readonly_fields = ('modified',)
    search_fields = ('enrollment__user__username',)
    inlines = (ScheduleExperienceAdminInline,)
    actions = experience_actions

    def experience_display(self, obj):
        return obj.experience.get_experience_type_display()
    experience_display.short_descriptions = _('Experience')

    def username(self, obj):
        return format_html(
            '<a href="{}">{}</a>',
            reverse("admin:auth_user_change", args=(obj.enrollment.user.id,)),
            obj.enrollment.user.username
        )

    username.short_description = _('Username')

    def course_id(self, obj):
        return format_html(
            '<a href="{}">{}</a>',
            reverse("admin:course_overviews_courseoverview_change", args=(
                obj.enrollment.course_id,
            )),
            obj.enrollment.course_id
        )

    course_id.short_description = _('Course ID')

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        qs = qs.select_related('enrollment', 'enrollment__user')
        return qs


class ScheduleConfigAdminForm(forms.ModelForm):  # pylint: disable=missing-class-docstring
    pass


@admin.register(models.ScheduleConfig)
class ScheduleConfigAdmin(admin.ModelAdmin):  # pylint: disable=missing-class-docstring
    search_fields = ('site',)
    list_display = (
        'site',
        'enqueue_recurring_nudge', 'deliver_recurring_nudge',
        'enqueue_upgrade_reminder', 'deliver_upgrade_reminder',
        'enqueue_course_update', 'deliver_course_update',
    )
    form = ScheduleConfigAdminForm
