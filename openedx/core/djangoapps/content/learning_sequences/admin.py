"""
Read-only Django Admin for viewing Learning Sequences and Outline data.
"""
import json
from datetime import datetime
from enum import Enum

import attr
from django.contrib import admin
from django.utils.html import format_html, format_html_join
from django.utils.translation import gettext_lazy as _
from opaque_keys import OpaqueKey

from openedx.core import types

from .api import get_content_errors, get_course_outline
from .models import CourseContext, CourseSectionSequence


class HasErrorsListFilter(admin.SimpleListFilter):
    """
    Filter to find Courses with content errors.

    The default Django filter on an integer field will give a choice of values,
    which isn't something we really want. We just want a filter for > 0 errors.
    """
    title = _("Error Status")
    parameter_name = 'status'

    def lookups(self, request, model_admin):
        return (
            ('has_errors', _('Courses with Errors')),
        )

    def queryset(self, request, queryset):
        if self.value() == 'has_errors':
            return queryset.filter(
                learning_context__publish_report__num_errors__gt=0,
            )


class CourseSectionSequenceInline(admin.TabularInline):
    """
    Inline for showing the sequences of a course.

    The queries look a bit weird because a CourseSectionSequence holds both
    course-specific Sequence metadata, while much of the data we care about is
    in LearningSequence (like the title) and CourseSequenceExam.
    """
    model = CourseSectionSequence
    verbose_name = "Sequence"
    verbose_name_plural = "Sequences"

    fields = (
        'title',
        'is_time_limited',
        'is_proctored_enabled',
        'is_practice_exam',
        'accessible_after_due',
        'visible_to_staff_only',
        'hide_from_toc',
    )
    readonly_fields = (
        'title',
        'is_time_limited',
        'is_proctored_enabled',
        'is_practice_exam',
        'accessible_after_due',
        'visible_to_staff_only',
        'hide_from_toc',
    )
    ordering = ['ordering']

    def get_queryset(self, request):
        """
        Optimization to cut an extra two requests per sequence.

        We still have an N+1 issue, but given the number of sequences in a
        course, this is tolerable even for large courses. It is possible to get
        this down to one query if we do a lower level rendering for the
        sequences later.
        """
        qs = super().get_queryset(request)
        qs = qs.select_related('section', 'sequence')
        return qs

    @types.admin_display(description="Title")
    def title(self, cs_seq):
        return cs_seq.sequence.title

    @types.admin_display(boolean=True)
    def accessible_after_due(self, cs_seq):
        return not cs_seq.inaccessible_after_due

    @types.admin_display(boolean=True, description="Staff Only")
    def visible_to_staff_only(self, cs_seq):
        return not cs_seq.visible_to_staff_only

    @types.admin_display(boolean=True, description="Timed Exam")
    def is_time_limited(self, cs_seq):
        return cs_seq.exam.is_time_limited

    @types.admin_display(boolean=True, description="Proctored Exam")
    def is_proctored_enabled(self, cs_seq):
        return cs_seq.exam.is_proctored_enabled

    @types.admin_display(boolean=True, description="Practice Exam")
    def is_practice_exam(self, cs_seq):
        return cs_seq.exam.is_practice_exam


