"""
Conforming (ADR 0038) URLs for the authoring API, v3.

Mounted at ``api/authoring/v3/`` from ``cms/urls.py``, beside the legacy
``/api/contentstore/v3/`` routes, which stay live for their OEP-21
deprecation window and are marked ``deprecated: true`` in the OpenAPI schema
(see ``cms/lib/spectacular.py``).

ADR 0038 conformance relative to the legacy mount:

* rule 3 — the API name describes the domain (``authoring``), not the
  implementing Django app (``contentstore``);
* rule 4 / 8 — the screen-shaped ``course_details`` collection becomes a
  sub-resource of the plural ``courses/`` collection, one level deep, per the
  ADR's own target for these endpoints
  (``/api/authoring/…/courses/{course_key}/details/`` "and siblings");
* rule 9 — course keys are resolved by the shared ``course_key`` path
  converter (``edx_rest_framework_extensions.url_converters``), which rejects
  deprecated ``Org/Course/Run`` keys with a 404;
* rule 11 — URL names are ``snake_case``, version-free, and unique.

``home/`` is a BFF aggregate for the Studio home screen. Rule 4 disfavors
screen names, but ADR 0038's BFF provision applies: the surface keeps its
``/api/`` prefix and a single canonical conforming mount, and is marked
``x-internal`` in the OpenAPI schema (``cms/lib/spectacular.py``) so clients
can tell it apart from a stable resource contract.
"""

from django.urls import path

from cms.djangoapps.contentstore.rest_api.v3.views import CourseDetailsViewSet, HomeViewSet

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
    # Course details — /api/contentstore/v3/course_details/{course_id}/
    # renamed per the ADR's target shape; same view, same contract.
    path(
        "courses/<course_key:course_id>/details/",
        CourseDetailsViewSet.as_view({"get": "retrieve", "put": "update"}),
        name="course_details",
    ),
]
