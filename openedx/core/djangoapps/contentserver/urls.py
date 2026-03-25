"""
URL patterns for course asset serving.
"""

from django.urls import re_path  # noqa: I001

from . import views

# These patterns are incomplete and do not capture the variable
# components of the URLs. That's because the view itself is separately
# parsing the paths, for historical reasons. See docstring on views.py.
urlpatterns = [
    re_path("^c4x/", views.course_assets_view),
    re_path("^asset-v1:", views.course_assets_view),
    re_path("^assets/courseware/", views.course_assets_view),
]
