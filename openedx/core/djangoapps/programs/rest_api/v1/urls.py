"""
REST APIs for Programs.
"""

from django.urls import re_path

from openedx.core.djangoapps.programs.rest_api.v1.views import ProgramProgressDetailView, Programs

ENTERPRISE_UUID_PATTERN = r"[0-9a-fA-F]{8}-?[0-9a-fA-F]{4}-?4[0-9a-fA-F]{3}-?[89abAB][0-9a-fA-F]{3}-?[0-9a-fA-F]{12}"
PROGRAM_UUID_PATTERN = r"[0-9a-f-]+"

urlpatterns = [
    re_path(
        rf"^programs/(?P<enterprise_uuid>{ENTERPRISE_UUID_PATTERN})/$",
        Programs.as_view(),
        name="program_list",
    ),
    re_path(
        "^programs/$",
        Programs.as_view(),
        name="program_list_b2c",
    ),
    re_path(
        rf"^programs/(?P<program_uuid>{PROGRAM_UUID_PATTERN})/progress_details/$",
        ProgramProgressDetailView.as_view(),
        name="program_progress_detail",
    ),
]
