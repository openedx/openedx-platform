"""Authoring API v1 URLs (ADR 0038 conforming mount for Contentstore v1)."""

from django.urls import path

from cms.djangoapps.contentstore.rest_api.v1.views import XblockViewSet

app_name = "authoring_v1"

urlpatterns = [
    # The viewset has no ``list`` action, so the collection accepts POST only.
    path(
        "xblocks/",
        XblockViewSet.as_view({"post": "create"}),
        name="xblock_list",
    ),
    path(
        "xblocks/<usage_key:usage_key_string>/",
        XblockViewSet.as_view(
            {
                "get": "retrieve",
                "put": "update",
                "patch": "partial_update",
                "delete": "destroy",
            }
        ),
        name="xblock_detail",
    ),
]
