"""
Unit tests for AuthoringGradingViewSet (v3).

Single test module covering every ADR applied to the viewset:
  * ADR 0025 / 0026 / 0027 / 0028 — action + permission + routing tests
    (``TestAuthoringGradingViewSetPermissions``, ``TestAuthoringGradingViewSetUpdate``,
    ``TestAuthoringGradingViewSetRouting``)
  * ADR 0029 — standardized error envelope shape tests
    (``TestAuthoringGradingViewSetErrorShape``)

MongoDB-free: every service-layer call (``CourseGradingModel.update_from_json``,
``CourseOverview.course_exists``, ``update_credit_course_requirements.delay``,
and the ``openedx_authz`` permission lookup) is mocked, so these tests run
without a live modulestore or course-overview row.
"""
import json
from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from common.djangoapps.student.tests.factories import UserFactory

COURSE_ID = "course-v1:edX+ToyX+Toy_Course"

# Minimal graders payload accepted by CourseGradingModelSerializer.
_GRADERS_PAYLOAD = [
    {
        "type": "Homework",
        "min_count": 1,
        "drop_count": 0,
        "short_label": "",
        "weight": 100,
        "id": 0,
    },
]

# Fake CourseGradingModel return value: only the field the serializer reads.
_MOCK_GRADING_MODEL = SimpleNamespace(graders=_GRADERS_PAYLOAD)

# CourseOverview.course_exists is called inside ``resolve_course_key()`` (now in
# v3/utils.py), not directly in the view module — so the patch must target the
# utils module's binding, not the view module's.
MOCK_COURSE_EXISTS = (
    "cms.djangoapps.contentstore.rest_api.v3.utils.CourseOverview.course_exists"
)
MOCK_UPDATE_FROM_JSON = (
    "cms.djangoapps.contentstore.rest_api.v3.views.authoring_grading.CourseGradingModel.update_from_json"
)
MOCK_CREDIT_TASK = (
    "cms.djangoapps.contentstore.rest_api.v3.views.authoring_grading."
    "update_credit_course_requirements.delay"
)
# Patch the local module-level binding (imported from openedx.core.djangoapps.authz.decorators)
# — patching the source module would not replace the already-bound reference inside the view.
MOCK_HAS_PERMISSION = (
    "cms.djangoapps.contentstore.rest_api.v3.views.authoring_grading.user_has_course_permission"
)

_REQUIRED_ERROR_FIELDS = ("type", "title", "status", "detail", "instance")


# ===========================================================================
# ADR 0026 — permission boundary tests
# ===========================================================================
class TestAuthoringGradingViewSetPermissions(APITestCase):
    """
    ADR 0026 — permission boundary tests for the partial_update action.
    """

    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.url = reverse(
            "cms.djangoapps.contentstore:v3:authoring_grading-detail",
            kwargs={"course_key": COURSE_ID},
        )

    def test_unauthenticated_patch_returns_401(self):
        """Unauthenticated PATCH must return 401 (IsAuthenticated)."""
        response = self.client.patch(self.url, data={}, content_type="application/json")
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    @patch(MOCK_HAS_PERMISSION, return_value=False)
    @patch(MOCK_COURSE_EXISTS, return_value=True)
    def test_authenticated_without_grading_permission_returns_403(self, mock_exists, mock_perm):  # noqa: ARG002
        """Authenticated user without grading permission must receive 403."""
        user = UserFactory.create()
        self.client.force_authenticate(user=user)
        response = self.client.patch(self.url, data={}, content_type="application/json")
        assert response.status_code == status.HTTP_403_FORBIDDEN


