"""URLs served by the embargo app. """

from django.urls import path, re_path

from .views import CheckCourseAccessView, CourseAccessMessageView

app_name = 'embargo'
urlpatterns = [
    re_path(
        r'^blocked-message/(?P<access_point>enrollment|courseware)/(?P<message_key>.+)/$',
        CourseAccessMessageView.as_view(),
        name='blocked_message',
    ),
    path('v1/course_access/', CheckCourseAccessView.as_view(), name='v1_course_access'),
]
