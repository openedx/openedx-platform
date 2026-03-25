"""
Commerce URLs
"""


from django.conf import settings  # noqa: I001
from django.urls import include, path, re_path

from . import views

COURSE_URLS = ([
    path('', views.CourseListView.as_view(), name='list'),
    re_path(fr'^{settings.COURSE_ID_PATTERN}/$', views.CourseRetrieveUpdateView.as_view(), name='retrieve_update'),
], 'courses')

ORDER_URLS = ([
    re_path(r'^(?P<number>[-\w]+)/$', views.OrderView.as_view(), name='detail'),
], 'orders')

app_name = 'v1'
urlpatterns = [
    path('courses/', include(COURSE_URLS)),
    path('orders/', include(ORDER_URLS)),
]
