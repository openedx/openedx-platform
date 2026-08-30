"""
Conforming (ADR 0038) URLs for the authoring API, v1.

Mounted at ``api/authoring/v1/`` from ``cms/urls.py``, beside the legacy
``/api/contentstore/v1/xblock/`` routes, which stay live for their OEP-21
deprecation window and are marked ``deprecated: true`` in the OpenAPI schema
(see ``cms/lib/spectacular.py``).

ADR 0038 conformance relative to the legacy mount:

* rule 2 — the collection is plural (``xblocks/``), the API name singular;
* rule 3 — the API name describes the domain (``authoring``), not the
  implementing Django app (``contentstore``);
* rule 9 — the identifier is resolved by the shared ``usage_key`` path
  converter (``edx_rest_framework_extensions.url_converters``), which rejects
  deprecated ``i4x://`` keys with a 404;
* rule 11 — URL names are ``snake_case``, version-free, and unique.

Note: ADR 0038 (implementation note 4) asks that ``/api/authoring/v1/xblocks/``
be reconciled with the existing Learning Core ``/api/xblock/v2/xblocks/``
rather than leaving two names for what looks like one API. That reconciliation
is an API-owner decision tracked with the DEPR work, not part of this
mechanical migration.
"""

from django.urls import path

from cms.djangoapps.contentstore.rest_api.v1.views import XblockViewSet

app_name = "authoring_v1"

urlpatterns = [
    # No ``list`` action exists on the viewset, so the collection URL accepts
    # POST only — the same surface the legacy router-generated route exposes.
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
