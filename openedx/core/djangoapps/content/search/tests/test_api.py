"""
Tests for the Studio content search API.
"""
from __future__ import annotations

import copy

from datetime import datetime, timezone
from unittest.mock import MagicMock, Mock, call, patch
from opaque_keys.edx.keys import UsageKey
from opaque_keys.edx.locator import LibraryCollectionLocator, LibraryContainerLocator

import ddt
import pytest
from django.test import override_settings
from freezegun import freeze_time
from meilisearch.errors import MeilisearchApiError
from openedx_content import api as content_api
from organizations.tests.factories import OrganizationFactory

from common.djangoapps.student.tests.factories import UserFactory
from openedx.core.djangoapps.content_libraries import api as library_api
from openedx.core.djangoapps.content_tagging import api as tagging_api
from openedx.core.djangoapps.content.course_overviews.api import CourseOverview
from openedx.core.djangolib.testing.utils import skip_unless_cms
from xmodule.modulestore.tests.django_utils import TEST_DATA_SPLIT_MODULESTORE, ModuleStoreTestCase


try:
    # This import errors in the lms because content.search is not an installed app there.
    from .. import api
    from ..models import SearchAccess, IncrementalIndexCompleted
except RuntimeError:
    SearchAccess = {}

STUDIO_SEARCH_ENDPOINT_URL = "/api/content_search/v2/studio/"


