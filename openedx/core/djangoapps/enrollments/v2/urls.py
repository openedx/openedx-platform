"""
URLs for the Enrollment API — v2.

Mounted at ``/api/enrollment/v2/`` (see ``lms/urls.py``).

Conforming routes (ADR 0038) are dual-mounted beside the legacy slashless
routes, which keep their original names and are marked ``deprecated: true``
in the OpenAPI schema (``lms/lib/spectacular.py``). Collapsing ``enrollment/``
into ``enrollments/``, replacing ``unenroll`` with ``DELETE``, and addressing
the caller as ``me`` are contract changes deferred to a future version.

URL surface
-----------

Router-generated (basename ``enrollment``):
    GET    /enrollment/
    POST   /enrollment/
    POST   /enrollment/unenroll/
    GET    /enrollment/enrollment_allowed/
    POST   /enrollment/enrollment_allowed/
    DELETE /enrollment/enrollment_allowed/

Conforming explicit paths:
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

urlpatterns = [
    *router.urls,
    # Conforming routes (ADR 0038).
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
    # Legacy routes, kept for their OEP-21 window. The admin list's
    # optional-slash pattern is narrowed to slashless only, since the slashed
    # address is now served by the conforming route above.
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
        # Was sharing ``enrollment-v2-retrieve`` with the composite-key form
        # above; nothing reverses it, so it gets its own name.
        name="enrollment-v2-retrieve-own",
    ),
    re_path(
        rf"^course/{settings.COURSE_ID_PATTERN}$",
        CourseEnrollmentDetailView.as_view(),
        name="enrollment-v2-course-detail",
    ),
]
