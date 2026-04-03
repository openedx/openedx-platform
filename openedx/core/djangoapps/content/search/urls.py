"""
URLs for content sesarch
"""
from django.urls import path

from .views import StudioSearchView

urlpatterns = [
    path('api/content_search/v2/studio/', StudioSearchView.as_view(), name='studio_content_search')
]
