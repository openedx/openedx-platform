"""
Content Tagging URLs
"""
from django.urls import include, path

from .rest_api import urls

urlpatterns = [
    path('', include(urls)),
]
