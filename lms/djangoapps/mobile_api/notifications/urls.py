"""
URLs for the mobile_api.notifications APIs.
"""
from django.urls import path

from .views import GCMDeviceViewSet

create_gcm_device_post_view = GCMDeviceViewSet.as_view({'post': 'create'})

urlpatterns = [
    path('create-token/', create_gcm_device_post_view, name='gcmdevice-list'),
]
