"""
Tests for the Studio content search REST API.
"""
from __future__ import annotations

import functools
from datetime import datetime, timedelta, timezone
from unittest.mock import ANY, MagicMock, Mock, patch

import ddt
from django.test import override_settings
from rest_framework.test import APIClient

from common.djangoapps.student.auth import update_org_role
from common.djangoapps.student.roles import OrgStaffRole
from openedx.core.djangolib.testing.utils import skip_unless_cms
from xmodule.modulestore.tests.django_utils import SharedModuleStoreTestCase

from .test_models import StudioSearchTestMixin

try:
    # This import errors in the lms because content.search is not an installed app there.
    from .. import api
    from ..models import SearchAccess
except RuntimeError:
    SearchAccess = {}


STUDIO_SEARCH_ENDPOINT_URL = "/api/content_search/v2/studio/"
MOCK_API_KEY_UID = "3203d764-370f-4e99-a917-d47ab7f29739"


def mock_meilisearch(enabled=True):
    """
    Decorator that mocks the required parts of content.search.api to simulate a running Meilisearch instance.
    """
    def decorator(func):
        """
        Overrides settings and patches to enable view tests.
        """
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            with override_settings(
                MEILISEARCH_ENABLED=enabled,
                MEILISEARCH_PUBLIC_URL="http://meilisearch.url",
            ):
                with patch(
                    'openedx.core.djangoapps.content.search.api._get_meili_api_key_uid',
                    return_value=MOCK_API_KEY_UID,
                ):
                    return func(*args, **kwargs)

        return wrapper
    return decorator


def search_rules_for_both_indexes(rule: dict) -> dict:
    """
    The tenant token applies the same access rule to the course index and the library index.
    """
    return {
        "studio_content": rule,
        "studio_library_content": rule,
    }


