"""
URLs for the Enrollment API — v2.

Mounted at ``/api/enrollment/v2/`` (see ``lms/urls.py``).

ADR 0028 — :class:`EnrollmentViewSet` is registered via ``DefaultRouter``
(actions: ``list``, ``create``, ``unenroll``, ``allowed``). The other v2
endpoints (singleton retrieve by URL form, roles, course-detail-by-id,
admin enrollments list) cannot be expressed as router-generated URLs, so
they remain as standalone ``APIView`` classes routed via ``path()`` /
``re_path()``.

ADR 0038 — the API name and version position already conform. The conforming
routes below fix the remaining rule 6 violations (a required trailing slash;
no optional-slash patterns) and rule 11 violations (``snake_case``,
version-free, unique URL names), and are dual-mounted (OEP-21) beside the
legacy slashless routes, which keep their original names and are marked
``deprecated: true`` in the OpenAPI schema (``lms/lib/spectacular.py``).
Conforming member routes live under the plural ``enrollments/`` and
``courses/`` collections (rule 2), with course keys resolved by the shared
``course_key`` converter (rule 9), which rejects deprecated ``Org/Course/Run``
keys. Deeper ADR 0038 targets — collapsing the singular ``enrollment/``
collection into ``enrollments/``, replacing ``unenroll`` (a verb, rule 10)
with ``DELETE`` on the member address, and addressing the requesting user as
``me`` — are contract changes and belong to a future v3 per ADR 0037.

URL surface
-----------

Router-generated (basename ``enrollment``):
    GET    /enrollment/
    POST   /enrollment/
    POST   /enrollment/unenroll/
    GET    /enrollment/enrollment_allowed/
    POST   /enrollment/enrollment_allowed/
    DELETE /enrollment/enrollment_allowed/

Conforming explicit paths (ADR 0038):
    GET    /enrollments/                              (name: enrollment_admin_list)
    GET    /enrollments/{username},{course_key}/      (name: enrollment_detail)
    GET    /courses/{course_key}/                     (name: course_enrollment_detail)
    GET    /roles/                                    (name: user_roles)

Legacy paths (deprecated, kept for their OEP-21 window):
    GET    /enrollment/{username},{course_key}   (name: enrollment-v2-retrieve)
    GET    /enrollment/{course_key}              (name: enrollment-v2-retrieve-own)
    GET    /enrollments                           (name: enrollment-v2-admin-list)
    GET    /course/{course_key}                   (name: enrollment-v2-course-detail)
"""

from django.conf import settings
from django.urls import path, re_path
from rest_framework.routers import DefaultRouter

from .views import (
    CourseEnrollmentDetailView,
    EnrollmentRetrieveView,
    EnrollmentsAdminListView,
    EnrollmentViewSet,
    UserRolesView,
)

app_name = "v2"

router = DefaultRouter()
router.register(r"enrollment", EnrollmentViewSet, basename="enrollment")

urlpatterns = router.urls + [
    # -- Conforming routes (ADR 0038: required trailing slash, plural
    # -- collections, snake_case version-free names, shared key converter).
    path(
        "enrollments/",
        EnrollmentsAdminListView.as_view(),
        name="enrollment_admin_list",
    ),
    path(
        "enrollments/<str:username>,<course_key:course_id>/",
        EnrollmentRetrieveView.as_view(),
        name="enrollment_detail",
    ),
    path(
        "courses/<course_key:course_id>/",
        CourseEnrollmentDetailView.as_view(),
        name="course_enrollment_detail",
    ),
    path("roles/", UserRolesView.as_view(), name="user_roles"),
    # -- Legacy routes (OEP-21 deprecation window; ADR 0038 rule 6
    # -- violations frozen as-is, marked deprecated in the OpenAPI schema).
    # -- The admin list's optional-slash pattern is narrowed to slashless
    # -- only: the slashed address is now served by the conforming route
    # -- above, so every address that resolved before still resolves.
    re_path(
        r"^enrollments$",
        EnrollmentsAdminListView.as_view(),
        name="enrollment-v2-admin-list",
    ),
    re_path(
        r"^enrollment/{username},{course_key}$".format(  # noqa: UP032
            username=settings.USERNAME_PATTERN, course_key=settings.COURSE_ID_PATTERN,
        ),
        EnrollmentRetrieveView.as_view(),
        name="enrollment-v2-retrieve",
    ),
    re_path(
        rf"^enrollment/{settings.COURSE_ID_PATTERN}$",
        EnrollmentRetrieveView.as_view(),
        # Previously this route shared the name ``enrollment-v2-retrieve``
        # with the composite-key form above, resolving only because Django
        # disambiguates by argument signature (the fragility ADR 0038 rule 11
        # calls out). Nothing reverses it, so it gets its own name.
        name="enrollment-v2-retrieve-own",
    ),
    re_path(
        rf"^course/{settings.COURSE_ID_PATTERN}$",
        CourseEnrollmentDetailView.as_view(),
        name="enrollment-v2-course-detail",
    ),
]
