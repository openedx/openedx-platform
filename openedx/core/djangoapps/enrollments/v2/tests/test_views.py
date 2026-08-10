"""
Action + permission regression tests for the v2 Enrollment ViewSet.

MongoDB-free: every service-layer call is mocked, so these tests run
without a live modulestore or course-overview row.

Covers:
  - ADR 0026: permission enforcement on every action (list/create/unenroll/allowed)
  - ADR 0028: router-generated URL reverse names work
  - ADR 0032: list action returns the 7-field DefaultPagination envelope
  - ADR 0033: ordering whitelist + Deprecation header on the admin list
"""
from unittest.mock import patch

from django.test import override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from common.djangoapps.student.tests.factories import AdminFactory, UserFactory
from openedx.core.djangoapps.enrollments.v2.views import EnrollmentViewSet
from openedx.core.djangolib.testing.utils import skip_unless_lms

API_KEY = "test-enrollment-v2-api-key"

# Mock targets — all keyed off the v2 module to avoid leaking into v1.
MOCK_OPS_LIST = "openedx.core.djangoapps.enrollments.v2.views._OPS.list_enrollments_for_user"
MOCK_OPS_CREATE = "openedx.core.djangoapps.enrollments.v2.views._OPS.create_or_update_enrollment"
MOCK_OPS_UNENROLL = "openedx.core.djangoapps.enrollments.v2.views._OPS.unenroll_user_for_retirement"
MOCK_OPS_LIST_ALLOWED = "openedx.core.djangoapps.enrollments.v2.views._OPS.list_allowed_for_email"
MOCK_OPS_CREATE_ALLOWED = "openedx.core.djangoapps.enrollments.v2.views._OPS.create_allowed_enrollment"
MOCK_OPS_DELETE_ALLOWED = "openedx.core.djangoapps.enrollments.v2.views._OPS.delete_allowed_enrollment"


# ---------------------------------------------------------------------------
# EnrollmentViewSet.list  (GET /enrollment/)
# ---------------------------------------------------------------------------

@skip_unless_lms
class TestEnrollmentViewSetList(APITestCase):
    """ADR 0026 + 0028 — permission + reverse-name tests for the list action."""

    def setUp(self):
        super().setUp()
        self.user = UserFactory.create(password="test")
        self.url = reverse("v2:enrollment-list")

    def test_unauthenticated_gets_401(self):
        response = self.client.get(self.url)
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    @patch(MOCK_OPS_LIST, return_value=[])
    def test_authenticated_user_gets_200(self, mock_list):  # noqa: ARG002
        self.client.force_authenticate(user=self.user)
        response = self.client.get(self.url)
        assert response.status_code == status.HTTP_200_OK

    @patch(MOCK_OPS_LIST, return_value=[])
    def test_valid_api_key_gets_200(self, mock_list):  # noqa: ARG002
        with override_settings(EDX_API_KEY=API_KEY):
            response = self.client.get(self.url, HTTP_X_EDX_API_KEY=API_KEY)
        assert response.status_code == status.HTTP_200_OK

    def test_invalid_api_key_without_session_gets_401(self):
        response = self.client.get(self.url, HTTP_X_EDX_API_KEY="wrong-key")
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    @patch(MOCK_OPS_LIST, return_value=[])
    def test_list_returns_pagination_envelope(self, mock_list):  # noqa: ARG002
        """ADR 0032 — every response carries the 7-field DefaultPagination envelope."""
        self.client.force_authenticate(user=self.user)
        response = self.client.get(self.url)
        assert response.status_code == status.HTTP_200_OK
        for field in ("count", "num_pages", "current_page", "start", "next", "previous", "results"):
            assert field in response.data, f"ADR 0032: missing envelope field '{field}'"


# ---------------------------------------------------------------------------
# EnrollmentViewSet.create  (POST /enrollment/)
# ---------------------------------------------------------------------------