@ddt.ddt
@skip_unless_cms
@patch("openedx.core.djangoapps.content.search.api._wait_for_meili_task", new=MagicMock(return_value=None))
class StudioSearchViewTest(StudioSearchTestMixin, SharedModuleStoreTestCase):
    """
    General tests for the Studio search REST API.
    """
    def setUp(self):
        super().setUp()
        self.client = APIClient()

        # Clear the Meilisearch client to avoid side effects from other tests
        api.clear_meilisearch_client()

    @mock_meilisearch(enabled=False)
    def test_studio_search_unathenticated_disabled(self):
        """
        Whether or not Meilisearch is enabled, the API endpoint requires authentication.
        """
        result = self.client.get(STUDIO_SEARCH_ENDPOINT_URL)
        assert result.status_code == 401

    @mock_meilisearch(enabled=True)
    def test_studio_search_unathenticated_enabled(self):
        """
        Whether or not Meilisearch is enabled, the API endpoint requires authentication.
        """
        result = self.client.get(STUDIO_SEARCH_ENDPOINT_URL)
        assert result.status_code == 401

    @mock_meilisearch(enabled=False)
    def test_studio_search_disabled(self):
        """
        When Meilisearch is disabled, the Studio search endpoint gives a 404
        """
        self.client.login(username='student', password='student_pass')
        result = self.client.get(STUDIO_SEARCH_ENDPOINT_URL)
        assert result.status_code == 404

    def _mock_generate_tenant_token(self, mock_search_client):
        """
        Return a mocked meilisearch.Client.generate_tenant_token method.
        """
        mock_generate_tenant_token = Mock(return_value='restricted_api_key')
        mock_search_client.return_value = Mock(
            generate_tenant_token=mock_generate_tenant_token,
        )
        return mock_generate_tenant_token

    @mock_meilisearch(enabled=True)
    @patch('openedx.core.djangoapps.content.search.api.MeilisearchClient')
    def test_studio_search_enabled(self, mock_search_client):
        """
        We've implement fine-grained permissions on the meilisearch content,
        so any logged-in user can get a restricted API key for Meilisearch using the REST API.
        """
        self.client.login(username='student', password='student_pass')
        mock_generate_tenant_token = self._mock_generate_tenant_token(mock_search_client)  # noqa: F841
        result = self.client.get(STUDIO_SEARCH_ENDPOINT_URL)
        assert result.status_code == 200
        assert result.data["course_index_name"] == "studio_content"
        assert result.data["library_index_name"] == "studio_library_content"
        # Deprecated key, kept for frontends that predate the index split
        assert result.data["index_name"] == "studio_content"
        assert result.data["url"] == "http://meilisearch.url"
        assert result.data["api_key"] and isinstance(result.data["api_key"], str)  # noqa: PT018
        expiry = datetime.fromisoformat(result.data["expires_at"])
        assert timedelta(days=6) < expiry - datetime.now(timezone.utc) < timedelta(days=8)  # noqa: UP017

    @mock_meilisearch(enabled=True)
    @patch('openedx.core.djangoapps.content.search.api.MeilisearchClient')
    def test_studio_search_student_no_access(self, mock_search_client):
        """
        Users without access to any courses or libraries will have all documents filtered out.
        """
        self.client.login(username='student', password='student_pass')
        mock_generate_tenant_token = self._mock_generate_tenant_token(mock_search_client)
        result = self.client.get(STUDIO_SEARCH_ENDPOINT_URL)
        assert result.status_code == 200
        mock_generate_tenant_token.assert_called_once_with(
            api_key_uid=MOCK_API_KEY_UID,
            search_rules=search_rules_for_both_indexes({
                "filter": "org IN [] OR access_id IN []",
            }),
            expires_at=ANY,
        )

    @mock_meilisearch(enabled=True)
    @patch('openedx.core.djangoapps.content.search.api.MeilisearchClient')
    def test_studio_search_staff(self, mock_search_client):
        """
        Users with global staff access can search any document.
        """
        self.client.login(username='staff', password='staff_pass')
        mock_generate_tenant_token = self._mock_generate_tenant_token(mock_search_client)
        result = self.client.get(STUDIO_SEARCH_ENDPOINT_URL)
        assert result.status_code == 200
        mock_generate_tenant_token.assert_called_once_with(
            api_key_uid=MOCK_API_KEY_UID,
            search_rules=search_rules_for_both_indexes({}),
            expires_at=ANY,
        )

    @mock_meilisearch(enabled=True)
    @patch('openedx.core.djangoapps.content.search.api.MeilisearchClient')
    def test_studio_search_course_staff_access(self, mock_search_client):
        """
        Users with staff or instructor access to a course or library will be limited to these courses/libraries.
        """
        self.client.login(username='course_staff', password='course_staff_pass')
        mock_generate_tenant_token = self._mock_generate_tenant_token(mock_search_client)
        result = self.client.get(STUDIO_SEARCH_ENDPOINT_URL)
        assert result.status_code == 200

        expected_access_ids = list(SearchAccess.objects.filter(
            context_key__in=self.course_user_keys,
        ).only('id').values_list('id', flat=True))
        expected_access_ids.sort(reverse=True)

        mock_generate_tenant_token.assert_called_once_with(
            api_key_uid=MOCK_API_KEY_UID,
            search_rules=search_rules_for_both_indexes({
                "filter": f"org IN [] OR access_id IN {expected_access_ids}",
            }),
            expires_at=ANY,
        )

    @ddt.data(
        'org_staff',
        'org_instr',
    )
    @mock_meilisearch(enabled=True)
    @patch('openedx.core.djangoapps.content.search.api.MeilisearchClient')
    def test_studio_search_org_access(self, username, mock_search_client):
        """
        Users with org access to any courses or libraries will use the org filter.
        """
        self.client.login(username=username, password=f'{username}_pass')
        mock_generate_tenant_token = self._mock_generate_tenant_token(mock_search_client)
        result = self.client.get(STUDIO_SEARCH_ENDPOINT_URL)
        assert result.status_code == 200
        mock_generate_tenant_token.assert_called_once_with(
            api_key_uid=MOCK_API_KEY_UID,
            search_rules=search_rules_for_both_indexes({
                "filter": 'org IN ["org1"] OR access_id IN []',
            }),
            expires_at=ANY,
        )

    @mock_meilisearch(enabled=True)
    @patch('openedx.core.djangoapps.content.search.api.MeilisearchClient')
    def test_studio_search_omit_orgs(self, mock_search_client):
        """
        Grant org access to our staff user to ensure that org's access_ids are omitted from the search filter.
        """
        update_org_role(caller=self.global_staff, role=OrgStaffRole, user=self.course_staff, orgs=['org1'])
        self.client.login(username='course_staff', password='course_staff_pass')
        mock_generate_tenant_token = self._mock_generate_tenant_token(mock_search_client)
        result = self.client.get(STUDIO_SEARCH_ENDPOINT_URL)
        assert result.status_code == 200

        expected_access_ids = list(SearchAccess.objects.filter(
            context_key__in=[key for key in self.course_user_keys if key.org != 'org1'],
        ).only('id').values_list('id', flat=True))
        expected_access_ids.sort(reverse=True)

        mock_generate_tenant_token.assert_called_once_with(
            api_key_uid=MOCK_API_KEY_UID,
            search_rules=search_rules_for_both_indexes({
                "filter": f'org IN ["org1"] OR access_id IN {expected_access_ids}',
            }),
            expires_at=ANY,
        )

    @ddt.data("access_ids", "organizations")
    @mock_meilisearch(enabled=True)
    @patch('openedx.core.djangoapps.content.search.api._get_user_orgs')
    @patch('openedx.core.djangoapps.content.search.api.get_access_ids_for_request')
    @patch('openedx.core.djangoapps.content.search.api.MeilisearchClient')
    def test_studio_search_limits(self, grant_kind, mock_search_client, mock_get_access_ids, mock_get_user_orgs):
        """
        A complete unscoped permission set exceeding the budget returns a scope-required response.
        """
        self.client.login(username='student', password='student_pass')
        mock_generate_tenant_token = self._mock_generate_tenant_token(mock_search_client)

        mock_get_access_ids.return_value = list(range(2000)) if grant_kind == "access_ids" else []
        mock_get_user_orgs.return_value = [f"org{i}" for i in range(1001)] if grant_kind == "organizations" else []
        result = self.client.get(STUDIO_SEARCH_ENDPOINT_URL)
        assert result.status_code == 409
        assert result.data["code"] == "search_scope_required"
        mock_generate_tenant_token.assert_not_called()

    @mock_meilisearch(enabled=True)
    @patch('openedx.core.djangoapps.content.search.api.get_access_ids_for_request')
    @patch('openedx.core.djangoapps.content.search.api.MeilisearchClient')
    def test_scoped_library_after_1000_grants(self, mock_search_client, mock_get_access_ids):
        """Scoped access uses database permission checks, without enumerating grants."""
        self.client.login(username='course_staff', password='course_staff_pass')
        generate = self._mock_generate_tenant_token(mock_search_client)
        library_key = str(next(key for key in self.course_user_keys if str(key).startswith('lib:')))
        result = self.client.get(STUDIO_SEARCH_ENDPOINT_URL, {"library_key": library_key})
        assert result.status_code == 200
        mock_get_access_ids.assert_not_called()
        expiry = datetime.fromisoformat(result.data["expires_at"])
        assert timedelta(minutes=4) < expiry - datetime.now(timezone.utc) < timedelta(minutes=6)  # noqa: UP017
        assert result.data["scope"] == {"library_key": library_key}
        generate.assert_called_once_with(
            api_key_uid=MOCK_API_KEY_UID,
            search_rules={"studio_library_content": {"filter": f'context_key = "{library_key}"'}},
            expires_at=ANY,
        )

    @mock_meilisearch(enabled=True)
    @patch('openedx.core.djangoapps.content.search.api.MeilisearchClient')
    def test_scope_unauthorized(self, mock_search_client):
        """Knowing a library key does not grant access."""
        self.client.login(username='student', password='student_pass')
        generate = self._mock_generate_tenant_token(mock_search_client)
        result = self.client.get(STUDIO_SEARCH_ENDPOINT_URL, {"library_key": "lib:org1:lib1"})
        assert result.status_code == 403
        generate.assert_not_called()

    @mock_meilisearch(enabled=True)
    def test_scope_invalid(self):
        """Course keys and malformed values are rejected."""
        self.client.login(username='course_staff', password='course_staff_pass')
        result = self.client.get(STUDIO_SEARCH_ENDPOINT_URL, {"library_key": "course-v1:org1+course+run"})
        assert result.status_code == 400

    @mock_meilisearch(enabled=True)
    def test_repeated_scope(self):
        """Do not silently choose a repeated query parameter."""
        self.client.login(username='course_staff', password='course_staff_pass')
        result = self.client.get(STUDIO_SEARCH_ENDPOINT_URL, {"library_key": ["lib:org1:lib1", "lib:org1:lib2"]})
        assert result.status_code == 400

    @mock_meilisearch(enabled=True)
    @patch('openedx.core.djangoapps.content.search.api.MeilisearchClient')
    def test_actual_token_byte_budget(self, mock_search_client):
        """The final signed JWT is checked as well as its filter payload."""
        self.client.login(username='student', password='student_pass')
        generate = self._mock_generate_tenant_token(mock_search_client)
        generate.return_value = "x" * 8193
        result = self.client.get(STUDIO_SEARCH_ENDPOINT_URL)
        assert result.status_code == 409
        assert result.data["code"] == "search_scope_required"
        assert "api_key" not in result.data
