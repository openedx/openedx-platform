# lint-amnesty, pylint: disable=missing-module-docstring

from django.urls import path  # noqa: I001
from .views import CourseOutlineView


urlpatterns = [
    path('v1/course_outline/<path:course_key_str>', CourseOutlineView.as_view(),
         name='course_outline',
         )
]
