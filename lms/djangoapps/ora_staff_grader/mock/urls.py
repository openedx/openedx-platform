"""
URLs for Enhanced Staff Grader (ESG) backend-for-frontend (BFF)

NOTE - This should be the same as ../urls.py
"""
from django.urls import path

from lms.djangoapps.ora_staff_grader.mock.views import (
    InitializeView,
    SubmissionFetchView,
    SubmissionLockView,
    SubmissionStatusFetchView,
    UpdateGradeView,
)

urlpatterns = []
app_name = "mock-ora-staff-grader"

urlpatterns += [
    path("initialize", InitializeView.as_view(), name="initialize"),
    path(
        "submission/status",
        SubmissionStatusFetchView.as_view(),
        name="fetch-submission-status",
    ),
    path("submission/lock", SubmissionLockView.as_view(), name="lock-submission"),
    path("submission/grade", UpdateGradeView.as_view(), name="update-grade"),
    path("submission", SubmissionFetchView.as_view(), name="fetch-submission"),
]
