"""
URLs file for the Entitlements API.
"""

from django.urls import include, path

app_name = 'entitlements'
urlpatterns = [
    path('v1/', include('common.djangoapps.entitlements.rest_api.v1.urls')),
]
