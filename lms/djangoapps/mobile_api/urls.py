"""
URLs for mobile API
"""


from django.urls import include, path  # noqa: I001

from .users.views import my_user_info

urlpatterns = [
    path('users/', include('lms.djangoapps.mobile_api.users.urls')),
    path('my_user_info', my_user_info, name='user-info'),
    path('notifications/', include('lms.djangoapps.mobile_api.notifications.urls')),
    path('course_info/', include('lms.djangoapps.mobile_api.course_info.urls')),
]
