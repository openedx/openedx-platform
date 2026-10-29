"""Real CMS authorization and opt-in signed-token enforcement."""
import os
from datetime import UTC, datetime, timedelta
from unittest.mock import Mock, patch
from uuid import uuid4

import jwt
import pytest
from django.test import RequestFactory, TestCase
from meilisearch import Client
from meilisearch.errors import MeilisearchApiError
from organizations.models import Organization
from rest_framework.exceptions import PermissionDenied

from common.djangoapps.student.tests.factories import UserFactory
from openedx.core.djangoapps.content_libraries.models import ContentLibrary, ContentLibraryPermission
from openedx.core.djangolib.testing.utils import skip_unless_cms

from .. import api
from ..models import SearchAccess, has_library_search_access


@skip_unless_cms
class PermissionScopeIntegrationTests(TestCase):
    """Use the platform's permission queryset, without an authorization double."""

    def setUp(self):
        super().setUp()
        self.user = UserFactory(is_staff=False)
        org = Organization.objects.create(name="Permission validation", short_name="scopevalidation")
        self.libraries = ContentLibrary.objects.bulk_create([
            ContentLibrary(org=org, slug=f"library{number:04}") for number in range(1501)
        ])
        ContentLibraryPermission.objects.bulk_create([
            ContentLibraryPermission(library=library, user=self.user, access_level="read")
            for library in self.libraries
        ])
        SearchAccess.objects.bulk_create([
            SearchAccess(context_key=library.library_key) for library in self.libraries
        ])
        self.selected = self.libraries[-1]
        self.request = RequestFactory().get("/")
        self.request.user = self.user

    def test_exact_scope_after_1501_real_grants_and_revocation(self):
        key = self.selected.library_key
        assert has_library_search_access(self.user, key)
        client = Mock(generate_tenant_token=Mock(return_value="small-token"))
        with patch.object(api, "_get_meilisearch_client", return_value=client), patch.object(
            api, "_get_meili_api_key_uid", return_value="search-key-uid",
        ):
            result = api.generate_user_token_for_studio_search(self.request, str(key))
            assert result["scope"] == {"library_key": str(key)}
            assert result["authorized_indexes"] == [api.STUDIO_LIBRARY_INDEX_NAME]
            assert client.generate_tenant_token.call_args.kwargs["search_rules"] == {
                api.STUDIO_LIBRARY_INDEX_NAME: {"filter": f'context_key = "{key}"'},
            }
            with pytest.raises(api.StudioSearchScopeRequired):
                api.generate_user_token_for_studio_search(self.request)
            ContentLibraryPermission.objects.filter(library=self.selected, user=self.user).delete()
            with pytest.raises(PermissionDenied):
                api.generate_user_token_for_studio_search(self.request, str(key))

    def test_live_tenant_token_scope_and_expiry(self):
        url, master_key = os.getenv("OPENEDX_SEARCH_LIVE_URL"), os.getenv("OPENEDX_SEARCH_LIVE_KEY")
        if not url or not master_key:
            self.skipTest("Set disposable Meilisearch endpoint and master key")
        name = "permission_scope_" + uuid4().hex
        admin = Client(url, master_key)
        admin.wait_for_task(admin.create_index(name, {"primaryKey": "id"}).task_uid)
        key = None
        try:
            index = admin.index(name)
            admin.wait_for_task(index.update_filterable_attributes(["context_key"]).task_uid)
            selected = str(self.selected.library_key)
            other = str(self.libraries[0].library_key)
            admin.wait_for_task(index.add_documents([
                {"id": "allowed", "context_key": selected},
                {"id": "other", "context_key": other},
            ]).task_uid)
            key = admin.create_key({
                "description": "Disposable permission test", "actions": ["search"],
                "indexes": [name], "expiresAt": None,
            })
            signer = Client(url, key.key)
            with patch.object(api, "STUDIO_LIBRARY_INDEX_NAME", name), patch.object(
                api, "_get_meilisearch_client", return_value=signer,
            ), patch.object(api, "_get_meili_api_key_uid", return_value=key.uid):
                result = api.generate_user_token_for_studio_search(self.request, selected)
            scoped = Client(url, result["api_key"]).index(name)
            assert [doc["id"] for doc in scoped.search("")["hits"]] == ["allowed"]
            assert scoped.search("", {"filter": f'context_key = "{other}"'})["hits"] == []
            assert scoped.search("", {"facets": ["context_key"]})["facetDistribution"] == {
                "context_key": {selected: 1},
            }
            expiry = datetime.fromisoformat(result["expires_at"])
            assert timedelta(seconds=290) < expiry - datetime.now(UTC) <= timedelta(seconds=300)
            # The SDK refuses expired tokens. Sign an expired test JWT directly
            # to verify that the engine itself enforces expiration.
            claims = jwt.decode(result["api_key"], key.key, algorithms=["HS256"])
            claims["exp"] = int(datetime.now(UTC).timestamp()) - 1
            expired = jwt.encode(claims, key.key, algorithm="HS256")
            with pytest.raises(MeilisearchApiError):
                Client(url, expired).index(name).search("")
        finally:
            if key is not None:
                admin.delete_key(key.uid)
            admin.wait_for_task(admin.delete_index(name).task_uid)
