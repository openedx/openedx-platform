"""
Learner Home URL routing configuration
"""

from django.urls import include, path, re_path

from lms.djangoapps.learner_home import views

from .rest_api import urls as rest_api_urls

app_name = "learner_home"

# Learner Dashboard Routing
urlpatterns = [
    re_path(r"^init/?", views.InitializeView.as_view(), name="initialize"),
    path("mock/", include("lms.djangoapps.learner_home.mock.urls")),
    path("", include(rest_api_urls)),
]
