""" Grades API v1 URLs. """


from django.conf import settings  # noqa: I001
from django.urls import path, re_path

from lms.djangoapps.grades.rest_api.v1 import gradebook_views, views

app_name = 'lms.djangoapps.grades'

urlpatterns = [
    path(
        'courses/',
        views.CourseGradesView.as_view(),
        name='course_grades'
    ),
    re_path(
        fr'^courses/{settings.COURSE_ID_PATTERN}/$',
        views.CourseGradesView.as_view(),
        name='course_grades'
    ),
    re_path(
        fr'^policy/courses/{settings.COURSE_ID_PATTERN}/$',
        views.CourseGradingPolicy.as_view(),
        name='course_grading_policy'
    ),
    re_path(
        fr'^gradebook/{settings.COURSE_ID_PATTERN}/$',
        gradebook_views.GradebookView.as_view(),
        name='course_gradebook'
    ),
    re_path(
        fr'^gradebook/{settings.COURSE_ID_PATTERN}/bulk-update$',
        gradebook_views.GradebookBulkUpdateView.as_view(),
        name='course_gradebook_bulk_update'
    ),
    re_path(
        fr'^gradebook/{settings.COURSE_ID_PATTERN}/grading-info$',
        gradebook_views.CourseGradingView.as_view(),
        name='course_gradebook_grading_info'
    ),
    re_path(
        r'^subsection/(?P<subsection_id>.*)/$',
        gradebook_views.SubsectionGradeView.as_view(),
        name='course_grade_overrides'
    ),
    path(
        'section_grades_breakdown/',
        views.SectionGradesBreakdown.as_view(),
        name='section_grades_breakdown'
    ),
    re_path(
        fr'submission_history/{settings.COURSE_ID_PATTERN}/',
        views.SubmissionHistoryView.as_view(),
        name='submission_history'
    ),
]