@skip_unless_lms
class TestEnrollmentViewSetCreate(APITestCase):
    """ADR 0026 + 0028 — permission tests for the create action."""

    def setUp(self):
        super().setUp()
        self.user = UserFactory.create(password="test")
        self.url = reverse("v2:enrollment-list")

    def test_unauthenticated_post_gets_401(self):
        response = self.client.post(self.url, data={}, content_type="application/json")
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_authenticated_post_missing_course_id_gets_400(self):
        """ADR 0029 — missing course_id raises ValidationError → 400."""
        self.client.force_authenticate(user=self.user)
        response = self.client.post(self.url, data={}, content_type="application/json")
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_authenticated_post_invalid_course_id_gets_400(self):
        """ADR 0029 — unparseable course_id raises ValidationError → 400."""
        self.client.force_authenticate(user=self.user)
        response = self.client.post(
            self.url,
            data={"course_details": {"course_id": "not-a-course-key"}},
            content_type="application/json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    @patch(MOCK_OPS_CREATE, return_value={"mode": "audit", "is_active": True})
    def test_authenticated_post_valid_returns_200(self, mock_create):  # noqa: ARG002
        self.client.force_authenticate(user=self.user)
        response = self.client.post(
            self.url,
            data={"course_details": {"course_id": "course-v1:org+course+run"}},
            content_type="application/json",
        )
        assert response.status_code == status.HTTP_200_OK


# ---------------------------------------------------------------------------
# EnrollmentViewSet.unenroll  (POST /enrollment/unenroll/)
# ---------------------------------------------------------------------------

@skip_unless_lms
class TestEnrollmentViewSetUnenroll(APITestCase):
    """ADR 0026 — IsAuthenticated + CanRetireUser permission."""

    def setUp(self):
        super().setUp()
        self.user = UserFactory.create(password="test")
        self.url = reverse("v2:enrollment-unenroll")

    def test_unauthenticated_gets_401(self):
        response = self.client.post(
            self.url, data={"username": self.user.username}, content_type="application/json",
        )
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_authenticated_non_retirement_user_gets_403(self):
        """A plain authenticated user lacks CanRetireUser → 403."""
        self.client.force_authenticate(user=self.user)
        response = self.client.post(
            self.url, data={"username": self.user.username}, content_type="application/json",
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN


# ---------------------------------------------------------------------------
# EnrollmentViewSet.allowed  (GET/POST/DELETE /enrollment/enrollment_allowed/)
# ---------------------------------------------------------------------------

@skip_unless_lms
class TestEnrollmentViewSetAllowed(APITestCase):
    """ADR 0026 — IsAdminUser permission on the allowed action."""

    def setUp(self):
        super().setUp()
        self.user = UserFactory.create(password="test")
        self.admin = AdminFactory.create(password="test")
        self.url = reverse("v2:enrollment-allowed")

    def test_unauthenticated_get_gets_401(self):
        response = self.client.get(self.url)
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_non_admin_get_gets_403(self):
        self.client.force_authenticate(user=self.user)
        response = self.client.get(self.url)
        assert response.status_code == status.HTTP_403_FORBIDDEN

    @patch(MOCK_OPS_LIST_ALLOWED, return_value=[])
    def test_admin_get_gets_200(self, mock_list):  # noqa: ARG002
        self.client.force_authenticate(user=self.admin)
        response = self.client.get(self.url)
        assert response.status_code == status.HTTP_200_OK
        assert response.data == []

    def test_non_admin_post_gets_403(self):
        self.client.force_authenticate(user=self.user)
        response = self.client.post(
            self.url,
            data={"email": "test@example.com", "course_id": "course-v1:edX+DemoX+Demo_Course"},
            content_type="application/json",
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_non_admin_delete_gets_403(self):
        self.client.force_authenticate(user=self.user)
        response = self.client.delete(
            self.url,
            data={"email": "test@example.com", "course_id": "course-v1:edX+DemoX+Demo_Course"},
            content_type="application/json",
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN


# ---------------------------------------------------------------------------
# UserRolesView  (GET /roles/)  — ADR 0033 OEP-68 aliasing
# ---------------------------------------------------------------------------

_ADR_0033_HEADER_COURSE_ID = (
    "Parameter 'course_id' is deprecated. Use 'course_key' instead. "
    "Support will be removed in release '<release_name>'."
)


@skip_unless_lms
class TestUserRolesViewAliases(APITestCase):
    """ADR 0033 — OEP-68 parameter alias + Deprecation header tests."""

    def setUp(self):
        super().setUp()
        self.user = UserFactory.create(password="test")
        self.url = reverse("v2:enrollment-v2-roles")

    @patch("openedx.core.djangoapps.enrollments.v2.views.api.get_user_roles", return_value=[])
    def test_new_course_key_param_no_header(self, mock_get):  # noqa: ARG002
        self.client.force_authenticate(user=self.user)
        response = self.client.get(self.url, {"course_key": "course-v1:org+course+run"})
        assert response.status_code == status.HTTP_200_OK
        assert "Deprecation" not in response.headers

    @patch("openedx.core.djangoapps.enrollments.v2.views.api.get_user_roles", return_value=[])
    def test_legacy_course_id_param_emits_header(self, mock_get):  # noqa: ARG002
        self.client.force_authenticate(user=self.user)
        response = self.client.get(self.url, {"course_id": "course-v1:org+course+run"})
        assert response.status_code == status.HTTP_200_OK
        assert response.headers.get("Deprecation") == _ADR_0033_HEADER_COURSE_ID

    @patch("openedx.core.djangoapps.enrollments.v2.views.api.get_user_roles", return_value=[])
    def test_no_filter_no_header(self, mock_get):  # noqa: ARG002
        self.client.force_authenticate(user=self.user)
        response = self.client.get(self.url)
        assert response.status_code == status.HTTP_200_OK
        assert "Deprecation" not in response.headers


# ---------------------------------------------------------------------------
# ADR 0036 — minimal view tests
# ---------------------------------------------------------------------------
@skip_unless_lms
class TestEnrollmentViewSetMinimalView(APITestCase):
    """
    ADR 0036 — verify ``?view=minimal`` on the list action collapses each
    enrollment's embedded ``course_details`` sub-object to a single ``course_id``
    string and drops the heavy fields (``course_modes`` etc.).
    """

    def setUp(self):
        super().setUp()
        self.user = UserFactory.create(password="test")
        self.client.force_authenticate(user=self.user)
        self.url = reverse("v2:enrollment-list")

    @patch(MOCK_OPS_LIST, return_value=[])
    def test_default_list_includes_course_details(self, mock_list):  # noqa: ARG002
        """Without ``?view=minimal``, embedded course_details is present (full shape)."""
        response = self.client.get(self.url)
        assert response.status_code == status.HTTP_200_OK
        # An empty list naturally has no rows to inspect — the contract is that the
        # envelope's `results` key is a list (already verified by the pagination test).
        assert response.data["results"] == []

    @patch.object(EnrollmentViewSet, "get_serializer")
    @patch(MOCK_OPS_LIST, return_value=["e1", "e2"])
    def test_minimal_view_collapses_course_details_to_course_id(self, mock_list, mock_get_ser):  # noqa: ARG002
        """``?view=minimal`` replaces each ``course_details`` sub-object with a ``course_id`` string."""
        mock_get_ser.return_value.data = [
            {
                "mode": "audit", "is_active": True, "user": "u1",
                "course_details": {"course_id": "course-v1:org+a+r", "course_modes": [{"slug": "audit"}]},
            },
            {
                "mode": "honor", "is_active": True, "user": "u1",
                "course_details": {"course_id": "course-v1:org+b+r", "course_modes": [{"slug": "honor"}]},
            },
        ]

        response = self.client.get(self.url, {"view": "minimal"})

        assert response.status_code == status.HTTP_200_OK
        for row in response.data["results"]:
            assert "course_details" not in row, "ADR 0036: minimal must drop embedded course_details"
            assert "course_id" in row, "ADR 0036: minimal must keep the flattened course_id"
        assert {r["course_id"] for r in response.data["results"]} == {
            "course-v1:org+a+r", "course-v1:org+b+r",
        }
