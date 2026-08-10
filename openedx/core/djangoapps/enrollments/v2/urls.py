"""
URLs for the Enrollment API — v2.

Mounted at ``/api/enrollment/v2/`` (see ``lms/urls.py``).

ADR 0028 — :class:`EnrollmentViewSet` is registered via ``DefaultRouter``
(actions: ``list``, ``create``, ``unenroll``, ``allowed``). The other v2
endpoints (singleton retrieve by URL form, roles, course-detail-by-id,
admin enrollments list) cannot be expressed as router-generated URLs, so
they remain as standalone ``APIView`` classes routed via ``path()`` /
``re_path()``.

URL surface
-----------

Router-generated (basename ``enrollment``):
    GET    /enrollment/
    POST   /enrollment/
    POST   /enrollment/unenroll/
    GET    /enrollment/enrollment_allowed/
    POST   /enrollment/enrollment_allowed/
    DELETE /enrollment/enrollment_allowed/

Explicit paths:
    GET    /enrollment/{username},{course_key}   (name: enrollment-v2-retrieve)
    GET    /enrollment/{course_key}              (name: enrollment-v2-retrieve)
    GET    /enrollments/                          (name: enrollment-v2-admin-list)
    GET    /course/{course_key}                   (name: enrollment-v2-course-detail)
    GET    /roles/                                (name: enrollment-v2-roles)
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
        name="enrollment-v2-retrieve",
    ),
    re_path(
        r"^enrollments/?$",
        EnrollmentsAdminListView.as_view(),
        name="enrollment-v2-admin-list",
    ),
    re_path(
        rf"^course/{settings.COURSE_ID_PATTERN}$",
        CourseEnrollmentDetailView.as_view(),
        name="enrollment-v2-course-detail",
    ),
    path("roles/", UserRolesView.as_view(), name="enrollment-v2-roles"),
]
