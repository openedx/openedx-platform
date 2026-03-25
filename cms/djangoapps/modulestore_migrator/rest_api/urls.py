"""
Course to Library Import API URLs.
"""

from django.urls import include, path  # noqa: I001

from .v1 import urls as v1_urls

app_name = 'modulestore_migrator'

urlpatterns = [
    path('v1/', include(v1_urls)),
]
