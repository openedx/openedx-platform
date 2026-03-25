"""
URLs for track app
"""
from django.urls import path  # noqa: I001

from . import views
from .views import segmentio

urlpatterns = [
    path('event', views.user_track),
    path('segmentio/event', segmentio.segmentio_event),
]
