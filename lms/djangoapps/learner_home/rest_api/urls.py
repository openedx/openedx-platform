"""Learner Home REST API URLs."""

from django.urls import include, path

from openedx.core.djangoapps.programs.rest_api.v1 import urls as v1_programs_rest_api_urls

from .pathways import LearnerPathwaysByCourseView, LearnerPathwaysView

urlpatterns = [
    path("v1/pathways/", LearnerPathwaysView.as_view(), name="pathways"),
    path("v1/pathways/by_course/", LearnerPathwaysByCourseView.as_view(), name="pathways_by_course"),
    path("v1/", include((v1_programs_rest_api_urls, "v1"))),
]
