"""
Programs API URLs.

This is legacy URLs for the program dashboard API from when the legacy learner
dashboard existed.  Current-and-future advertised URLs for this API will be
under `api/learner_home`.  This is why there is a version numbering discrepancy.
While these will still be reachable from `/dashboard/v0/programs` for backward
compatibility, the API will now be part of `/learner_dashboard/v1/programs`.
"""

from django.urls import include, path

from openedx.core.djangoapps.programs.rest_api.v1 import urls as v1_programs_rest_api_urls

app_name = "openedx.core.djangoapps.programs"

urlpatterns = [
    path("v0/", include((v1_programs_rest_api_urls, "v0"), namespace="v0")),
]
