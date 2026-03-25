"""
Programs API URLs.
"""

from django.urls import include, path  # noqa: I001

from openedx.core.djangoapps.programs.rest_api.v1 import urls as v1_programs_rest_api_urls

urlpatterns = [
    path("v1/", include((v1_programs_rest_api_urls, "v1"))),
]
