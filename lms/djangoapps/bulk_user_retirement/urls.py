"""
Defines the URL route for this app.
"""

from django.urls import path

from .views import BulkUsersRetirementView

urlpatterns = [
    path(
        'v1/accounts/bulk_retire_users',
        BulkUsersRetirementView.as_view(),
        name='bulk_retirement_api'
    ),
]
