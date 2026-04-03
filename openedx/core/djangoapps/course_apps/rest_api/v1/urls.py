# lint-amnesty, pylint: disable=missing-module-docstring
from django.urls import re_path

from openedx.core.constants import COURSE_ID_PATTERN

from .views import CourseAppsView

app_name = "openedx.core.djangoapps.course_apps"

urlpatterns = [
    re_path(fr"^apps/{COURSE_ID_PATTERN}$", CourseAppsView.as_view(), name="course_apps"),
]
