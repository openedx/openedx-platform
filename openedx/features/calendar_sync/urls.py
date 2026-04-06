"""
Defines URLs for Calendar Sync.
"""

from django.urls import path

from .views.calendar_sync import CalendarSyncView

urlpatterns = [
    path('calendar_sync', CalendarSyncView.as_view(),
         name='openedx.calendar_sync',
         ),
]