# ===========================================================================
# ADR 0025 / 0028 — action body tests
# ===========================================================================
class TestAuthoringGradingViewSetUpdate(APITestCase):
    """
    Action tests for the partial_update flow.

    All service-layer interactions are mocked so the test exercises the
    routing, serialization, and credit-task wiring without touching MongoDB
    or modulestore.
    """

    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.user = UserFactory.create(is_staff=True)
        self.client.force_authenticate(user=self.user)
        self.url = reverse(
            "cms.djangoapps.contentstore:v3:authoring_grading-detail",
            kwargs={"course_key": COURSE_ID},
        )

    @patch(MOCK_CREDIT_TASK)
    @patch(MOCK_UPDATE_FROM_JSON, return_value=_MOCK_GRADING_MODEL)
    @patch(MOCK_HAS_PERMISSION, return_value=True)
    @patch(MOCK_COURSE_EXISTS, return_value=True)
    def test_patch_with_minimum_grade_credit_fires_credit_task(
        self, mock_exists, mock_perm, mock_update, mock_credit_task,  # noqa: ARG002
    ):
        """``minimum_grade_credit`` in the payload triggers the credit-requirements Celery task."""
        body = {
            "graders": _GRADERS_PAYLOAD,
            "grade_cutoffs": {"A": 0.75, "B": 0.63, "C": 0.57, "D": 0.5},
            "grace_period": {"hours": 12, "minutes": 0},
            "minimum_grade_credit": 0.7,
            "is_credit_course": True,
        }
        response = self.client.patch(self.url, data=json.dumps(body), content_type="application/json")
        assert response.status_code == status.HTTP_200_OK
        mock_update.assert_called_once()
        mock_credit_task.assert_called_once()

    @patch(MOCK_CREDIT_TASK)
    @patch(MOCK_UPDATE_FROM_JSON, return_value=_MOCK_GRADING_MODEL)
    @patch(MOCK_HAS_PERMISSION, return_value=True)
    @patch(MOCK_COURSE_EXISTS, return_value=True)
    def test_patch_without_minimum_grade_credit_skips_credit_task(
        self, mock_exists, mock_perm, mock_update, mock_credit_task,  # noqa: ARG002
    ):
        """Absent ``minimum_grade_credit`` keeps the credit-requirements task unscheduled."""
        body = {
            "graders": _GRADERS_PAYLOAD,
            "grade_cutoffs": {"A": 0.75, "B": 0.63, "C": 0.57, "D": 0.5},
            "grace_period": {"hours": 12, "minutes": 0},
        }
        response = self.client.patch(self.url, data=json.dumps(body), content_type="application/json")
        assert response.status_code == status.HTTP_200_OK
        mock_update.assert_called_once()
        mock_credit_task.assert_not_called()


# ===========================================================================
# ADR 0028 — routing checks
# ===========================================================================
class TestAuthoringGradingViewSetRouting(TestCase):
    """
    Routing checks — confirm the URL namespace and HTTP-method mapping are wired correctly.
    """

    def test_detail_url_resolves(self):
        """v3 router exposes the viewset under ``v3:authoring_grading-detail``."""
        url = reverse(
            "cms.djangoapps.contentstore:v3:authoring_grading-detail",
            kwargs={"course_key": COURSE_ID},
        )
        assert "/api/contentstore/v3/authoring_grading/" in url
        assert COURSE_ID in url

    def test_post_not_allowed(self):
        """The viewset only exposes PATCH (partial_update); POST must return 405."""
        client = APIClient()
        client.force_authenticate(user=UserFactory.create(is_staff=True))
        url = reverse(
            "cms.djangoapps.contentstore:v3:authoring_grading-detail",
            kwargs={"course_key": COURSE_ID},
        )
        response = client.post(url, data={}, content_type="application/json")
        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED


