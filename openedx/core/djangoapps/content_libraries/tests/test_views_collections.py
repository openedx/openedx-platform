"""
Tests Library Collections REST API views
"""

from __future__ import annotations
import ddt

from openedx_content.models_api import Collection
from opaque_keys.edx.locator import LibraryLocatorV2

from openedx.core.djangolib.testing.utils import skip_unless_cms
from openedx.core.djangoapps.content_libraries import api
from openedx.core.djangoapps.content_libraries.tests.base import ContentLibrariesRestApiTest
from openedx.core.djangoapps.content_libraries.models import ContentLibrary
from common.djangoapps.student.tests.factories import UserFactory

URL_PREFIX = '/api/libraries/v2/{lib_key}/'
URL_LIB_COLLECTIONS = URL_PREFIX + 'collections/'
URL_LIB_COLLECTION = URL_LIB_COLLECTIONS + '{collection_key}/'
URL_LIB_COLLECTION_RESTORE = URL_LIB_COLLECTIONS + '{collection_key}/restore/'
URL_LIB_COLLECTION_COMPONENTS = URL_LIB_COLLECTION + 'items/'


@ddt.ddt
@skip_unless_cms  # Content Library Collections REST API is only available in Studio
class ContentLibraryCollectionsViewsTest(ContentLibrariesRestApiTest):
    """
    Tests for Content Library Collection REST API Views
    """

    def setUp(self):
        super().setUp()

        # Create Content Libraries
        self._create_library("test-lib-col-1", "Test Library 1")
        self._create_library("test-lib-col-2", "Test Library 2")
        self.lib1 = ContentLibrary.objects.get(slug="test-lib-col-1")
        self.lib2 = ContentLibrary.objects.get(slug="test-lib-col-2")

        # Create Content Library Collections
        self.col1 = api.create_library_collection(
            self.lib1.library_key,
            "COL1",
            title="Collection 1",
            created_by=self.user.id,
            description="Description for Collection 1",
        )

        self.col2 = api.create_library_collection(
            self.lib1.library_key,
            "COL2",
            title="Collection 2",
            created_by=self.user.id,
            description="Description for Collection 2",
        )
        self.col3 = api.create_library_collection(
            self.lib2.library_key,
            "COL3",
            title="Collection 3",
            created_by=self.user.id,
            description="Description for Collection 3",
        )

        # Create some library blocks
        self.lib1_problem_block = self._add_block_to_library(
            self.lib1.library_key, "problem", "problem1",
        )
        self.lib1_html_block = self._add_block_to_library(
            self.lib1.library_key, "html", "html1",
        )
        self.lib2_problem_block = self._add_block_to_library(
            self.lib2.library_key, "problem", "problem2",
        )
        self.lib2_html_block = self._add_block_to_library(
            self.lib2.library_key, "html", "html2",
        )
        self.unit = self._create_container(self.lib1.library_key, "unit", display_name="Unit 1", slug=None)
        self.subsection = self._create_container(
            self.lib1.library_key,
            "subsection",
            display_name="Subsection 1",
            slug=None,
        )
        self.section = self._create_container(self.lib1.library_key, "section", display_name="Section 1", slug=None)

    def test_get_library_collection(self):
        """
        Test retrieving a Content Library Collection
        """
        resp = self.client.get(
            URL_LIB_COLLECTION.format(lib_key=self.lib2.library_key, collection_key=self.col3.key)
        )

        # Check that correct Content Library Collection data retrieved
        expected_collection = {
            "title": "Collection 3",
            "description": "Description for Collection 3",
        }
        assert resp.status_code == 200
        self.assertDictContainsEntries(resp.data, expected_collection)

        # Check that a random user without permissions cannot access Content Library Collection
        random_user = UserFactory.create(username="Random", email="random@example.com")
        with self.as_user(random_user):
            resp = self.client.get(
                URL_LIB_COLLECTION.format(lib_key=self.lib2.library_key, collection_key=self.col3.key)
            )
            assert resp.status_code == 403

    def test_get_invalid_library_collection(self):
        """
        Test retrieving a an invalid Content Library Collection or one that does not exist
        """
        # Fetch collection that belongs to a different library, it should fail
        resp = self.client.get(
            URL_LIB_COLLECTION.format(lib_key=self.lib1.library_key, collection_key=self.col3.key)
        )

        assert resp.status_code == 404

        # Fetch collection with invalid ID provided, it should fail
        resp = self.client.get(
            URL_LIB_COLLECTION.format(lib_key=self.lib1.library_key, collection_key='123')
        )

        assert resp.status_code == 404

        # Fetch collection with invalid library_key provided, it should fail
        resp = self.client.get(
            URL_LIB_COLLECTION.format(lib_key=123, collection_key='123')
        )
        assert resp.status_code == 404

    def test_list_library_collections(self):
        """
        Test listing Content Library Collections
        """
        resp = self.client.get(URL_LIB_COLLECTIONS.format(lib_key=self.lib1.library_key))

        # Check that the correct collections are listed
        assert resp.status_code == 200
        assert len(resp.data["results"]) == 2
        expected_collections = [
            {"key": "COL1", "title": "Collection 1", "description": "Description for Collection 1"},
            {"key": "COL2", "title": "Collection 2", "description": "Description for Collection 2"},
        ]
        for collection, expected in zip(resp.data["results"], expected_collections):
            self.assertDictContainsEntries(collection, expected)

        # Check that a random user without permissions cannot access Content Library Collections
        random_user = UserFactory.create(username="Random", email="random@example.com")
        with self.as_user(random_user):
            resp = self.client.get(URL_LIB_COLLECTIONS.format(lib_key=self.lib1.library_key))
            assert resp.status_code == 403

    def test_list_invalid_library_collections(self):
        """
        Test listing invalid Content Library Collections
        """
        non_existing_key = LibraryLocatorV2.from_string("lib:DoesNotExist:NE1")
        resp = self.client.get(URL_LIB_COLLECTIONS.format(lib_key=non_existing_key))

        assert resp.status_code == 404

        # List collections with invalid library_key provided, it should fail
        resp = resp = self.client.get(URL_LIB_COLLECTIONS.format(lib_key=123))
        assert resp.status_code == 404

    def test_create_library_collection(self):
        """
        Test creating a Content Library Collection
        """
        post_data = {
            "title": "Collection 4",
            "description": "Description for Collection 4",
        }
        resp = self.client.post(
            URL_LIB_COLLECTIONS.format(lib_key=self.lib1.library_key), post_data, format="json"
        )
        # Check that the new Content Library Collection is returned in response and created in DB
        assert resp.status_code == 200
        post_data["key"] = 'collection-4'
        self.assertDictContainsEntries(resp.data, post_data)

        created_collection = Collection.objects.get(id=resp.data["id"])
        self.assertIsNotNone(created_collection)

        # Check that user with read only access cannot create new Content Library Collection
        reader = UserFactory.create(username="Reader", email="reader@example.com")
        self._add_user_by_email(self.lib1.library_key, reader.email, access_level="read")

        with self.as_user(reader):
            post_data = {
                "title": "Collection 5",
                "description": "Description for Collection 5",
            }
            resp = self.client.post(
                URL_LIB_COLLECTIONS.format(lib_key=self.lib1.library_key), post_data, format="json"
            )

            assert resp.status_code == 403

    def test_create_collection_same_key(self):
        """
        Test collection creation with same key
        """
        post_data = {
            "title": "Same Collection",
            "description": "Description for Collection 4",
        }
        self.client.post(
            URL_LIB_COLLECTIONS.format(lib_key=self.lib1.library_key), post_data, format="json"
        )

        for i in range(100):
            resp = self.client.post(
                URL_LIB_COLLECTIONS.format(lib_key=self.lib1.library_key), post_data, format="json"
            )
            expected_data = {
                "key": f"same-collection-{i + 1}",
                "title": "Same Collection",
                "description": "Description for Collection 4",
            }

            assert resp.status_code == 200
            self.assertDictContainsEntries(resp.data, expected_data)

    def test_create_invalid_library_collection(self):
        """
        Test creating an invalid Content Library Collection
        """
        post_data_missing_title = {
            "key": "COL_KEY",
        }
        resp = self.client.post(
            URL_LIB_COLLECTIONS.format(lib_key=self.lib1.library_key), post_data_missing_title, format="json"
        )

        assert resp.status_code == 400

        post_data_missing_key = {
            "title": "Collection 4",
        }
        resp = self.client.post(
            URL_LIB_COLLECTIONS.format(lib_key=self.lib1.library_key), post_data_missing_key, format="json"
        )

        assert resp.status_code == 400

        # Create collection with an existing collection.key; it should fail
        post_data_existing_key = {
            "key": self.col1.key,
            "title": "Collection 4",
        }
        resp = self.client.post(
            URL_LIB_COLLECTIONS.format(lib_key=self.lib1.library_key),
            post_data_existing_key,
            format="json"
        )
        assert resp.status_code == 400

        # Create collection with invalid library_key provided, it should fail
        resp = self.client.post(
            URL_LIB_COLLECTIONS.format(lib_key=123),
            {**post_data_missing_title, **post_data_missing_key},
            format="json"
        )
        assert resp.status_code == 404

    def test_update_library_collection(self):
        """
        Test updating a Content Library Collection
        """
        patch_data = {
            "title": "Collection 3 Updated",
        }
        resp = self.client.patch(
            URL_LIB_COLLECTION.format(lib_key=self.lib2.library_key, collection_key=self.col3.key),
            patch_data,
            format="json"
        )

        # Check that updated Content Library Collection is returned in response and updated in DB
        assert resp.status_code == 200
        self.assertDictContainsEntries(resp.data, patch_data)

        created_collection = Collection.objects.get(id=resp.data["id"])
        self.assertIsNotNone(created_collection)
        self.assertEqual(created_collection.title, patch_data["title"])

        # Check that user with read only access cannot update a Content Library Collection
        reader = UserFactory.create(username="Reader", email="reader@example.com")
        self._add_user_by_email(self.lib1.library_key, reader.email, access_level="read")

        with self.as_user(reader):
            patch_data = {
                "title": "Collection 3 should not update",
            }
            resp = self.client.patch(
                URL_LIB_COLLECTION.format(lib_key=self.lib2.library_key, collection_key=self.col3.key),
                patch_data,
                format="json"
            )

            assert resp.status_code == 403

    def test_update_invalid_library_collection(self):
        """
        Test updating an invalid Content Library Collection or one that does not exist
        """
        patch_data = {
            "title": "Collection 3 Updated",
        }
        # Update collection that belongs to a different library, it should fail
        resp = self.client.patch(
            URL_LIB_COLLECTION.format(lib_key=self.lib1.library_key, collection_key=self.col3.key),
            patch_data,
            format="json"
        )

        assert resp.status_code == 404

        # Update collection with invalid ID provided, it should fail
        resp = self.client.patch(
            URL_LIB_COLLECTION.format(lib_key=self.lib1.library_key, collection_key='123'),
            patch_data,
            format="json"
        )

        assert resp.status_code == 404

        # Update collection with invalid library_key provided, it should fail
        resp = self.client.patch(
            URL_LIB_COLLECTION.format(lib_key=123, collection_key=self.col3.key),
            patch_data,
            format="json"
        )
        assert resp.status_code == 404

    def test_delete_library_collection(self):
        """
        Test soft-deleting and restoring a Content Library Collection
        """
        resp = self.client.delete(
            URL_LIB_COLLECTION.format(lib_key=self.lib2.library_key, collection_key=self.col3.key)
        )
        assert resp.status_code == 204

        resp = self.client.get(
            URL_LIB_COLLECTION.format(lib_key=self.lib2.library_key, collection_key=self.col3.key)
        )
        assert resp.status_code == 404

        resp = self.client.post(
            URL_LIB_COLLECTION_RESTORE.format(lib_key=self.lib2.library_key, collection_key=self.col3.key)
        )
        assert resp.status_code == 204

        resp = self.client.get(
            URL_LIB_COLLECTION.format(lib_key=self.lib2.library_key, collection_key=self.col3.key)
        )
        # Check that correct Content Library Collection data retrieved
        expected_collection = {
            "title": "Collection 3",
            "description": "Description for Collection 3",
        }
        assert resp.status_code == 200
        self.assertDictContainsEntries(resp.data, expected_collection)

    def test_get_components(self):
        """
        Retrieving components is not supported by the REST API;
        use Meilisearch instead.
        """
        resp = self.client.get(
            URL_LIB_COLLECTION_COMPONENTS.format(
                lib_key=self.lib1.library_key,
                collection_key=self.col1.key,
            ),
        )
        assert resp.status_code == 405

    def test_update_components(self):
        """
        Test adding and removing components from a collection.
        """
        # Add two components to col1
        resp = self.client.patch(
            URL_LIB_COLLECTION_COMPONENTS.format(
                lib_key=self.lib1.library_key,
                collection_key=self.col1.key,
            ),
            data={
                "usage_keys": [
                    self.lib1_problem_block["id"],
                    self.lib1_html_block["id"],
                ]
            }
        )
        assert resp.status_code == 200
        assert resp.data == {"count": 2}

        # Remove one of the added components from col1
        resp = self.client.delete(
            URL_LIB_COLLECTION_COMPONENTS.format(
                lib_key=self.lib1.library_key,
                collection_key=self.col1.key,
            ),
            data={
                "usage_keys": [
                    self.lib1_problem_block["id"],
                ]
            }
        )
        assert resp.status_code == 200
        assert resp.data == {"count": 1}

    def test_update_containers(self):
        """
        Test adding and removing containers from a collection.
        """
        # Add containers to col1
        resp = self.client.patch(
            URL_LIB_COLLECTION_COMPONENTS.format(
                lib_key=self.lib1.library_key,
                collection_key=self.col1.key,
            ),
            data={
                "usage_keys": [
                    self.unit["id"],
                    self.subsection["id"],
                    self.section["id"],
                ]
            }
        )
        assert resp.status_code == 200
        assert resp.data == {"count": 3}

        # Remove one of the added containers from col1
        resp = self.client.delete(
            URL_LIB_COLLECTION_COMPONENTS.format(
                lib_key=self.lib1.library_key,
                collection_key=self.col1.key,
            ),
            data={
                "usage_keys": [
                    self.unit["id"],
                    self.subsection["id"],
                ]
            }
        )
        assert resp.status_code == 200
        assert resp.data == {"count": 2}

    @ddt.data("patch", "delete")
    def test_update_components_wrong_collection(self, method):
        """
        Collection must belong to the requested library.
        """
        resp = getattr(self.client, method)(
            URL_LIB_COLLECTION_COMPONENTS.format(
                lib_key=self.lib2.library_key,
                collection_key=self.col1.key,
            ),
            data={
                "usage_keys": [
                    self.lib1_problem_block["id"],
                ]
            }
        )
        assert resp.status_code == 404

    @ddt.data("patch", "delete")
    def test_update_components_missing_data(self, method):
        """
        List of usage keys must contain at least one item.
        """
        resp = getattr(self.client, method)(
            URL_LIB_COLLECTION_COMPONENTS.format(
                lib_key=self.lib2.library_key,
                collection_key=self.col3.key,
            ),
        )
        assert resp.status_code == 400
        assert resp.data == {
            "usage_keys": ["This field is required."],
        }

    @ddt.data("patch", "delete")
    def test_update_components_from_another_library(self, method):
        """
        Adding/removing components from another library raises a 404.
        """
        resp = getattr(self.client, method)(
            URL_LIB_COLLECTION_COMPONENTS.format(
                lib_key=self.lib2.library_key,
                collection_key=self.col3.key,
            ),
            data={
                "usage_keys": [
                    self.lib1_problem_block["id"],
                    self.lib1_html_block["id"],
                ]
            }
        )
        assert resp.status_code == 404

    @ddt.data("patch", "delete")
    def test_update_components_permissions(self, method):
        """
        Check that a random user without permissions cannot update a Content Library Collection's components.
        """
        random_user = UserFactory.create(username="Random", email="random@example.com")
        with self.as_user(random_user):
            resp = getattr(self.client, method)(
                URL_LIB_COLLECTION_COMPONENTS.format(
                    lib_key=self.lib1.library_key,
                    collection_key=self.col1.key,
                ),
            )
            assert resp.status_code == 403
