"""Grades API v2 URLs."""

from django.urls import path

from lms.djangoapps.grades.rest_api.v2.views.course_grades import CourseGradeViewSet
from lms.djangoapps.grades.rest_api.v2.views.course_grading_policy import CourseGradingPolicyView
from lms.djangoapps.grades.rest_api.v2.views.gradebook_entries import GradebookEntryViewSet
from lms.djangoapps.grades.rest_api.v2.views.submission_histories import SubmissionHistoryViewSet
from lms.djangoapps.grades.rest_api.v2.views.subsection_grade_overrides import SubsectionGradeOverrideViewSet
from lms.djangoapps.grades.rest_api.v2.views.subsection_grades import (
    SubsectionGradeOverrideHistoryViewSet,
    SubsectionGradeViewSet,
)
from lms.djangoapps.grades.rest_api.v2.views.unmatched import UnmatchedRouteView

app_name = 'grade_v2'

urlpatterns = [
    path(
        'course_grades/',
        CourseGradeViewSet.as_view({'get': 'list'}),
        name='course_grade_list',
    ),
    path(
        'course_grades/<str:username>,<course_key:course_key>/',
        CourseGradeViewSet.as_view({'get': 'retrieve'}),
        name='course_grade_detail',
    ),
    path(
        'subsection_grades/<str:username>,<usage_key:usage_key>/',
        SubsectionGradeViewSet.as_view({'get': 'retrieve'}),
        name='subsection_grade_detail',
    ),
    path(
        'subsection_grades/<str:username>,<usage_key:usage_key>/override_history_records/',
        SubsectionGradeOverrideHistoryViewSet.as_view({'get': 'list'}),
        name='subsection_grade_override_history_list',
    ),
    path(
        'courses/<course_key:course_key>/gradebook_entries/',
        GradebookEntryViewSet.as_view({'get': 'list'}),
        name='gradebook_entry_list',
    ),
    path(
        'courses/<course_key:course_key>/gradebook_entries/<str:username>/',
        GradebookEntryViewSet.as_view({'get': 'retrieve'}),
        name='gradebook_entry_detail',
    ),
    path(
        'courses/<course_key:course_key>/subsection_grade_overrides/',
        SubsectionGradeOverrideViewSet.as_view({'post': 'create'}),
        name='subsection_grade_override_list',
    ),
    path(
        'courses/<course_key:course_key>/grading_policy/',
        CourseGradingPolicyView.as_view(),
        name='course_grading_policy',
    ),
    path(
        'courses/<course_key:course_key>/submission_histories/',
        SubmissionHistoryViewSet.as_view({'get': 'list'}),
        name='submission_history_list',
    ),
    # Addresses that no route above matches, such as one with a malformed or
    # retired key, get a JSON not-found error rather than the site's HTML page.
    # They must stay after every real route, which would otherwise be shadowed.
    path('', UnmatchedRouteView.as_view(), name='root_unmatched'),
    path('course_grades/<path:unmatched>/', UnmatchedRouteView.as_view(), name='course_grade_unmatched'),
    path('subsection_grades/', UnmatchedRouteView.as_view(), name='subsection_grade_list_unmatched'),
    path('subsection_grades/<path:unmatched>/', UnmatchedRouteView.as_view(), name='subsection_grade_unmatched'),
    path('courses/', UnmatchedRouteView.as_view(), name='course_list_unmatched'),
    path('courses/<path:unmatched>/', UnmatchedRouteView.as_view(), name='course_unmatched'),
]
