"""
Tests for the Studio content search REST API.
"""
from __future__ import annotations

import functools
from unittest.mock import ANY, MagicMock, Mock, patch

import ddt
from django.test import override_settings
from opaque_keys.edx.keys import CourseKey
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
        assert result.data["index_name"] == "studio_content"
        assert result.data["url"] == "http://meilisearch.url"
        assert result.data["api_key"] and isinstance(result.data["api_key"], str)  # noqa: PT018

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
            search_rules={
                "studio_content": {
                    "filter": "org IN [] OR access_id IN []",
                }
            },
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
            search_rules={
                "studio_content": {}
            },
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
            search_rules={
                "studio_content": {
                    "filter": f"org IN [] OR access_id IN {expected_access_ids}",
                }
            },
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
            search_rules={
                "studio_content": {
                    "filter": "org IN ['org1'] OR access_id IN []",
                }
            },
            expires_at=ANY,
        )

    @mock_meilisearch(enabled=True)
    @patch('openedx.core.djangoapps.content.search.api.get_authz_org_keys')
    @patch('openedx.core.djangoapps.content.search.api.MeilisearchClient')
    def test_studio_search_authz_org_glob_access(self, mock_search_client, mock_authz_orgs):
        """
        A user with only an org-wide (glob) authz course grant -- and no legacy
        role -- is covered by the org clause (openedx/openedx-authz#417). The
        student has no legacy access, so 'org1' here comes purely from the authz
        org union wired into ``_get_user_orgs``.
        """
        mock_authz_orgs.return_value = {'org1'}

        self.client.login(username='student', password='student_pass')
        mock_generate_tenant_token = self._mock_generate_tenant_token(mock_search_client)
        result = self.client.get(STUDIO_SEARCH_ENDPOINT_URL)
        assert result.status_code == 200
        # The authz org union is fed into _get_user_orgs, which is passed as omit_orgs
        # to the access_ids query, so org1's courses are covered by the org clause.
        mock_authz_orgs.assert_called_once()
        mock_generate_tenant_token.assert_called_once_with(
            api_key_uid=MOCK_API_KEY_UID,
            search_rules={
                "studio_content": {
                    "filter": "org IN ['org1'] OR access_id IN []",
                }
            },
            expires_at=ANY,
        )

    @mock_meilisearch(enabled=True)
    @patch('openedx.core.djangoapps.content.search.api.authz_has_platform_access')
    @patch('openedx.core.djangoapps.content.search.api.MeilisearchClient')
    def test_studio_search_authz_platform_glob_access(self, mock_search_client, mock_platform_access):
        """
        A user with a platform-wide (``course-v1:*``) authz course grant is the
        authz analogue of global staff and can search any document, so the
        access filter is empty -- no ``org``/``access_id`` restriction clause
        (openedx/openedx-authz#417). The student has no legacy access, so the
        empty filter here comes purely from the platform-access short-circuit.
        """
        mock_platform_access.return_value = True

        self.client.login(username='student', password='student_pass')
        mock_generate_tenant_token = self._mock_generate_tenant_token(mock_search_client)
        result = self.client.get(STUDIO_SEARCH_ENDPOINT_URL)
        assert result.status_code == 200
        mock_platform_access.assert_called_once()
        mock_generate_tenant_token.assert_called_once_with(
            api_key_uid=MOCK_API_KEY_UID,
            search_rules={
                "studio_content": {}
            },
            expires_at=ANY,
        )

    @mock_meilisearch(enabled=True)
    @patch('openedx.core.djangoapps.content.search.models.get_authz_platform_course_keys')
    @patch('openedx.core.djangoapps.content.search.api.get_authz_platform_orgs')
    @patch('openedx.core.djangoapps.content.search.api.authz_has_platform_access')
    @patch('openedx.core.djangoapps.content.search.api.MeilisearchClient')
    def test_studio_search_authz_platform_glob_expansion(
        self, mock_search_client, mock_platform_access, mock_platform_orgs, mock_platform_courses,
    ):
        """
        When the authz course authoring flag is NOT globally on, a platform-wide
        (``course-v1:*``) grant is not see-everything -- it expands to just the
        orgs/courses that carry a force-on override (openedx/openedx-authz#417
        follow-up). This exercises the assembled filter at the token endpoint:
        the force-on org lands in the ``org`` clause (via ``_get_user_orgs``) and a
        force-on course in a *different* org lands in the ``access_id`` clause (via
        ``get_access_ids_for_request``), composing into one filter.
        """
        # Not see-everything: global switch is off, so the grant expands per-scope.
        mock_platform_access.return_value = False
        # A force-on org override surfaces this org in the org clause.
        mock_platform_orgs.return_value = {'platformorg'}
        # A force-on course override in a DIFFERENT (non-omitted) org surfaces this
        # course in the access_id clause. Give it a real SearchAccess row so its id resolves.
        expansion_course = CourseKey.from_string('course-v1:PlatformCourseOrg+PC101+2026')
        search_access = SearchAccess.objects.create(context_key=expansion_course)
        mock_platform_courses.return_value = {str(expansion_course)}

        self.client.login(username='student', password='student_pass')
        mock_generate_tenant_token = self._mock_generate_tenant_token(mock_search_client)
        result = self.client.get(STUDIO_SEARCH_ENDPOINT_URL)
        assert result.status_code == 200
        # The student has no legacy access, so both clauses come purely from the
        # platform-grant expansion: org from the force-on org, access_id from the
        # force-on course.
        mock_generate_tenant_token.assert_called_once_with(
            api_key_uid=MOCK_API_KEY_UID,
            search_rules={
                "studio_content": {
                    "filter": f"org IN ['platformorg'] OR access_id IN [{search_access.id}]",
                }
            },
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
            search_rules={
                "studio_content": {
                    "filter": f"org IN ['org1'] OR access_id IN {expected_access_ids}",
                }
            },
            expires_at=ANY,
        )

    @mock_meilisearch(enabled=True)
    @patch('openedx.core.djangoapps.content.search.api._get_user_orgs')
    @patch('openedx.core.djangoapps.content.search.api.get_access_ids_for_request')
    @patch('openedx.core.djangoapps.content.search.api.MeilisearchClient')
    def test_studio_search_limits(self, mock_search_client, mock_get_access_ids, mock_get_user_orgs):
        """
        Users with access to many courses/libraries or orgs will only be able to search content
        from the most recent 1_000 courses/libraries and orgs.
        """
        self.client.login(username='student', password='student_pass')
        mock_generate_tenant_token = self._mock_generate_tenant_token(mock_search_client)

        mock_get_access_ids.return_value = list(range(2000))
        expected_access_ids = list(range(1000))

        mock_get_user_orgs.return_value = [
            f"studio-search-org{x}" for x in range(2000)
        ]
        expected_user_orgs = [
            f"studio-search-org{x}" for x in range(1000)
        ]

        result = self.client.get(STUDIO_SEARCH_ENDPOINT_URL)
        assert result.status_code == 200
        mock_get_access_ids.assert_called_once()
        mock_generate_tenant_token.assert_called_once_with(
            api_key_uid=MOCK_API_KEY_UID,
            search_rules={
                "studio_content": {
                    "filter": f"org IN {expected_user_orgs} OR access_id IN {expected_access_ids}",
                }
            },
            expires_at=ANY,
        )
