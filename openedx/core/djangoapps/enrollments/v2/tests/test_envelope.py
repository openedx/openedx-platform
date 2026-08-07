"""
ADR 0029 — Standardized error-response envelope regression tests for the
v2 Enrollment API.

The envelope is wired into every v2 viewset via
:class:`openedx.core.lib.api.mixins.StandardizedErrorMixin`, which overrides
DRF's per-view ``get_exception_handler`` to point at the project-wide
``standardized_error_exception_handler``.

The envelope shape is::

    {
        "type":     "https://docs.openedx.org/errors/<slug>",
        "title":    "<Human-readable title>",
        "status":   <HTTP status code>,
        "detail":   "<flattened error message>",
        "instance": "<request path>",
    }

These tests confirm the envelope reaches every v2 endpoint that can produce
a 401 / 403 / 404 / 400. The last test (``test_v1_endpoint_unaffected``)
locks in the scoping — v1 must NOT carry the envelope.
"""
from unittest.mock import patch

from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from common.djangoapps.student.tests.factories import UserFactory
from openedx.core.djangolib.testing.utils import skip_unless_lms

_REQUIRED_ERROR_FIELDS = ("type", "title", "status", "detail", "instance")


@skip_unless_lms
class TestEnrollmentViewSetEnvelope(APITestCase):
    """ADR 0029 — envelope on the EnrollmentViewSet 401s."""

    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.list_url = reverse("v2:enrollment-list")
        self.unenroll_url = reverse("v2:enrollment-unenroll")
        self.allowed_url = reverse("v2:enrollment-allowed")

    def test_list_unauthenticated_envelope(self):
        response = self.client.get(self.list_url)
        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        for field in _REQUIRED_ERROR_FIELDS:
            assert field in response.data, f"ADR 0029: missing field '{field}'"

    def test_list_unauthenticated_type_uri(self):
        response = self.client.get(self.list_url)
        assert response.data.get("type") == "https://docs.openedx.org/errors/authn"

    def test_unenroll_unauthenticated_envelope(self):
        response = self.client.post(self.unenroll_url, data={}, content_type="application/json")
        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        for field in _REQUIRED_ERROR_FIELDS:
            assert field in response.data, f"ADR 0029: missing field '{field}'"

    def test_allowed_unauthenticated_envelope(self):
        response = self.client.get(self.allowed_url)
        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        for field in _REQUIRED_ERROR_FIELDS:
            assert field in response.data, f"ADR 0029: missing field '{field}'"

    def test_non_admin_get_allowed_envelope(self):
        """ADR 0029 — 403 also carries the envelope."""
        self.client.force_authenticate(user=UserFactory.create())
        response = self.client.get(self.allowed_url)
        assert response.status_code == status.HTTP_403_FORBIDDEN
        for field in _REQUIRED_ERROR_FIELDS:
            assert field in response.data, f"ADR 0029: missing field '{field}'"
        assert response.data.get("type") == "https://docs.openedx.org/errors/authz"

    def test_create_missing_course_id_envelope(self):
        """ADR 0029 — inline ValidationError surfaces with the envelope."""
        self.client.force_authenticate(user=UserFactory.create())
        response = self.client.post(self.list_url, data={}, content_type="application/json")
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        for field in _REQUIRED_ERROR_FIELDS:
            assert field in response.data, f"ADR 0029: missing field '{field}'"
        assert response.data.get("type") == "https://docs.openedx.org/errors/validation"

    def test_instance_field_is_request_path(self):
        response = self.client.get(self.list_url)
        assert response.data.get("instance") == self.list_url

    def test_error_body_has_no_developer_message(self):
        """Legacy DeveloperErrorViewMixin fields must not leak through."""
        response = self.client.get(self.list_url)
        assert "developer_message" not in response.data
        assert "error_code" not in response.data


@skip_unless_lms
class TestCourseEnrollmentDetailViewEnvelope(APITestCase):
    """ADR 0029 — envelope on the public course-detail endpoint."""

    def test_invalid_course_key_envelope(self):
        url = reverse("v2:enrollment-v2-course-detail", kwargs={"course_id": "course-v1:org+course+run"})
        with patch(
            "openedx.core.djangoapps.enrollments.v2.views.CourseOverview.get_from_id",
        ) as mock_get:
            from openedx.core.djangoapps.content.course_overviews.models import CourseOverview
            mock_get.side_effect = CourseOverview.DoesNotExist()
            response = self.client.get(url)
        assert response.status_code == status.HTTP_404_NOT_FOUND
        for field in _REQUIRED_ERROR_FIELDS:
            assert field in response.data, f"ADR 0029: missing field '{field}'"
        assert response.data.get("type") == "https://docs.openedx.org/errors/not-found"


@skip_unless_lms
class TestV1EndpointUnaffected(APITestCase):
    """
    The ADR 0029 envelope must be scoped to v2 — v1 endpoints continue to
    use whichever handler the project-wide ``EXCEPTION_HANDLER`` setting
    points at. Hitting v1 unauthenticated must NOT return the v2 envelope.
    """

    def test_v1_enrollment_list_does_not_carry_envelope(self):
        response = self.client.get(reverse("courseenrollments"))
        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        # v1 still uses the project-default handler; ADR 0029 fields absent.
        assert "type" not in response.data
        assert "instance" not in response.data
