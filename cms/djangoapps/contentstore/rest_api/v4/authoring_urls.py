"""
Conforming (ADR 0038) URLs for the authoring API, v4.

Mounted at ``api/authoring/v4/`` from ``cms/urls.py``, beside the legacy
``/api/contentstore/v4/home/courses/`` route, which stays live for its OEP-21
deprecation window and is marked ``deprecated: true`` in the OpenAPI schema
(see ``cms/lib/spectacular.py``).

ADR 0038 conformance relative to the legacy mount:

* rule 3 — the API name describes the domain (``authoring``), not the
  implementing Django app (``contentstore``);
* rule 4 — the screen-shaped ``home/courses/`` address becomes the concrete
  plural collection ``courses/`` (the authorable courses, filtered, sorted,
  and paginated in the query string);
* rule 11 — the URL name is ``snake_case``, version-free, and unique.
"""

from django.urls import path

from cms.djangoapps.contentstore.rest_api.v4.views import home

app_name = "authoring_v4"

urlpatterns = [
    path(
        "courses/",
        home.HomeCoursesViewSet.as_view({"get": "list"}),
        name="course_list",
    ),
]