@ddt.ddt
@skip_unless_cms
@patch("openedx.core.djangoapps.content.search.api._wait_for_meili_task", new=MagicMock(return_value=None))
@patch("openedx.core.djangoapps.content.search.api.MeilisearchClient")
class TestSearchApi(ModuleStoreTestCase):
    """
    Tests for the Studio content search and index API.
    """

    MODULESTORE = TEST_DATA_SPLIT_MODULESTORE

    def setUp(self) -> None:
        # pylint: disable=too-many-statements
        super().setUp()
        self.user = UserFactory.create()
        self.user_id = self.user.id

        self.modulestore_patcher = patch(
            "openedx.core.djangoapps.content.search.api.modulestore", return_value=self.store
        )
        self.addCleanup(self.modulestore_patcher.stop)
        self.modulestore_patcher.start()

        # Clear the Meilisearch client to avoid side effects from other tests
        api.clear_meilisearch_client()

        modified_date = datetime(2024, 5, 6, 7, 8, 9, tzinfo=timezone.utc)
        # Create course
        with freeze_time(modified_date):
            self.course = self.store.create_course(
                "org1",
                "test_course",
                "test_run",
                self.user_id,
                fields={"display_name": "Test Course"},
            )
            course_access, _ = SearchAccess.objects.get_or_create(context_key=self.course.id)
            self.course_block_key = "block-v1:org1+test_course+test_run+type@course+block@course"

            # Create XBlocks
            self.sequential = self.store.create_child(
                self.user_id,
                self.course.location,
                "sequential",
                "test_sequential"
            )
            self.store.create_child(self.user_id, self.sequential.location, "vertical", "test_vertical")
        self.doc_sequential = {
            "id": "block-v1org1test_coursetest_runtypesequentialblocktest_sequential-f702c144",
            "type": "course_block",
            "usage_key": "block-v1:org1+test_course+test_run+type@sequential+block@test_sequential",
            "block_id": "test_sequential",
            "display_name": "sequential",
            "block_type": "sequential",
            "context_key": "course-v1:org1+test_course+test_run",
            "org": "org1",
            "breadcrumbs": [
                {
                    "display_name": "Test Course",
                },
            ],
            "content": {},
            "access_id": course_access.id,
            "modified": modified_date.timestamp(),
        }
        self.doc_vertical = {
            "id": "block-v1org1test_coursetest_runtypeverticalblocktest_vertical-e76a10a4",
            "type": "course_block",
            "usage_key": "block-v1:org1+test_course+test_run+type@vertical+block@test_vertical",
            "block_id": "test_vertical",
            "display_name": "vertical",
            "block_type": "vertical",
            "context_key": "course-v1:org1+test_course+test_run",
            "org": "org1",
            "breadcrumbs": [
                {
                    "display_name": "Test Course",
                },
                {
                    "display_name": "sequential",
                    "usage_key": "block-v1:org1+test_course+test_run+type@sequential+block@test_sequential",
                },
            ],
            "content": {},
            "access_id": course_access.id,
            "modified": modified_date.timestamp(),
        }
        # Make sure the CourseOverview for the course is created:
        CourseOverview.get_from_id(self.course.id)

        # Create a content library:
        self.library = library_api.create_library(
            org=OrganizationFactory.create(short_name="org1"),
            slug="lib",
            title="Library",
        )
        lib_access, _ = SearchAccess.objects.get_or_create(context_key=self.library.key)

        # Populate it with 2 problems, freezing the date so we can verify created date serializes correctly.
        self.created_date = datetime(2023, 4, 5, 6, 7, 8, tzinfo=timezone.utc)
        with freeze_time(self.created_date):
            self.problem1 = library_api.create_library_block(self.library.key, "problem", "p1")
            self.problem2 = library_api.create_library_block(self.library.key, "problem", "p2")
        # Update problem1, freezing the date so we can verify modified date serializes correctly.
        with freeze_time(modified_date):
            library_api.set_library_block_olx(self.problem1.usage_key, "<problem />")

        self.doc_problem1 = {
            "id": "lborg1libproblemp1-a698218e",
            "usage_key": "lb:org1:lib:problem:p1",
            "block_id": "p1",
            "display_name": "Blank Problem",
            "block_type": "problem",
            "context_key": "lib:org1:lib",
            "org": "org1",
            "breadcrumbs": [{"display_name": "Library"}],
            "content": {"problem_types": [], "capa_content": ""},
            "type": "library_block",
            "access_id": lib_access.id,
            "last_published": None,
            "created": self.created_date.timestamp(),
            "modified": modified_date.timestamp(),
            "publish_status": "never",
        }
        self.doc_problem2 = {
            "id": "lborg1libproblemp2-b2f65e29",
            "usage_key": "lb:org1:lib:problem:p2",
            "block_id": "p2",
            "display_name": "Blank Problem",
            "block_type": "problem",
            "context_key": "lib:org1:lib",
            "org": "org1",
            "breadcrumbs": [{"display_name": "Library"}],
            "content": {"problem_types": [], "capa_content": ""},
            "type": "library_block",
            "access_id": lib_access.id,
            "last_published": None,
            "created": self.created_date.timestamp(),
            "modified": self.created_date.timestamp(),
            "publish_status": "never",
        }

        # Create a couple of taxonomies with tags
        self.taxonomyA = tagging_api.create_taxonomy(name="A", export_id="A")
        self.taxonomyB = tagging_api.create_taxonomy(name="B", export_id="B")
        tagging_api.set_taxonomy_orgs(self.taxonomyA, all_orgs=True)
        tagging_api.set_taxonomy_orgs(self.taxonomyB, all_orgs=True)
        tagging_api.add_tag_to_taxonomy(self.taxonomyA, "one")
        tagging_api.add_tag_to_taxonomy(self.taxonomyA, "two")
        tagging_api.add_tag_to_taxonomy(self.taxonomyB, "three")
        tagging_api.add_tag_to_taxonomy(self.taxonomyB, "four")

        # Create a collection:
        self.learning_package = content_api.get_learning_package_by_key(self.library.key)
        with freeze_time(self.created_date):
            self.collection = content_api.create_collection(
                learning_package_id=self.learning_package.id,
                key="MYCOL",
                title="my_collection",
                created_by=None,
                description="my collection description"
            )
            self.collection_key = LibraryCollectionLocator.from_string(
                "lib-collection:org1:lib:MYCOL",
            )
        self.collection_dict = {
            "id": "lib-collectionorg1libmycol-5b647617",
            "block_id": self.collection.key,
            "usage_key": str(self.collection_key),
            "type": "collection",
            "display_name": "my_collection",
            "description": "my collection description",
            "num_children": 0,
            "context_key": "lib:org1:lib",
            "org": "org1",
            "created": self.created_date.timestamp(),
            "modified": self.created_date.timestamp(),
            "access_id": lib_access.id,
            "published": {
                "num_children": 0
            },
            "breadcrumbs": [{"display_name": "Library"}],
        }

        # Create a container:
        with freeze_time(self.created_date):
            self.unit = library_api.create_container(
                library_key=self.library.key,
                container_type=library_api.ContainerType.Unit,
                slug="unit-1",
                title="Unit 1",
                user_id=None,
            )
            self.unit_key = "lct:org1:lib:unit:unit-1"
            self.subsection = library_api.create_container(
                self.library.key,
                container_type=library_api.ContainerType.Subsection,
                slug="subsection-1",
                title="Subsection 1",
                user_id=None,
            )
            library_api.update_container_children(
                self.subsection.container_key,
                [self.unit.container_key],
                None,
            )
            self.subsection_key = "lct:org1:lib:subsection:subsection-1"
            self.section = library_api.create_container(
                self.library.key,
                container_type=library_api.ContainerType.Section,
                slug="section-1",
                title="Section 1",
                user_id=None,
            )
            self.section_key = "lct:org1:lib:section:section-1"
            library_api.update_container_children(
                self.section.container_key,
                [self.subsection.container_key],
                None,
            )

        self.unit_dict = {
            "id": "lctorg1libunitunit-1-e4527f7c",
            "block_id": "unit-1",
            "block_type": "unit",
            "usage_key": self.unit_key,
            "type": "library_container",
            "display_name": "Unit 1",
            # description is not set for containers
            "num_children": 0,
            "content": {
                "child_usage_keys": [],
                "child_display_names": [],
            },
            "publish_status": "never",
            "context_key": "lib:org1:lib",
            "org": "org1",
            "created": self.created_date.timestamp(),
            "modified": self.created_date.timestamp(),
            "last_published": None,
            "access_id": lib_access.id,
            "breadcrumbs": [{"display_name": "Library"}],
            # "published" is not set since we haven't published it yet
        }
        self.subsection_dict = {
            "id": "lctorg1libsubsectionsubsection-1-cf808309",
            "block_id": "subsection-1",
            "block_type": "subsection",
            "usage_key": self.subsection_key,
            "type": "library_container",
            "display_name": "Subsection 1",
            # description is not set for containers
            "num_children": 1,
            "content": {
                "child_usage_keys": ["lct:org1:lib:unit:unit-1"],
                "child_display_names": ["Unit 1"],
            },
            "publish_status": "never",
            "context_key": "lib:org1:lib",
            "org": "org1",
            "created": self.created_date.timestamp(),
            "modified": self.created_date.timestamp(),
            "last_published": None,
            "access_id": lib_access.id,
            "breadcrumbs": [{"display_name": "Library"}],
            # "published" is not set since we haven't published it yet
        }
        self.section_dict = {
            "id": "lctorg1libsectionsection-1-dc4791a4",
            "block_id": "section-1",
            "block_type": "section",
            "usage_key": self.section_key,
            "type": "library_container",
            "display_name": "Section 1",
            # description is not set for containers
            "num_children": 1,
            "content": {
                "child_usage_keys": ["lct:org1:lib:subsection:subsection-1"],
                "child_display_names": ["Subsection 1"],
            },
            "publish_status": "never",
            "context_key": "lib:org1:lib",
            "org": "org1",
            "created": self.created_date.timestamp(),
            "modified": self.created_date.timestamp(),
            "last_published": None,
            "access_id": lib_access.id,
            "breadcrumbs": [{"display_name": "Library"}],
            # "published" is not set since we haven't published it yet
        }

    @override_settings(MEILISEARCH_ENABLED=False)
    def test_reindex_meilisearch_disabled(self, mock_meilisearch) -> None:
        with self.assertRaises(RuntimeError):
            api.rebuild_index()

        mock_meilisearch.return_value.swap_indexes.assert_not_called()

    @override_settings(MEILISEARCH_ENABLED=True)
    def test_reindex_meilisearch(self, mock_meilisearch) -> None:

        # Add tags field to doc, since reindex calls includes tags
        doc_sequential = copy.deepcopy(self.doc_sequential)
        doc_sequential["tags"] = {}
        doc_vertical = copy.deepcopy(self.doc_vertical)
        doc_vertical["tags"] = {}
        doc_problem1 = copy.deepcopy(self.doc_problem1)
        doc_problem1["tags"] = {}
        doc_problem1["collections"] = {'display_name': [], 'key': []}
        doc_problem1["units"] = {'display_name': [], 'key': []}
        doc_problem2 = copy.deepcopy(self.doc_problem2)
        doc_problem2["tags"] = {}
        doc_problem2["collections"] = {'display_name': [], 'key': []}
        doc_problem2["units"] = {'display_name': [], 'key': []}
        doc_collection = copy.deepcopy(self.collection_dict)
        doc_collection["tags"] = {}
        doc_unit = copy.deepcopy(self.unit_dict)
        doc_unit["tags"] = {}
        doc_unit["collections"] = {'display_name': [], 'key': []}
        doc_unit["subsections"] = {'display_name': ['Subsection 1'], 'key': ['lct:org1:lib:subsection:subsection-1']}
        doc_subsection = copy.deepcopy(self.subsection_dict)
        doc_subsection["tags"] = {}
        doc_subsection["collections"] = {'display_name': [], 'key': []}
        doc_subsection["sections"] = {'display_name': ['Section 1'], 'key': ['lct:org1:lib:section:section-1']}
        doc_section = copy.deepcopy(self.section_dict)
        doc_section["tags"] = {}
        doc_section["collections"] = {'display_name': [], 'key': []}

        api.rebuild_index()
        assert mock_meilisearch.return_value.index.return_value.add_documents.call_count == 4
        mock_meilisearch.return_value.index.return_value.add_documents.assert_has_calls(
            [
                call([doc_sequential, doc_vertical]),
                call([doc_problem1, doc_problem2]),
                call([doc_collection]),
                call([doc_unit, doc_subsection, doc_section]),
            ],
            any_order=True,
        )

    @override_settings(MEILISEARCH_ENABLED=True)
    def test_reindex_meilisearch_incremental(self, mock_meilisearch) -> None:

        # Add tags field to doc, since reindex calls includes tags
        doc_sequential = copy.deepcopy(self.doc_sequential)
        doc_sequential["tags"] = {}
        doc_vertical = copy.deepcopy(self.doc_vertical)
        doc_vertical["tags"] = {}
        doc_problem1 = copy.deepcopy(self.doc_problem1)
        doc_problem1["tags"] = {}
        doc_problem1["collections"] = {"display_name": [], "key": []}
        doc_problem1["units"] = {'display_name': [], 'key': []}
        doc_problem2 = copy.deepcopy(self.doc_problem2)
        doc_problem2["tags"] = {}
        doc_problem2["collections"] = {"display_name": [], "key": []}
        doc_problem2["units"] = {'display_name': [], 'key': []}
        doc_collection = copy.deepcopy(self.collection_dict)
        doc_collection["tags"] = {}
        doc_unit = copy.deepcopy(self.unit_dict)
        doc_unit["tags"] = {}
        doc_unit["collections"] = {"display_name": [], "key": []}
        doc_unit["subsections"] = {'display_name': ['Subsection 1'], 'key': ['lct:org1:lib:subsection:subsection-1']}
        doc_subsection = copy.deepcopy(self.subsection_dict)
        doc_subsection["tags"] = {}
        doc_subsection["collections"] = {'display_name': [], 'key': []}
        doc_subsection["sections"] = {'display_name': ['Section 1'], 'key': ['lct:org1:lib:section:section-1']}
        doc_section = copy.deepcopy(self.section_dict)
        doc_section["tags"] = {}
        doc_section["collections"] = {'display_name': [], 'key': []}

        api.rebuild_index(incremental=True)
        assert mock_meilisearch.return_value.index.return_value.add_documents.call_count == 4
        mock_meilisearch.return_value.index.return_value.add_documents.assert_has_calls(
            [
                call([doc_sequential, doc_vertical]),
                call([doc_problem1, doc_problem2]),
                call([doc_collection]),
                call([doc_unit, doc_subsection, doc_section]),
            ],
            any_order=True,
        )

        # Now we simulate interruption by passing this function to the status_cb argument
        def simulated_interruption(message):
            # this exception prevents courses from being indexed
            if "Indexing courses" in message:
                raise Exception("Simulated interruption")

        with pytest.raises(Exception, match="Simulated interruption"):
            api.rebuild_index(simulated_interruption, incremental=True)

        # three more calls due to collections and containers
        assert mock_meilisearch.return_value.index.return_value.add_documents.call_count == 7
        assert IncrementalIndexCompleted.objects.all().count() == 1
        api.rebuild_index(incremental=True)
        assert IncrementalIndexCompleted.objects.all().count() == 0
        # one missing course indexed
        assert mock_meilisearch.return_value.index.return_value.add_documents.call_count == 8

    @override_settings(MEILISEARCH_ENABLED=True)
    def test_reset_meilisearch_index(self, mock_meilisearch) -> None:
        api.reset_index()
        mock_meilisearch.return_value.swap_indexes.assert_called_once()
        mock_meilisearch.return_value.create_index.assert_called_once()
        mock_meilisearch.return_value.delete_index.call_count = 2
        api.reset_index()
        mock_meilisearch.return_value.delete_index.call_count = 4

    @override_settings(MEILISEARCH_ENABLED=True)
    def test_init_meilisearch_index(self, mock_meilisearch) -> None:
        # Test index already exists
        api.init_index()
        mock_meilisearch.return_value.swap_indexes.assert_not_called()
        mock_meilisearch.return_value.create_index.assert_not_called()
        mock_meilisearch.return_value.delete_index.assert_not_called()

        # Test index already exists and has no documents
        mock_meilisearch.return_value.get_stats.return_value = 0
        api.init_index()
        mock_meilisearch.return_value.swap_indexes.assert_not_called()
        mock_meilisearch.return_value.create_index.assert_not_called()
        mock_meilisearch.return_value.delete_index.assert_not_called()

        mock_meilisearch.return_value.get_index.side_effect = [
            MeilisearchApiError("Testing reindex", Mock(text='{"code":"index_not_found"}')),
            MeilisearchApiError("Testing reindex", Mock(text='{"code":"index_not_found"}')),
            Mock(created_at=1),
            Mock(created_at=1),
            Mock(created_at=1),
        ]
        api.init_index()
        mock_meilisearch.return_value.swap_indexes.assert_called_once()
        mock_meilisearch.return_value.create_index.assert_called_once()
        mock_meilisearch.return_value.delete_index.call_count = 2

    @override_settings(MEILISEARCH_ENABLED=True)
    @patch(
        "openedx.core.djangoapps.content.search.api.searchable_doc_for_collection",
        Mock(side_effect=Exception("Failed to generate document")),
    )
    def test_reindex_meilisearch_collection_error(self, mock_meilisearch) -> None:

        mock_logger = Mock()
        api.rebuild_index(mock_logger)
        assert call(
            [self.collection_dict]
        ) not in mock_meilisearch.return_value.index.return_value.add_documents.mock_calls
        mock_logger.assert_any_call(
            f"Error indexing collection {self.collection}: Failed to generate document"
        )

    @override_settings(MEILISEARCH_ENABLED=True)
    @patch(
        "openedx.core.djangoapps.content.search.api.searchable_doc_for_container",
        Mock(side_effect=Exception("Failed to generate document")),
    )
    def test_reindex_meilisearch_container_error(self, mock_meilisearch) -> None:

        mock_logger = Mock()
        api.rebuild_index(mock_logger)
        assert call(
            [self.unit_dict]
        ) not in mock_meilisearch.return_value.index.return_value.add_documents.mock_calls
        mock_logger.assert_any_call(
            "Error indexing container unit-1: Failed to generate document"
        )

    @override_settings(MEILISEARCH_ENABLED=True)
    def test_reindex_meilisearch_library_block_error(self, mock_meilisearch) -> None:

        # Add tags field to doc, since reindex calls includes tags
        doc_sequential = copy.deepcopy(self.doc_sequential)
        doc_sequential["tags"] = {}
        doc_vertical = copy.deepcopy(self.doc_vertical)
        doc_vertical["tags"] = {}
        doc_problem2 = copy.deepcopy(self.doc_problem2)
        doc_problem2["tags"] = {}
        doc_problem2["collections"] = {'display_name': [], 'key': []}
        doc_problem2["units"] = {'display_name': [], 'key': []}

        orig_from_component = library_api.LibraryXBlockMetadata.from_component

        def mocked_from_component(lib_key, component):
            # Simulate an error when processing problem 1
            if component.key == 'xblock.v1:problem:p1':
                raise Exception('Error')

            return orig_from_component(lib_key, component)

        with patch.object(
            library_api.LibraryXBlockMetadata,
            "from_component",
            new=mocked_from_component,
        ):
            api.rebuild_index()

        mock_meilisearch.return_value.index.return_value.add_documents.assert_has_calls(
            [
                call([doc_sequential, doc_vertical]),
                # Problem 1 should not be indexed
                call([doc_problem2]),
            ],
            any_order=True,
        )

        # Check that the sorting-related settings were updated to support sorting on the expected fields
        mock_meilisearch.return_value.index.return_value.update_sortable_attributes.assert_called_with([
            "display_name",
            "created",
            "modified",
            "last_published",
        ])
        mock_meilisearch.return_value.index.return_value.update_ranking_rules.assert_called_with([
            "sort",
            "words",
            "typo",
            "proximity",
            "attribute",
            "exactness",
        ])

    @ddt.data(
        True,
        False
    )
    @override_settings(MEILISEARCH_ENABLED=True)
    def test_index_xblock_metadata(self, recursive, mock_meilisearch) -> None:
        """
        Test indexing an XBlock.
        """
        api.upsert_xblock_index_doc(self.sequential.usage_key, recursive=recursive)

        if recursive:
            expected_docs = [self.doc_sequential, self.doc_vertical]
        else:
            expected_docs = [self.doc_sequential]

        mock_meilisearch.return_value.index.return_value.update_documents.assert_called_once_with(expected_docs)

    @override_settings(MEILISEARCH_ENABLED=True)
    def test_no_index_excluded_xblocks(self, mock_meilisearch) -> None:
        api.upsert_xblock_index_doc(UsageKey.from_string(self.course_block_key))

        mock_meilisearch.return_value.index.return_value.update_document.assert_not_called()

    @override_settings(MEILISEARCH_ENABLED=True)
    def test_index_xblock_tags(self, mock_meilisearch) -> None:
        """
        Test indexing an XBlock with tags.
        """
        # Tag XBlock (these internally call `upsert_content_object_tags_index_doc`)
        tagging_api.tag_object(str(self.sequential.usage_key), self.taxonomyA, ["one", "two"])
        tagging_api.tag_object(str(self.sequential.usage_key), self.taxonomyB, ["three", "four"])

        # Build expected docs with tags at each stage
        doc_sequential_with_tags1 = {
            "id": self.doc_sequential["id"],
            "tags": {
                'taxonomy': ['A'],
                'level0': ['A > one', 'A > two']
            }
        }
        doc_sequential_with_tags2 = {
            "id": self.doc_sequential["id"],
            "tags": {
                'taxonomy': ['A', 'B'],
                'level0': ['A > one', 'A > two', 'B > four', 'B > three']
            }
        }

        assert mock_meilisearch.return_value.index.return_value.update_documents.call_count == 2
        mock_meilisearch.return_value.index.return_value.update_documents.assert_has_calls(
            [
                call([doc_sequential_with_tags1]),
                call([doc_sequential_with_tags2]),
            ],
            any_order=True,
        )

    @override_settings(MEILISEARCH_ENABLED=True)
    def test_delete_index_xblock(self, mock_meilisearch) -> None:
        """
        Test deleting an XBlock doc and its children docs from the index.
        """
        api.delete_index_doc(self.sequential.usage_key, delete_children=True)

        mock_meilisearch.return_value.index.return_value.delete_document.assert_called_once_with(
            self.doc_sequential['id']
        )

        mock_meilisearch.return_value.index.return_value.delete_documents.assert_called_once_with(
            filter=f'breadcrumbs.usage_key = "{self.sequential.usage_key}"'
        )

    @override_settings(MEILISEARCH_ENABLED=True)
    def test_index_library_block_metadata(self, mock_meilisearch) -> None:
        """
        Test indexing a Library Block.
        """
        api.upsert_library_block_index_doc(self.problem1.usage_key)

        mock_meilisearch.return_value.index.return_value.update_documents.assert_called_once_with([self.doc_problem1])

    @override_settings(MEILISEARCH_ENABLED=True)
    def test_index_library_block_tags(self, mock_meilisearch) -> None:
        """
        Test indexing an Library Block with tags.
        """

        # Tag XBlock (these internally call `upsert_block_tags_index_docs`)
        tagging_api.tag_object(str(self.problem1.usage_key), self.taxonomyA, ["one", "two"])
        tagging_api.tag_object(str(self.problem1.usage_key), self.taxonomyB, ["three", "four"])

        # Build expected docs with tags at each stage
        doc_problem_with_tags1 = {
            "id": self.doc_problem1["id"],
            "tags": {
                'taxonomy': ['A'],
                'level0': ['A > one', 'A > two']
            }
        }
        doc_problem_with_tags2 = {
            "id": self.doc_problem1["id"],
            "tags": {
                'taxonomy': ['A', 'B'],
                'level0': ['A > one', 'A > two', 'B > four', 'B > three']
            }
        }

        assert mock_meilisearch.return_value.index.return_value.update_documents.call_count == 2
        mock_meilisearch.return_value.index.return_value.update_documents.assert_has_calls(
            [
                call([doc_problem_with_tags1]),
                call([doc_problem_with_tags2]),
            ],
            any_order=True,
        )

    @override_settings(MEILISEARCH_ENABLED=True)
    def test_index_library_block_and_collections(self, mock_meilisearch) -> None:
        """
        Test indexing an Library Block and the Collections it's in.
        """
        # Create collections (these internally call `upsert_library_collection_index_doc`)
        created_date = datetime(2023, 5, 6, 7, 8, 9, tzinfo=timezone.utc)
        with freeze_time(created_date):
            collection1 = library_api.create_library_collection(
                self.library.key,
                collection_key="COL1",
                title="Collection 1",
                created_by=None,
                description="First Collection",
            )

            collection2 = library_api.create_library_collection(
                self.library.key,
                collection_key="COL2",
                title="Collection 2",
                created_by=None,
                description="Second Collection",
            )

        # Add Problem1 to both Collections (these internally call `upsert_item_collections_index_docs` and
        # `upsert_library_collection_index_doc`)
        # (adding in reverse order to test sorting of collection tag)
        updated_date = datetime(2023, 6, 7, 8, 9, 10, tzinfo=timezone.utc)
        with freeze_time(updated_date):
            for collection in (collection2, collection1):
                library_api.update_library_collection_items(
                    self.library.key,
                    collection_key=collection.key,
                    opaque_keys=[
                        self.problem1.usage_key,
                    ],
                )

        # Build expected docs at each stage
        lib_access, _ = SearchAccess.objects.get_or_create(context_key=self.library.key)
        doc_collection1_created = {
            "id": "lib-collectionorg1libcol1-283a79c9",
            "block_id": collection1.key,
            "usage_key": f"lib-collection:org1:lib:{collection1.key}",
            "type": "collection",
            "display_name": "Collection 1",
            "description": "First Collection",
            "num_children": 0,
            "context_key": "lib:org1:lib",
            "org": "org1",
            "created": created_date.timestamp(),
            "modified": created_date.timestamp(),
            "access_id": lib_access.id,
            "published": {
                "num_children": 0
            },
            "breadcrumbs": [{"display_name": "Library"}],
        }
        doc_collection2_created = {
            "id": "lib-collectionorg1libcol2-46823d4d",
            "block_id": collection2.key,
            "usage_key": f"lib-collection:org1:lib:{collection2.key}",
            "type": "collection",
            "display_name": "Collection 2",
            "description": "Second Collection",
            "num_children": 0,
            "context_key": "lib:org1:lib",
            "org": "org1",
            "created": created_date.timestamp(),
            "modified": created_date.timestamp(),
            "access_id": lib_access.id,
            "published": {
                "num_children": 0
            },
            "breadcrumbs": [{"display_name": "Library"}],
        }
        doc_collection2_updated = {
            "id": "lib-collectionorg1libcol2-46823d4d",
            "block_id": collection2.key,
            "usage_key": f"lib-collection:org1:lib:{collection2.key}",
            "type": "collection",
            "display_name": "Collection 2",
            "description": "Second Collection",
            "num_children": 1,
            "context_key": "lib:org1:lib",
            "org": "org1",
            "created": created_date.timestamp(),
            "modified": updated_date.timestamp(),
            "access_id": lib_access.id,
            "published": {
                "num_children": 0
            },
            "breadcrumbs": [{"display_name": "Library"}],
        }
        doc_collection1_updated = {
            "id": "lib-collectionorg1libcol1-283a79c9",
            "block_id": collection1.key,
            "usage_key": f"lib-collection:org1:lib:{collection1.key}",
            "type": "collection",
            "display_name": "Collection 1",
            "description": "First Collection",
            "num_children": 1,
            "context_key": "lib:org1:lib",
            "org": "org1",
            "created": created_date.timestamp(),
            "modified": updated_date.timestamp(),
            "access_id": lib_access.id,
            "published": {
                "num_children": 0
            },
            "breadcrumbs": [{"display_name": "Library"}],
        }
        doc_problem_with_collection1 = {
            "id": self.doc_problem1["id"],
            "collections": {
                "display_name": ["Collection 2"],
                "key": ["COL2"],
            },
        }
        doc_problem_with_collection2 = {
            "id": self.doc_problem1["id"],
            "collections": {
                "display_name": ["Collection 1", "Collection 2"],
                "key": ["COL1", "COL2"],
            },
        }

        assert mock_meilisearch.return_value.index.return_value.update_documents.call_count == 6
        mock_meilisearch.return_value.index.return_value.update_documents.assert_has_calls(
            [
                call([doc_collection1_created]),
                call([doc_collection2_created]),
                call([doc_collection2_updated]),
                call([doc_collection1_updated]),
                call([doc_problem_with_collection1]),
                call([doc_problem_with_collection2]),
            ],
            any_order=True,
        )

    @override_settings(MEILISEARCH_ENABLED=True)
    def test_delete_index_library_block(self, mock_meilisearch) -> None:
        """
        Test deleting a Library Block doc from the index.
        """
        api.delete_index_doc(self.problem1.usage_key)

        mock_meilisearch.return_value.index.return_value.delete_document.assert_called_once_with(
            self.doc_problem1['id']
        )

    @override_settings(MEILISEARCH_ENABLED=True)
    def test_delete_docs_with_context_key(self, mock_meilisearch) -> None:
        """
        Test deleting a all Block docs from the index using context_key.
        """
        api.delete_docs_with_context_key(self.course.id)

        mock_meilisearch.return_value.index.return_value.delete_documents.assert_called_once_with(
            filter=f'context_key = "{self.course.id}"'
        )

    @override_settings(MEILISEARCH_ENABLED=True)
    def test_index_content_library_metadata(self, mock_meilisearch) -> None:
        """
        Test indexing a whole content library.
        """
        api.upsert_content_library_index_docs(self.library.key)

        mock_meilisearch.return_value.index.return_value.update_documents.assert_called_once_with(
            [self.doc_problem1, self.doc_problem2]
        )

    @override_settings(MEILISEARCH_ENABLED=True)
    def test_index_tags_in_collections(self, mock_meilisearch) -> None:
        # Tag collection
        tagging_api.tag_object(str(self.collection_key), self.taxonomyA, ["one", "two"])
        tagging_api.tag_object(str(self.collection_key), self.taxonomyB, ["three", "four"])

        # Build expected docs with tags at each stage
        doc_collection_with_tags1 = {
            "id": "lib-collectionorg1libmycol-5b647617",
            "tags": {
                'taxonomy': ['A'],
                'level0': ['A > one', 'A > two']
            }
        }
        doc_collection_with_tags2 = {
            "id": "lib-collectionorg1libmycol-5b647617",
            "tags": {
                'taxonomy': ['A', 'B'],
                'level0': ['A > one', 'A > two', 'B > four', 'B > three']
            }
        }

        assert mock_meilisearch.return_value.index.return_value.update_documents.call_count == 2
        mock_meilisearch.return_value.index.return_value.update_documents.assert_has_calls(
            [
                call([doc_collection_with_tags1]),
                call([doc_collection_with_tags2]),
            ],
            any_order=True,
        )

    @override_settings(MEILISEARCH_ENABLED=True)
    def test_delete_collection(self, mock_meilisearch) -> None:
        """
        Test soft-deleting, restoring, and hard-deleting a collection.
        """
        # Add a component to the collection
        updated_date = datetime(2023, 6, 7, 8, 9, 10, tzinfo=timezone.utc)
        with freeze_time(updated_date):
            library_api.update_library_collection_items(
                self.library.key,
                collection_key=self.collection.key,
                opaque_keys=[
                    self.problem1.usage_key,
                    self.unit.container_key
                ],
            )

        doc_collection = copy.deepcopy(self.collection_dict)
        doc_collection["num_children"] = 2
        doc_collection["modified"] = updated_date.timestamp()
        doc_problem_with_collection = {
            "id": self.doc_problem1["id"],
            "collections": {
                "display_name": [self.collection.title],
                "key": [self.collection.key],
            },
        }
        doc_unit_with_collection = {
            "id": self.unit_dict["id"],
            "collections": {
                "display_name": [self.collection.title],
                "key": [self.collection.key],
            },
        }

        # Should update the collection and its component
        assert mock_meilisearch.return_value.index.return_value.update_documents.call_count == 3
        mock_meilisearch.return_value.index.return_value.update_documents.assert_has_calls(
            [
                call([doc_collection]),
                call([doc_problem_with_collection]),
                call([doc_unit_with_collection]),
            ],
            any_order=True,
        )
        mock_meilisearch.return_value.index.reset_mock()

        # Soft-delete the collection
        content_api.delete_collection(
            self.collection.learning_package_id,
            self.collection.key,
        )

        doc_problem_without_collection = {
            "id": self.doc_problem1["id"],
            "collections": {'display_name': [], 'key': []},
        }
        doc_unit_without_collection = {
            "id": self.unit_dict["id"],
            "collections": {'display_name': [], 'key': []},
        }

        # Should delete the collection document
        mock_meilisearch.return_value.index.return_value.delete_document.assert_called_once_with(
            self.collection_dict["id"],
        )
        # ...and update the component's "collections" field
        mock_meilisearch.return_value.index.return_value.update_documents.assert_has_calls(
            [
                call([doc_problem_without_collection]),
                call([doc_unit_without_collection]),
            ],
            any_order=True,
        )
        mock_meilisearch.return_value.index.reset_mock()

        # We need to mock get_document here so that when we restore the collection below, meilisearch knows the
        # collection is being re-added, so it will update its components too.
        mock_meilisearch.return_value.get_index.return_value.get_document.return_value = None

        # Restore the collection
        restored_date = datetime(2023, 8, 9, 10, 11, 12, tzinfo=timezone.utc)
        with freeze_time(restored_date):
            content_api.restore_collection(
                self.collection.learning_package_id,
                self.collection.key,
            )

        doc_collection = copy.deepcopy(self.collection_dict)
        doc_collection["num_children"] = 2
        doc_collection["modified"] = restored_date.timestamp()

        # Should update the collection and its component's "collections" field
        assert mock_meilisearch.return_value.index.return_value.update_documents.call_count == 3
        mock_meilisearch.return_value.index.return_value.update_documents.assert_has_calls(
            [
                call([doc_collection]),
                call([doc_problem_with_collection]),
                call([doc_unit_with_collection]),
            ],
            any_order=True,
        )
        mock_meilisearch.return_value.index.reset_mock()

        # Hard-delete the collection
        content_api.delete_collection(
            self.collection.learning_package_id,
            self.collection.key,
            hard_delete=True,
        )

        # Should delete the collection document
        mock_meilisearch.return_value.index.return_value.delete_document.assert_called_once_with(
            self.collection_dict["id"],
        )
        # ...and cascade delete updates the "collections" field for the associated components
        mock_meilisearch.return_value.index.return_value.update_documents.assert_has_calls(
            [
                call([doc_problem_without_collection]),
                call([doc_unit_without_collection]),
            ],
            any_order=True,
        )

    @ddt.data(
        "unit",
        "subsection",
        "section",
    )
    @override_settings(MEILISEARCH_ENABLED=True)
    def test_delete_index_container(self, container_type, mock_meilisearch) -> None:
        """
        Test delete a container index.
        """
        container = getattr(self, container_type)
        container_dict = getattr(self, f"{container_type}_dict")
        update_doc_calls = []

        def clear_contents(data: dict):
            return {
                **data,
                "num_children": 0,
                "content": {
                    "child_usage_keys": [],
                    "child_display_names": [],
                },
            }
        if container_type == "unit":
            update_doc_calls.append(call([clear_contents(self.subsection_dict)]))
        elif container_type == "subsection":
            update_doc_calls.append(call([clear_contents(self.section_dict)]))
            update_doc_calls.append(call([{
                'id': self.unit_dict['id'],
                'subsections': {'display_name': [], 'key': []},
            }]))
        elif container_type == "section":
            update_doc_calls.append(call([{
                'id': self.subsection_dict['id'],
                'sections': {'display_name': [], 'key': []},
            }]))

        library_api.delete_container(container.container_key)

        mock_meilisearch.return_value.index.return_value.delete_document.assert_called_once_with(
            container_dict["id"],
        )
        # Parent containers index data is updated.
        if update_doc_calls:
            mock_meilisearch.return_value.index.return_value.update_documents.assert_has_calls(
                update_doc_calls,
                any_order=True,
            )

        # Restore
        library_api.restore_container(container.container_key)
        if container_type == "unit":
            update_doc_calls.append(call([self.subsection_dict]))
        elif container_type == "subsection":
            update_doc_calls.append(call([self.section_dict]))
            update_doc_calls.append(call([{
                'id': self.unit_dict['id'],
                'subsections': {
                    'display_name': [self.subsection_dict['display_name']],
                    'key': [self.subsection_key],
                },
            }]))
        elif container_type == "section":
            update_doc_calls.append(call([{
                'id': self.subsection_dict['id'],
                'sections': {
                    'display_name': [self.section_dict['display_name']],
                    'key': [self.section_key],
                },
            }]))
        # Parent containers index data is updated on restore again.
        if update_doc_calls:
            mock_meilisearch.return_value.index.return_value.update_documents.assert_has_calls(
                update_doc_calls,
                any_order=True,
            )

    @ddt.data(
        "unit",
        "subsection",
        "section",
    )
    @override_settings(MEILISEARCH_ENABLED=True)
    def test_index_library_container_metadata(self, container_type, mock_meilisearch) -> None:
        """
        Test indexing a Library Container.
        """
        container = getattr(self, container_type)
        container_dict = getattr(self, f"{container_type}_dict")
        api.upsert_library_container_index_doc(container.container_key)

        mock_meilisearch.return_value.index.return_value.update_documents.assert_called_once_with([container_dict])

    @ddt.data(
        ("unit", "lctorg1libunitunit-1-e4527f7c"),
        ("subsection", "lctorg1libsubsectionsubsection-1-cf808309"),
        ("section", "lctorg1libsectionsection-1-dc4791a4"),
    )
    @ddt.unpack
    @override_settings(MEILISEARCH_ENABLED=True)
    def test_index_tags_in_containers(self, container_type, container_id, mock_meilisearch) -> None:
        container_key = getattr(self, f"{container_type}_key")

        # Tag container
        tagging_api.tag_object(container_key, self.taxonomyA, ["one", "two"])
        tagging_api.tag_object(container_key, self.taxonomyB, ["three", "four"])

        # Build expected docs with tags at each stage
        doc_unit_with_tags1 = {
            "id": container_id,
            "tags": {
                'taxonomy': ['A'],
                'level0': ['A > one', 'A > two']
            }
        }
        doc_unit_with_tags2 = {
            "id": container_id,
            "tags": {
                'taxonomy': ['A', 'B'],
                'level0': ['A > one', 'A > two', 'B > four', 'B > three']
            }
        }

        assert mock_meilisearch.return_value.index.return_value.update_documents.call_count == 2
        mock_meilisearch.return_value.index.return_value.update_documents.assert_has_calls(
            [
                call([doc_unit_with_tags1]),
                call([doc_unit_with_tags2]),
            ],
            any_order=True,
        )

    @override_settings(MEILISEARCH_ENABLED=True)
    def test_block_in_units(self, mock_meilisearch) -> None:
        with freeze_time(self.created_date):
            library_api.update_container_children(
                LibraryContainerLocator.from_string(self.unit_key),
                [self.problem1.usage_key],
                None,
            )

        doc_block_with_units = {
            "id": self.doc_problem1["id"],
            "units": {
                "display_name": [self.unit.display_name],
                "key": [self.unit_key],
            },
        }
        new_unit_dict = {
            **self.unit_dict,
            "num_children": 1,
            'content': {
                'child_usage_keys': [self.doc_problem1["usage_key"]],
                'child_display_names': [self.doc_problem1["display_name"]],
            }
        }

        assert mock_meilisearch.return_value.index.return_value.update_documents.call_count == 2
        mock_meilisearch.return_value.index.return_value.update_documents.assert_has_calls(
            [
                call([doc_block_with_units]),
                call([new_unit_dict]),
            ],
            any_order=True,
        )

    @override_settings(MEILISEARCH_ENABLED=True)
    def test_units_in_subsection(self, mock_meilisearch) -> None:
        with freeze_time(self.created_date):
            library_api.update_container_children(
                LibraryContainerLocator.from_string(self.subsection_key),
                [LibraryContainerLocator.from_string(self.unit_key)],
                None,
            )

        doc_block_with_subsections = {
            "id": self.unit_dict["id"],
            "subsections": {
                "display_name": [self.subsection.display_name],
                "key": [self.subsection_key],
            },
        }
        new_subsection_dict = {
            **self.subsection_dict,
            "num_children": 1,
            'content': {
                'child_usage_keys': [self.unit_key],
                'child_display_names': [self.unit.display_name]
            }
        }
        assert mock_meilisearch.return_value.index.return_value.update_documents.call_count == 2
        mock_meilisearch.return_value.index.return_value.update_documents.assert_has_calls(
            [
                call([doc_block_with_subsections]),
                call([new_subsection_dict]),
            ],
            any_order=True,
        )

    @override_settings(MEILISEARCH_ENABLED=True)
    def test_section_in_usbsections(self, mock_meilisearch) -> None:
        with freeze_time(self.created_date):
            library_api.update_container_children(
                LibraryContainerLocator.from_string(self.section_key),
                [LibraryContainerLocator.from_string(self.subsection_key)],
                None,
            )

        doc_block_with_sections = {
            "id": self.subsection_dict["id"],
            "sections": {
                "display_name": [self.section.display_name],
                "key": [self.section_key],
            },
        }
        new_section_dict = {
            **self.section_dict,
            "num_children": 1,
            'content': {
                'child_usage_keys': [self.subsection_key],
                'child_display_names': [self.subsection.display_name],
            }
        }
        assert mock_meilisearch.return_value.index.return_value.update_documents.call_count == 2
        mock_meilisearch.return_value.index.return_value.update_documents.assert_has_calls(
            [
                call([doc_block_with_sections]),
                call([new_section_dict]),
            ],
            any_order=True,
        )

    @override_settings(MEILISEARCH_ENABLED=True)
    def test_fetch_block_types(self, mock_meilisearch):
        from openedx.core.djangoapps.content.search.api import fetch_block_types

        mock_index = mock_meilisearch.return_value.get_index.return_value
        fetch_block_types('context_key = test')

        mock_index.search.assert_called_once_with(
            "",
            {
                "facets": ["block_type"],
                "filter": ['context_key = test'],
                "limit": 0,
            }
        )

    @override_settings(MEILISEARCH_ENABLED=True)
    def test_get_all_blocks_from_context(self, mock_meilisearch):
        from openedx.core.djangoapps.content.search.api import get_all_blocks_from_context

        mock_index = mock_meilisearch.return_value.get_index.return_value
        expected_result = [
            {"usage_key": "block-v1:test+type@html+block@1"},
            {"usage_key": "block-v1:test+type@video+block@2"},
        ]

        # Simulate two pages: one with results and one empty (while loop ends)
        mock_index.search.side_effect = [
            {
                "hits": expected_result,
                "estimatedTotalHits": 1200,
            },
            {
                "hits": [],
                "estimatedTotalHits": 1200,
            },
        ]

        result = list(get_all_blocks_from_context(
            context_key="course-v1:TestOrg+TestCourse+TestRun",
            extra_attributes_to_retrieve=["display_name"],
        ))

        assert result == expected_result
        assert mock_index.search.call_count == 2
        mock_index.search.assert_any_call(
            "",
            {
                "filter": ['context_key = "course-v1:TestOrg+TestCourse+TestRun"'],
                "limit": 1000,
                "offset": 0,
                "attributesToRetrieve": ["usage_key", "display_name"],
            }
        )

        mock_index.search.assert_any_call(
            "",
            {
                "filter": ['context_key = "course-v1:TestOrg+TestCourse+TestRun"'],
                "limit": 1000,
                "offset": 1000,
                "attributesToRetrieve": ["usage_key", "display_name"],
            }
        )
