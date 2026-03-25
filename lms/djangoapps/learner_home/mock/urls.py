"""Learner Home mock URL routing configuration"""

from django.urls import re_path  # noqa: I001

from lms.djangoapps.learner_home.mock import mock_views

urlpatterns = [
    re_path(r"^init/?", mock_views.InitializeView.as_view(), name="mock_initialize"),
]
