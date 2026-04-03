"""
Studio URL configuration for openedx-olx-rest-api.
"""
from django.urls import include, path

from . import views

urlpatterns = [
    path('api/olx-export/v1/', include([
        path('xblock/<str:usage_key_str>/', views.get_block_olx),
        # Get a static file from an XBlock that's not part of contentstore/GridFS
        path('xblock-export-file/<str:usage_key_str>/<path:path>', views.get_block_exportfs_file),
    ])),
]
