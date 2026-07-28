"""
Instructor Task Django app root REST API URLs.
"""
from django.urls import include, path

from lms.djangoapps.instructor_task.rest_api.v1 import urls as v1_urls

app_name = "lms.djangoapps.instructor_task"

urlpatterns = [
    path("v1/", include(v1_urls))
]
