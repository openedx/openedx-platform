"""
Course API URLs
"""


from django.conf import settings  # noqa: I001
from django.urls import include, path, re_path

from .views import CourseDetailView, CourseIdListView, CourseListView

urlpatterns = [
    path('v1/courses/', CourseListView.as_view(), name="course-list"),
    re_path(fr'^v1/courses/{settings.COURSE_KEY_PATTERN}', CourseDetailView.as_view(), name="course-detail"),
    path('v1/course_ids/', CourseIdListView.as_view(), name="course-id-list"),
    path('', include('lms.djangoapps.course_api.blocks.urls'))
]
