"""
URLs for the notifications API.
"""
from django.urls import path
from rest_framework import routers

from .views import (
    MarkNotificationsSeenAPIView,
    NotificationCountView,
    NotificationListAPIView,
    NotificationReadAPIView,
    preference_update_from_encrypted_username_view,
    NotificationPreferencesView,
    NotificationPreferencesViewV3,
)

router = routers.DefaultRouter()

urlpatterns = [
    path(
        'v2/configurations/',
        NotificationPreferencesView.as_view(),
        name='notification-preferences-aggregated-v2'
    ),
    path(
        'v3/configurations/',
        NotificationPreferencesViewV3.as_view(),
        name='notification-preferences-aggregated-v3'
    ),
    path('', NotificationListAPIView.as_view(), name='notifications-list'),
    path('count/', NotificationCountView.as_view(), name='notifications-count'),
    path(
        'mark-seen/<app_name>/',
        MarkNotificationsSeenAPIView.as_view(),
        name='mark-notifications-seen'
    ),
    path('read/', NotificationReadAPIView.as_view(), name='notifications-read'),
    path('preferences/update/<str:username>/', preference_update_from_encrypted_username_view,
         name='preference_update_view'),
    path('preferences/update/<str:username>/<str:patch>/', preference_update_from_encrypted_username_view,
         name='preference_update_from_encrypted_username_view'),
]

urlpatterns += router.urls
