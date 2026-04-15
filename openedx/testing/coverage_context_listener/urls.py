"""
Coverage Context Listener URLs.
"""
from django.urls import path

from .views import update_context

urlpatterns = [
    path('update_context', update_context),
]