# ===========================================================================
# ADR 0029 — standardized error-response envelope tests
# ===========================================================================
class TestAuthoringGradingViewSetErrorShape(APITestCase):
    """
    ADR 0029 — error response shape regression tests for AuthoringGradingViewSet.

    The envelope is wired in via
    :class:`openedx.core.lib.api.mixins.StandardizedErrorMixin`, which overrides
    DRF's per-view ``get_exception_handler`` to point at
    ``openedx.core.lib.api.exceptions.standardized_error_exception_handler``.

    Scoped to v3 — the project-wide DRF ``EXCEPTION_HANDLER`` setting is
    unchanged, so v0 / v1 / v2 / v4 endpoints continue to return the legacy
    error shape (locked in by ``test_v0_endpoint_unaffected_by_v3_envelope``).
    """

    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.url = reverse(
            "cms.djangoapps.contentstore:v3:authoring_grading-detail",
            kwargs={"course_key": COURSE_ID},
        )

    def test_unauthenticated_patch_returns_standardized_401(self):
        """Unauthenticated PATCH must return 401 with the ADR 0029 envelope."""
        response = self.client.patch(self.url, data={}, content_type="application/json")
        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        for field in _REQUIRED_ERROR_FIELDS:
            assert field in response.data, f"ADR 0029: missing field '{field}'"

    def test_unauthenticated_401_type_uri(self):
        """The ``type`` field for 401 must be the ADR 0029 authn URI."""
        response = self.client.patch(self.url, data={}, content_type="application/json")
        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        assert response.data.get("type") == "https://docs.openedx.org/errors/authn"

    @patch(MOCK_COURSE_EXISTS, return_value=True)
    @patch(MOCK_HAS_PERMISSION, return_value=False)
    def test_non_author_patch_returns_standardized_403(self, mock_perm, mock_exists):  # noqa: ARG002
        """Authenticated non-author PATCH must return 403 with the ADR 0029 envelope."""
        non_author = UserFactory.create()
        self.client.force_authenticate(user=non_author)
        response = self.client.patch(self.url, data={}, content_type="application/json")
        assert response.status_code == status.HTTP_403_FORBIDDEN
        for field in _REQUIRED_ERROR_FIELDS:
            assert field in response.data, f"ADR 0029: missing field '{field}'"

    @patch(MOCK_COURSE_EXISTS, return_value=True)
    @patch(MOCK_HAS_PERMISSION, return_value=False)
    def test_non_author_403_type_uri(self, mock_perm, mock_exists):  # noqa: ARG002
        """The ``type`` field for 403 must be the ADR 0029 authz URI."""
        non_author = UserFactory.create()
        self.client.force_authenticate(user=non_author)
        response = self.client.patch(self.url, data={}, content_type="application/json")
        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.data.get("type") == "https://docs.openedx.org/errors/authz"

    @patch(MOCK_COURSE_EXISTS, return_value=False)
    def test_nonexistent_course_returns_standardized_404(self, mock_exists):  # noqa: ARG002
        """PATCH for a non-existent course must return 404 with the ADR 0029 envelope."""
        staff = UserFactory.create(is_staff=True)
        self.client.force_authenticate(user=staff)
        response = self.client.patch(self.url, data={}, content_type="application/json")
        assert response.status_code == status.HTTP_404_NOT_FOUND
        for field in _REQUIRED_ERROR_FIELDS:
            assert field in response.data, f"ADR 0029: missing field '{field}'"

    @patch(MOCK_COURSE_EXISTS, return_value=False)
    def test_not_found_type_uri(self, mock_exists):  # noqa: ARG002
        """The ``type`` field for 404 must be the ADR 0029 not-found URI."""
        staff = UserFactory.create(is_staff=True)
        self.client.force_authenticate(user=staff)
        response = self.client.patch(self.url, data={}, content_type="application/json")
        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert response.data.get("type") == "https://docs.openedx.org/errors/not-found"

    def test_error_body_has_no_developer_message(self):
        """Error responses must NOT contain old DeveloperErrorViewMixin fields."""
        response = self.client.patch(self.url, data={}, content_type="application/json")
        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        assert "developer_message" not in response.data
        assert "error_code" not in response.data

    def test_instance_field_is_request_path(self):
        """The ``instance`` field must equal the request path."""
        response = self.client.patch(self.url, data={}, content_type="application/json")
        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        assert response.data.get("instance") == self.url

    def test_v0_endpoint_unaffected_by_v3_envelope(self):
        """
        The ADR 0029 envelope must be scoped to v3 — hitting the legacy v0
        ``grading`` endpoint unauthenticated must NOT return the v3 envelope
        (no ``type`` / ``instance`` keys).
        """
        v0_url = reverse(
            "cms.djangoapps.contentstore:v0:cms_api_update_grading",
            # v0 URL uses the legacy ``course_id`` named group; only v3 was renamed
            # to ``course_key`` per OEP-68.
            kwargs={"course_id": COURSE_ID},
        )
        response = self.client.post(v0_url, data={}, content_type="application/json")
        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        assert "type" not in response.data
        assert "instance" not in response.data
