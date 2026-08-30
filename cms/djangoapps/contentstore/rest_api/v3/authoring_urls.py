"""
Conforming (ADR 0038) URLs for the authoring API, v3.

Mounted at ``api/authoring/v3/`` from ``cms/urls.py``, beside the legacy
``/api/contentstore/v3/`` routes, which stay live for their OEP-21
deprecation window and are marked ``deprecated: true`` in the OpenAPI schema
(see ``cms/lib/spectacular.py``).

ADR 0038 conformance relative to the legacy mount:

* rule 3 — the API name describes the domain (``authoring``), not the
  implementing Django app (``contentstore``);
* rule 11 — URL names are ``snake_case``, version-free, and unique.

``home/`` is a BFF aggregate for the Studio home screen. Rule 4 disfavors
screen names, but ADR 0038's BFF provision applies: the surface keeps its
``/api/`` prefix and a single canonical conforming mount, and is marked
``x-internal`` in the OpenAPI schema (``cms/lib/spectacular.py``) so clients
can tell it apart from a stable resource contract.
"""

from django.urls import path

from cms.djangoapps.contentstore.rest_api.v3.views import HomeViewSet

app_name = "authoring_v3"

urlpatterns = [
    # Studio home BFF (x-internal — see module docstring).
    path(
        "home/",
        HomeViewSet.as_view({"get": "list"}),
        name="home",
    ),
    path(
        "home/courses/",
        HomeViewSet.as_view({"get": "courses"}),
        name="home_courses",
    ),
    path(
        "home/libraries/",
        HomeViewSet.as_view({"get": "libraries"}),
        name="home_libraries",
    ),
]
