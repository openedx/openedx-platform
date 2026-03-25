"""
Content Tagging URLs
"""
from django.urls import path, include  # noqa: I001

from .rest_api import urls

urlpatterns = [
    path('', include(urls)),
]
