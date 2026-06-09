"""
Tests for XblockViewSet (v1 — ADR 0028, 0026, 0029).

Verifies:
  * Each HTTP method routes to the correct per-verb handler (ADR 0028)
  * Unauthenticated requests return standardized 401 (ADR 0029)
  * Authenticated non-authors return standardized 403 (ADR 0029)
  * ADR 0029 error envelope fields are present and correctly typed
"""
from unittest.mock import patch

from django.http import JsonResponse
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from common.djangoapps.student.tests.factories import GlobalStaffFactory, UserFactory
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase

TEST_LOCATOR = "block-v1:edX+ToyX+Toy_Course+type@problem+block@ba6327f840da49289fb27a9243913478"
PARENT_LOCATOR = "block-v1:edX+ToyX+Toy_Course+type@vertical+block@vert1"

_REQUIRED_ERROR_FIELDS = ("type", "title", "status", "detail", "instance")

_MOCK_RESPONSE = JsonResponse({"locator": TEST_LOCATOR})

_VIEW_MODULE = "cms.djangoapps.contentstore.rest_api.v1.views.xblock"


def _list_url():
    return reverse("cms.djangoapps.contentstore:v1:xblock-list")


def _detail_url():
    return reverse(
        "cms.djangoapps.contentstore:v1:xblock-detail",
        kwargs={"usage_key_string": TEST_LOCATOR},
    )


# ---------------------------------------------------------------------------
# Routing tests
# ---------------------------------------------------------------------------


class XblockViewSetRoutingTest(ModuleStoreTestCase, APITestCase):
    """Verify each HTTP method routes to the correct per-verb handler (ADR 0028)."""

    def setUp(self):
        super().setUp()
        self.staff = GlobalStaffFactory(password='password')
        self.client.force_authenticate(user=self.staff)

    @patch(f"{_VIEW_MODULE}.create_xblock_response", return_value=_MOCK_RESPONSE)
    def test_post_calls_create_xblock_response(self, mock_fn):
        data = {"parent_locator": PARENT_LOCATOR, "category": "html"}
        response = self.client.post(_list_url(), data=data, format="json")
        assert response.status_code == status.HTTP_200_OK
        mock_fn.assert_called_once()
        assert mock_fn.call_args[0][0].method == "POST"

    @patch(f"{_VIEW_MODULE}.retrieve_xblock_response", return_value=_MOCK_RESPONSE)
    def test_get_calls_retrieve_xblock_response(self, mock_fn):
        response = self.client.get(_detail_url())
        assert response.status_code == status.HTTP_200_OK
        mock_fn.assert_called_once()
        assert mock_fn.call_args[0][0].method == "GET"

    @patch(f"{_VIEW_MODULE}.update_xblock_response", return_value=_MOCK_RESPONSE)
    def test_put_calls_update_xblock_response(self, mock_fn):
        data = {"id": TEST_LOCATOR, "data": "<p>Updated</p>"}
        response = self.client.put(_detail_url(), data=data, format="json")
        assert response.status_code == status.HTTP_200_OK
        mock_fn.assert_called_once()
        assert mock_fn.call_args[0][0].method == "PUT"

    @patch(f"{_VIEW_MODULE}.update_xblock_response", return_value=_MOCK_RESPONSE)
    def test_patch_calls_update_xblock_response(self, mock_fn):
        data = {"id": TEST_LOCATOR, "display_name": "New Name"}
        response = self.client.patch(_detail_url(), data=data, format="json")
        assert response.status_code == status.HTTP_200_OK
        mock_fn.assert_called_once()
        assert mock_fn.call_args[0][0].method == "PATCH"

    @patch(f"{_VIEW_MODULE}.delete_xblock_response", return_value=_MOCK_RESPONSE)
    def test_delete_calls_delete_xblock_response(self, mock_fn):
        response = self.client.delete(_detail_url())
        assert response.status_code == status.HTTP_200_OK
        mock_fn.assert_called_once()
        assert mock_fn.call_args[0][0].method == "DELETE"


# ---------------------------------------------------------------------------
# ADR 0029 error-shape tests
# ---------------------------------------------------------------------------


class XblockViewSetErrorShapeTest(ModuleStoreTestCase, APITestCase):
    """Verify ADR 0029 standardized error envelope for auth failures."""

    def setUp(self):
        super().setUp()
        self.non_author = UserFactory.create(password='password')

    def test_unauthenticated_returns_401(self):
        response = self.client.get(_detail_url())
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_unauthenticated_401_has_required_fields(self):
        response = self.client.get(_detail_url())
        data = response.json()
        for field in _REQUIRED_ERROR_FIELDS:
            assert field in data, f"Missing ADR 0029 field: {field}"

    def test_unauthenticated_401_type_uri(self):
        response = self.client.get(_detail_url())
        assert response.json()["type"] == "https://docs.openedx.org/errors/authn"

    def test_non_author_returns_403(self):
        self.client.force_authenticate(user=self.non_author)
        response = self.client.get(_detail_url())
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_non_author_403_has_required_fields(self):
        self.client.force_authenticate(user=self.non_author)
        response = self.client.get(_detail_url())
        data = response.json()
        for field in _REQUIRED_ERROR_FIELDS:
            assert field in data, f"Missing ADR 0029 field: {field}"

    def test_non_author_403_type_uri(self):
        self.client.force_authenticate(user=self.non_author)
        response = self.client.get(_detail_url())
        assert response.json()["type"] == "https://docs.openedx.org/errors/authz"

    def test_error_body_has_no_developer_message(self):
        response = self.client.get(_detail_url())
        data = response.json()
        assert "developer_message" not in data
        assert "error_code" not in data

    def test_instance_field_is_request_path(self):
        response = self.client.get(_detail_url())
        assert response.json()["instance"] == _detail_url()
