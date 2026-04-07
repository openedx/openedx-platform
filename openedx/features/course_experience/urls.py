"""
Defines URLs for the course experience.
"""

from django.urls import path

from .views.course_home import outline_tab
from .views.course_updates import CourseUpdatesView

urlpatterns = [
    path('', outline_tab),  # a now-removed legacy view, redirects to MFE
    path('updates', CourseUpdatesView.as_view(), name='openedx.course_experience.course_updates'),
]
