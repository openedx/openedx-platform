"""
Admin site bindings for CourseActionState
"""

from django.contrib import admin  # noqa: I001

from common.djangoapps.course_action_state.models import CourseRerunState

admin.site.register(CourseRerunState)