class CourseContextAdmin(admin.ModelAdmin):
    """
    This is a read-only model admin that is meant to be useful for querying.

    Writes are disabled, because:

    1. These values are auto-built/updated based on course publishes.
    2. These are read either the Studio or LMS process, but it's only supposed
       to be written to from the Studio process.
    """
    list_display = (
        'course_key',
        'title',
        'published_at',
        'num_errors',
        'num_sections',
        'num_sequences',
    )
    list_select_related = (
        'learning_context',
        'learning_context__publish_report',
    )
    list_filter = (
        HasErrorsListFilter,
        'learning_context__published_at',
    )
    readonly_fields = (
        'course_key',
        'title',
        'published_at',
        'published_version',
        'created',
        'modified',
        'course_visibility',
        'self_paced',
        'days_early_for_beta',
        'entrance_exam_id',

        'error_details',
        'raw_outline',
    )
    raw_id_fields = (
        'learning_context',
    )
    fieldsets = (
        (
            None,
            {
                'fields': (
                    'course_key',
                    'title',
                    'published_at',
                    'course_visibility',
                    'self_paced',
                    'days_early_for_beta',
                    'entrance_exam_id',
                ),
            }
        ),
        (
            'Outline Data',
            {
                'fields': (
                    'num_sections', 'num_sequences', 'num_errors', 'error_details',
                ),
            }
        ),
        (
            'Debug Details',
            {
                'fields': (
                    'published_version', 'created', 'modified', 'raw_outline'
                ),
                'classes': ('collapse',),
            }
        ),
    )
    inlines = [
        CourseSectionSequenceInline,
    ]
    search_fields = ['learning_context__context_key', 'learning_context__title']
    ordering = ['-learning_context__published_at']

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        qs = qs.prefetch_related('section_sequences')
        return qs

    @types.admin_display(description="Record Created")
    def created(self, course_context):
        return course_context.learning_context.created

    @types.admin_display(description="Record Modified")
    def modified(self, course_context):
        return course_context.learning_context.modified

    def course_key(self, course_context):
        return course_context.learning_context.context_key

    def title(self, course_context):
        return course_context.learning_context.title

    @types.admin_display(description="Published at (UTC)")
    def published_at(self, course_context):
        published_at_dt = course_context.learning_context.published_at
        return published_at_dt.strftime("%B %-d, %Y, %-I:%M %p")

    def published_version(self, course_context):
        return course_context.learning_context.published_version

    def _publish_report_attr(self, course_context, attr_name):
        learning_context = course_context.learning_context
        if not hasattr(learning_context, 'publish_report'):
            return None
        return getattr(learning_context.publish_report, attr_name)

    @types.admin_display(description="Errors")
    def num_errors(self, course_context):
        return self._publish_report_attr(course_context, 'num_errors')

    @types.admin_display(description="Sections")
    def num_sections(self, course_context):
        return self._publish_report_attr(course_context, 'num_sections')

    @types.admin_display(description="Sequences")
    def num_sequences(self, course_context):
        return self._publish_report_attr(course_context, 'num_sequences')

    def raw_outline(self, obj):
        """
        Computed attribute that shows the outline JSON in the detail view.
        """
        def json_serializer(_obj, _field, value):
            if isinstance(value, OpaqueKey):
                return str(value)
            elif isinstance(value, Enum):
                return value.value
            elif isinstance(value, datetime):
                return value.isoformat()
            return value

        outline_data = get_course_outline(obj.learning_context.context_key)
        outline_data_dict = attr.asdict(
            outline_data,
            recurse=True,
            value_serializer=json_serializer,
        )
        outline_data_json = json.dumps(outline_data_dict, indent=2, sort_keys=True)
        return format_html("<pre>\n{}\n</pre>", outline_data_json)

    def error_details(self, course_context):
        """
        Generates the HTML for Error Details.
        """
        learning_context = course_context.learning_context
        if not hasattr(learning_context, 'publish_report'):
            return ""

        content_errors = get_content_errors(learning_context.context_key)
        if not content_errors:
            return format_html("<p>No errors were found.</p>")

        list_items = format_html_join(
            "\n",
            "<li>{} <br><small>Usage Key: {}</small></li>",
            (
                (err_data.message, err_data.usage_key)
                for err_data in content_errors
            )
        )
        return format_html(
            """
            <p>
            Parts of the course content were skipped when generating the Outline
            because they did not follow the standard Course → Section →
            Subsection hierarchy. Course structures like this cannot be created
            in Studio, but can be created by OLX import. In OLX, this hierarchy
            is represented by the tags <code>{}</code> → <code>{}</code> →
            <code>{}</code>.
            </p>
            <ol>
                {}
            </ol>
            """,
            "<course>",
            "<chapter>",
            "<sequential>",
            list_items,
        )

    def has_add_permission(self, request):
        """
        Disallow additions. See docstring for has_change_permission()
        """
        return False

    def has_change_permission(self, request, obj=None):
        """
        Disallow edits.

        This app rebuilds automatically based off of course publishes. Any
        manual edits will be wiped out the next time someone touches the course,
        so it's better to disallow this in the admin rather than to pretend this
        works and have it suddenly change back when someone edits the course.
        """
        return False

    def has_delete_permission(self, request, obj=None):
        """
        Disallow deletes.

        Deleting these models can have far reaching consequences and delete a
        lot of related data in other parts of the application/project. We should
        only do update through the API, which allows us to rebuild the outlines.
        """
        return False


admin.site.register(CourseContext, CourseContextAdmin)
