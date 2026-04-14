""" URL definitions for waffle utils. """
from django.urls import path

from openedx.core.djangoapps.waffle_utils.views import ToggleStateView

urlpatterns = [
    path('v0/state/', ToggleStateView.as_view(), name="toggle_state"),
]
