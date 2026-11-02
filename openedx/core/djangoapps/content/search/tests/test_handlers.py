"""
Tests for the search index update handlers
"""
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from django.test import LiveServerTestCase, override_settings
from freezegun import freeze_time
from organizations.tests.factories import OrganizationFactory

from common.djangoapps.student.tests.factories import UserFactory
from openedx.core.djangoapps.content_libraries import api as library_api
from openedx.core.djangolib.testing.utils import skip_unless_cms
from xmodule.modulestore.tests.django_utils import (
    TEST_DATA_SPLIT_MODULESTORE,
    ModuleStoreTestCase,
    ImmediateOnCommitMixin,
)


try:
    # This import errors in the lms because content.search is not an installed app there.
    from .. import api
    from ..models import SearchAccess
except RuntimeError:
    SearchAccess = {}


@patch("openedx.core.djangoapps.content.search.api._wait_for_meili_task", new=MagicMock(return_value=None))
@patch("openedx.core.djangoapps.content.search.api.MeilisearchClient")
@override_settings(MEILISEARCH_ENABLED=True)
@skip_unless_cms
class TestUpdateIndexHandlers(ImmediateOnCommitMixin, ModuleStoreTestCase, LiveServerTestCase):
    """
    Test that the search index is updated when XBlocks and Library Blocks are modified
    """

    MODULESTORE = TEST_DATA_SPLIT_MODULESTORE

    def setUp(self):
        super().setUp()
        # Create user
        self.user = UserFactory.create()
        self.user_id = self.user.id

        self.orgA = OrganizationFactory.create(short_name="orgA")

        self.patcher = patch("openedx.core.djangoapps.content_tagging.tasks.modulestore", return_value=self.store)
        self.addCleanup(self.patcher.stop)
        self.patcher.start()

        api.clear_meilisearch_client()  # Clear the Meilisearch client to avoid leaking state from other tests

    def test_create_delete_xblock(self, meilisearch_client):
        # Create course
        course = self.store.create_course(
            self.orgA.short_name,
            "test_course",
            "test_run",
            self.user_id,
            fields={"display_name": "Test Course"},
        )
        course_access, _ = SearchAccess.objects.get_or_create(context_key=course.id)

        # Create XBlocks
        created_date = datetime(2023, 4, 5, 6, 7, 8, tzinfo=timezone.utc)
        with freeze_time(created_date):
            sequential = self.store.create_child(self.user_id, course.location, "sequential", "test_sequential")
        doc_sequential = {
            "id": "block-v1orgatest_coursetest_runtypesequentialblocktest_sequential-0cdb9395",
            "type": "course_block",
            "usage_key": "block-v1:orgA+test_course+test_run+type@sequential+block@test_sequential",
            "block_id": "test_sequential",
            "display_name": "sequential",
            "block_type": "sequential",
            "context_key": "course-v1:orgA+test_course+test_run",
            "org": "orgA",
            "breadcrumbs": [
                {
                    "display_name": "Test Course",
                },
            ],
            "content": {},
            "access_id": course_access.id,
            "modified": created_date.timestamp(),
        }

        meilisearch_client.return_value.index.return_value.update_documents.assert_called_with([doc_sequential])

        with freeze_time(created_date):
            vertical = self.store.create_child(self.user_id, sequential.location, "vertical", "test_vertical")
        doc_vertical = {
            "id": "block-v1orgatest_coursetest_runtypeverticalblocktest_vertical-011f143b",
            "type": "course_block",
            "usage_key": "block-v1:orgA+test_course+test_run+type@vertical+block@test_vertical",
            "block_id": "test_vertical",
            "display_name": "vertical",
            "block_type": "vertical",
            "context_key": "course-v1:orgA+test_course+test_run",
            "org": "orgA",
            "breadcrumbs": [
                {
                    "display_name": "Test Course",
                },
                {
                    "display_name": "sequential",
                    "usage_key": "block-v1:orgA+test_course+test_run+type@sequential+block@test_sequential",
                },
            ],
            "content": {},
            "access_id": course_access.id,
            "modified": created_date.timestamp(),
        }

        meilisearch_client.return_value.index.return_value.update_documents.assert_called_with([doc_vertical])

        # Update the XBlock
        sequential = self.store.get_item(sequential.location, self.user_id)  # Refresh the XBlock
        sequential.display_name = "Updated Sequential"
        modified_date = datetime(2024, 5, 6, 7, 8, 9, tzinfo=timezone.utc)
        with freeze_time(modified_date):
            self.store.update_item(sequential, self.user_id)

        # The display name and the child's breadcrumbs should be updated
        doc_sequential["display_name"] = "Updated Sequential"
        doc_vertical["breadcrumbs"][1]["display_name"] = "Updated Sequential"
        doc_sequential["modified"] = modified_date.timestamp()

        meilisearch_client.return_value.index.return_value.update_documents.assert_called_with([
            doc_sequential,
            doc_vertical,
        ])

        # Delete the XBlock
        self.store.delete_item(vertical.location, self.user_id)

        meilisearch_client.return_value.index.return_value.delete_document.assert_called_with(
            "block-v1orgatest_coursetest_runtypeverticalblocktest_vertical-011f143b"
        )

    def test_create_delete_library_block(self, meilisearch_client):
        # Create library
        library = library_api.create_library(
            org=self.orgA,
            slug="lib_a",
            title="Library Org A",
            description="This is a library from Org A",
        )
        lib_access, _ = SearchAccess.objects.get_or_create(context_key=library.key)

        # Populate it with a problem, freezing the date so we can verify created date serializes correctly.
        created_date = datetime(2023, 4, 5, 6, 7, 8, tzinfo=timezone.utc)
        with freeze_time(created_date):
            problem = library_api.create_library_block(library.key, "problem", "Problem1")
        doc_problem = {
            "id": "lborgalib_aproblemproblem1-ca3186e9",
            "type": "library_block",
            "usage_key": "lb:orgA:lib_a:problem:Problem1",
            "block_id": "Problem1",
            "display_name": "Blank Problem",
            "block_type": "problem",
            "context_key": "lib:orgA:lib_a",
            "org": "orgA",
            "breadcrumbs": [{"display_name": "Library Org A"}],
            "content": {"problem_types": [], "capa_content": ""},
            "access_id": lib_access.id,
            "last_published": None,
            "created": created_date.timestamp(),
            "modified": created_date.timestamp(),
            "publish_status": "never",
        }

        meilisearch_client.return_value.index.return_value.update_documents.assert_called_with([doc_problem])

        # Rename the content library
        library_api.update_library(library.key, title="Updated Library Org A")

        # The breadcrumbs should be updated (but nothing else)
        doc_problem["breadcrumbs"][0]["display_name"] = "Updated Library Org A"
        meilisearch_client.return_value.index.return_value.update_documents.assert_called_with([doc_problem])

        # Edit the problem block, freezing the date so we can verify modified date serializes correctly
        modified_date = datetime(2024, 5, 6, 7, 8, 9, tzinfo=timezone.utc)
        with freeze_time(modified_date):
            library_api.set_library_block_olx(problem.usage_key, "<problem />")
        doc_problem["modified"] = modified_date.timestamp()
        meilisearch_client.return_value.index.return_value.update_documents.assert_called_with([doc_problem])

        # Publish the content library, freezing the date so we can verify last_published date serializes correctly
        published_date = datetime(2024, 6, 7, 8, 9, 10, tzinfo=timezone.utc)
        with freeze_time(published_date):
            library_api.publish_changes(library.key)
        doc_problem["last_published"] = published_date.timestamp()
        doc_problem["published"] = {"display_name": "Blank Problem"}
        doc_problem["publish_status"] = "published"
        meilisearch_client.return_value.index.return_value.update_documents.assert_called_with([doc_problem])

        # Delete the Library Block
        library_api.delete_library_block(problem.usage_key)

        meilisearch_client.return_value.index.return_value.delete_document.assert_called_with(
            "lborgalib_aproblemproblem1-ca3186e9"
        )

        # Restore the Library Block
        library_api.restore_library_block(problem.usage_key)
        meilisearch_client.return_value.index.return_value.update_documents.assert_any_call([doc_problem])
        meilisearch_client.return_value.index.return_value.update_documents.assert_any_call(
            [{'id': doc_problem['id'], 'collections': {'display_name': [], 'key': []}}]
        )
        meilisearch_client.return_value.index.return_value.update_documents.assert_any_call(
            [{'id': doc_problem['id'], 'tags': {}}]
        )
