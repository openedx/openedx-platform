"""
Urls for verifying health (heartbeat) of the app.
"""

from django.urls import path  # noqa: I001
from openedx.core.djangoapps.heartbeat.views import heartbeat

urlpatterns = [
    path('', heartbeat, name='heartbeat'),
]
