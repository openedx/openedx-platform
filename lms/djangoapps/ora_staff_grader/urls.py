"""
URLs for Enhanced Staff Grader (ESG) backend-for-frontend (BFF)
"""
from django.urls import include, path
from rest_framework.routers import DefaultRouter

from lms.djangoapps.ora_staff_grader.views import (
    AssessmentFeedbackView,
    InitializeView,
    SubmissionBatchUnlockView,
    SubmissionFetchView,
    SubmissionFilesFetchView,
    SubmissionLockView,
    SubmissionStatusFetchView,
    UpdateGradeView
)

urlpatterns = []
app_name = "ora-staff-grader"

router = DefaultRouter()
router.register("assessments/feedback", AssessmentFeedbackView, basename="assessment-feedback")

urlpatterns += [
    path("mock/", include("lms.djangoapps.ora_staff_grader.mock.urls")),
    path("initialize", InitializeView.as_view(), name="initialize"),
    path("submission/batch/unlock", SubmissionBatchUnlockView.as_view(), name="batch-unlock"),
    path("submission/files", SubmissionFilesFetchView.as_view(), name="fetch-files"),
    path(
        "submission/status",
        SubmissionStatusFetchView.as_view(),
        name="fetch-submission-status",
    ),
    path("submission/lock", SubmissionLockView.as_view(), name="lock"),
    path("submission/grade", UpdateGradeView.as_view(), name="update-grade"),
    path("submission", SubmissionFetchView.as_view(), name="fetch-submission"),
]

urlpatterns += router.urls
