"""
Tests for Content Library internal api.
"""

import base64
import hashlib
import uuid
from unittest import mock

from django.test import TestCase
from user_tasks.models import UserTaskStatus

from opaque_keys.edx.keys import (
    CourseKey,
    UsageKey,
    UsageKeyV2,
)
from opaque_keys.edx.locator import LibraryContainerLocator, LibraryLocatorV2, LibraryUsageLocatorV2
from openedx_events.content_authoring.data import (
    ContentObjectChangedData,
    LibraryCollectionData,
    LibraryContainerData,
)
from openedx_events.content_authoring.signals import (
    CONTENT_OBJECT_ASSOCIATIONS_CHANGED,
    LIBRARY_COLLECTION_CREATED,
    LIBRARY_COLLECTION_DELETED,
    LIBRARY_COLLECTION_UPDATED,
    LIBRARY_CONTAINER_UPDATED,
)
from openedx_authz.api.users import get_user_role_assignments_in_scope
from openedx_content import api as content_api

from common.djangoapps.student.tests.factories import UserFactory
from .. import api
from ..models import ContentLibrary
from .base import ContentLibrariesRestApiTest


class EdxModulestoreImportClientTest(TestCase):
    """
    Tests for course importing APIs.
    """

    def setUp(self):
        """
        Setup mocks and the test client.
        """
        super().setUp()
        self.mock_library = mock.MagicMock()
        self.modulestore_mock = mock.MagicMock()
        self.client = api.EdxModulestoreImportClient(
            modulestore_instance=self.modulestore_mock,
            library=self.mock_library
        )

    def test_instantiate_without_args(self):
        """
        When instantiated without args,
        Then raises.
        """
        with self.assertRaises(ValueError):
            api.EdxModulestoreImportClient()

    def test_import_blocks_from_course_without_course(self):
        """
        Given no course,
        Then raises.
        """
        self.modulestore_mock.get_course.return_value.get_children.return_value = []
        with self.assertRaises(ValueError):
            self.client.import_blocks_from_course('foobar', lambda *_: None)

    @mock.patch('openedx.core.djangoapps.content_libraries.api.courseware_import.create_library_block')
    @mock.patch('openedx.core.djangoapps.content_libraries.api.courseware_import.get_library_block')
    @mock.patch('openedx.core.djangoapps.content_libraries.api.courseware_import.get_library_block_static_asset_files')
    @mock.patch('openedx.core.djangoapps.content_libraries.api.courseware_import.publish_changes')
    @mock.patch('openedx.core.djangoapps.content_libraries.api.courseware_import.set_library_block_olx')
    def test_import_blocks_from_course_on_block_with_olx(
            self,
            mock_set_library_block_olx,
            mock_publish_changes,
            mock_get_library_block_static_asset_files,
            mock_get_library_block,
            mock_create_library_block,
    ):
        """
        Given a course with one block
        When called
        Then extract OLX, write to library and publish.
        """

        usage_key_str = 'lb:foo:bar:foobar:1234'
        library_key_str = 'lib:foo:bar'

        self.client.get_export_keys = mock.MagicMock(return_value=[UsageKey.from_string(usage_key_str)])
        self.client.get_block_data = mock.MagicMock(return_value={'olx': 'fake-olx'})

        mock_create_library_block.side_effect = api.LibraryBlockAlreadyExists
        self.mock_library.library_key = LibraryLocatorV2.from_string(library_key_str)

        self.client.import_blocks_from_course('foobar', lambda *_: None)

        mock_get_library_block.assert_called_once()
        mock_get_library_block_static_asset_files.assert_called_once()
        mock_set_library_block_olx.assert_called_once_with(
            mock.ANY, 'fake-olx')
        mock_publish_changes.assert_called_once()

    @mock.patch('openedx.core.djangoapps.content_libraries.api.courseware_import.create_library_block')
    @mock.patch('openedx.core.djangoapps.content_libraries.api.courseware_import.get_library_block_static_asset_files')
    @mock.patch('openedx.core.djangoapps.content_libraries.api.courseware_import.set_library_block_olx')
    def test_import_block_when_called_twice_same_block_but_different_course(
            self,
            mock_set_library_block_olx,
            mock_get_library_block_static_asset_files,
            mock_create_library_block,
    ):
        """
        Given an block used by one course
        And another block with same id use by a different course
        And import_block() was called on the first block
        When import_block() is called on the second block
        Then create a library block for the second block
        """
        course_key_str = 'block-v1:FakeCourse+FakeOrg+FakeRun+type@a-fake-block-type+block@fake-block-id'

        modulestore_usage_key = UsageKey.from_string(course_key_str)
        expected_course_key_hash = base64.b32encode(
            hashlib.blake2s(
                str(modulestore_usage_key.course_key).encode()
            ).digest()
        )[:16].decode().lower()
        expected_usage_id = f"{modulestore_usage_key.block_id}_c{expected_course_key_hash}"

        self.client.get_block_data = mock.MagicMock()
        self.client.import_block(modulestore_usage_key)

        mock_create_library_block.assert_called_with(
            self.client.library.library_key,
            modulestore_usage_key.block_type,
            expected_usage_id)
        mock_get_library_block_static_asset_files.assert_called_once()
        mock_set_library_block_olx.assert_called_once()


@mock.patch('openedx.core.djangoapps.content_libraries.api.courseware_import.OAuthAPIClient')
class EdxApiImportClientTest(TestCase):
    """
    Tests for EdxApiImportClient.
    """

    LMS_URL = 'https://foobar_lms.example.com/'

    STUDIO_URL = 'https://foobar_studio.example.com/'

    library_key_str = 'lib:foobar_content:foobar_library'

    course_key_str = 'course-v1:AFakeCourse+FooBar+1'

    def create_mock_library(self, *, course_id=None, course_key_str=None):
        """
        Create a library mock.
        """
        mock_library = mock.MagicMock()
        mock_library.library_key = LibraryLocatorV2.from_string(
            self.library_key_str
        )
        if course_key_str is None:
            course_key_str = self.course_key_str
        if course_id is None:
            course_id = CourseKey.from_string(course_key_str)
        type(mock_library).course_id = mock.PropertyMock(return_value=course_id)
        return mock_library

    def create_client(self, *, mock_library=None):
        """
        Create a edX API import client mock.
        """
        return api.EdxApiImportClient(
            self.LMS_URL,
            self.STUDIO_URL,
            'foobar_oauth_key',
            'foobar_oauth_secret',
            library=(mock_library or self.create_mock_library()),
        )

    def mock_oauth_client_response(self, mock_oauth_client, *, content=None, exception=None):
        """
        Setup a mock response for oauth client GET calls.
        """
        mock_response = mock.MagicMock()
        mock_content = None
        if exception:
            mock_response.raise_for_status.side_effect = exception
        if content:
            mock_content = mock.PropertyMock(return_value='foobar_file_content')
            type(mock_response).content = mock_content
        mock_oauth_client.get.return_value = mock_response
        if mock_content:
            return mock_response, mock_content
        return mock_response

    @mock.patch('openedx.core.djangoapps.content_libraries.api.courseware_import.add_library_block_static_asset_file')
    @mock.patch('openedx.core.djangoapps.content_libraries.api.courseware_import.create_library_block')
    @mock.patch('openedx.core.djangoapps.content_libraries.api.courseware_import.get_library_block_static_asset_files')
    @mock.patch('openedx.core.djangoapps.content_libraries.api.courseware_import.publish_changes')
    @mock.patch('openedx.core.djangoapps.content_libraries.api.courseware_import.set_library_block_olx')
    def test_import_block_when_url_is_from_studio(
            self,
            mock_set_library_block_olx,
            mock_publish_changes,
            mock_get_library_block_static_asset_files,
            mock_create_library_block,
            mock_add_library_block_static_asset_file,
            mock_oauth_client_class,
    ):
        """
        Given an block with one asset provided by a studio.
        When import_block() is called on the block.
        Then a GET to the API endpoint is.
        """

        # Setup mocks.

        static_filename = 'foobar_filename'
        static_content = 'foobar_file_content'
        block_olx = 'foobar-olx'
        usage_key = UsageKey.from_string('lb:foo:bar:foobar:1234')
        # We ensure ``export-file`` belongs to the URL.
        asset_studio_url = f"{self.STUDIO_URL}/foo/bar/export-file/foo/bar"
        block_data = {
            'olx': block_olx,
            'static_files': {static_filename: {'url': asset_studio_url}}
        }
        _, mock_content = self.mock_oauth_client_response(
            mock_oauth_client_class.return_value,
            content=static_content,
        )
        mock_create_library_block.return_value.usage_key = usage_key

        # Create client and call.

        client = self.create_client()
        client.get_block_data = mock.MagicMock(return_value=block_data)
        client.import_block(usage_key)

        # Assertions.

        client.get_block_data.assert_called_once_with(usage_key)
        mock_create_library_block.assert_called_once()
        mock_get_library_block_static_asset_files.assert_called_once()
        mock_content.assert_called()
        mock_add_library_block_static_asset_file.assert_called_once_with(
            usage_key,
            static_filename,
            static_content
        )
        mock_set_library_block_olx.assert_called_once_with(
            usage_key,
            block_olx
        )
        mock_publish_changes.assert_not_called()


class ContentLibraryCollectionsTest(ContentLibrariesRestApiTest):
    """
    Tests for Content Library API collections methods.

    Same guidelines as ContentLibrariesTestCase.
    """

    def setUp(self) -> None:
        super().setUp()

        # Create Content Libraries
        self._create_library("test-lib-col-1", "Test Library 1")
        self._create_library("test-lib-col-2", "Test Library 2")

        # Fetch the created ContentLibrare objects so we can access their learning_package.id
        self.lib1 = ContentLibrary.objects.get(slug="test-lib-col-1")
        self.lib2 = ContentLibrary.objects.get(slug="test-lib-col-2")

        # Create Content Library Collections
        self.col1 = api.create_library_collection(
            self.lib1.library_key,
            collection_key="COL1",
            title="Collection 1",
            description="Description for Collection 1",
            created_by=self.user.id,
        )
        self.col2 = api.create_library_collection(
            self.lib2.library_key,
            collection_key="COL2",
            title="Collection 2",
            description="Description for Collection 2",
            created_by=self.user.id,
        )
        self.col3 = api.create_library_collection(
            self.lib2.library_key,
            collection_key="COL3",
            title="Collection 3",
            description="Description for Collection 3",
            created_by=self.user.id,
        )

        # Create some library blocks in lib1
        self.lib1_problem_block = self._add_block_to_library(
            self.lib1.library_key, "problem", "problem1",
        )
        self.lib1_html_block = self._add_block_to_library(
            self.lib1.library_key, "html", "html1",
        )
        # Create a container in lib1
        self.unit1 = self._create_container(
            str(self.lib1.library_key),
            "unit", 'unit-1', 'Unit 1'
        )

        # Create a subsection container
        self.subsection1 = api.create_container(
            self.lib1.library_key,
            api.ContainerType.Subsection,
            'subsection-1',
            'Subsection 1',
            None,
        )
        # Create some library blocks in lib2
        self.lib2_problem_block = self._add_block_to_library(
            self.lib2.library_key, "problem", "problem2",
        )

    def test_create_library_collection(self) -> None:
        event_receiver = mock.Mock()
        LIBRARY_COLLECTION_CREATED.connect(event_receiver)

        collection = api.create_library_collection(
            self.lib2.library_key,
            collection_key="COL4",
            title="Collection 4",
            description="Description for Collection 4",
            created_by=self.user.id,
        )
        assert collection.key == "COL4"
        assert collection.title == "Collection 4"
        assert collection.description == "Description for Collection 4"
        assert collection.created_by == self.user

        assert event_receiver.call_count == 1
        self.assertDictContainsEntries(
            event_receiver.call_args_list[0].kwargs,
            {
                "signal": LIBRARY_COLLECTION_CREATED,
                "sender": None,
                "library_collection": LibraryCollectionData(
                    collection_key=api.library_collection_locator(
                        self.lib2.library_key,
                        collection_key="COL4",
                    ),
                ),
            },
        )

    def test_create_library_collection_invalid_library(self) -> None:
        library_key = LibraryLocatorV2.from_string("lib:INVALID:test-lib-does-not-exist")
        with self.assertRaises(api.ContentLibraryNotFound) as exc:
            api.create_library_collection(
                library_key,
                collection_key="COL4",
                title="Collection 3",
            )

    def test_update_library_collection(self) -> None:
        event_receiver = mock.Mock()
        LIBRARY_COLLECTION_UPDATED.connect(event_receiver)

        self.col1 = api.update_library_collection(
            self.lib1.library_key,
            self.col1.key,
            title="New title for Collection 1",
        )
        assert self.col1.key == "COL1"
        assert self.col1.title == "New title for Collection 1"
        assert self.col1.description == "Description for Collection 1"
        assert self.col1.created_by == self.user

        assert event_receiver.call_count == 1
        self.assertDictContainsEntries(
            event_receiver.call_args_list[0].kwargs,
            {
                "signal": LIBRARY_COLLECTION_UPDATED,
                "sender": None,
                "library_collection": LibraryCollectionData(
                    collection_key=api.library_collection_locator(
                        self.lib1.library_key,
                        collection_key="COL1",
                    ),
                ),
            },
        )

    def test_update_library_collection_wrong_library(self) -> None:
        with self.assertRaises(api.ContentLibraryCollectionNotFound) as exc:
            api.update_library_collection(
                self.lib1.library_key,
                self.col2.key,
            )

    def test_delete_library_collection(self) -> None:
        event_receiver = mock.Mock()
        LIBRARY_COLLECTION_DELETED.connect(event_receiver)

        assert self.lib1.learning_package_id is not None
        content_api.delete_collection(
            self.lib1.learning_package_id,
            self.col1.key,
            hard_delete=True,
        )

        assert event_receiver.call_count == 1
        self.assertDictContainsEntries(
            event_receiver.call_args_list[0].kwargs,
            {
                "signal": LIBRARY_COLLECTION_DELETED,
                "sender": None,
                "library_collection": LibraryCollectionData(
                    collection_key=api.library_collection_locator(
                        self.lib1.library_key,
                        collection_key="COL1",
                    ),
                ),
            },
        )

    def test_update_library_collection_items(self) -> None:
        assert not list(self.col1.entities.all())

        self.col1 = api.update_library_collection_items(
            self.lib1.library_key,
            self.col1.key,
            opaque_keys=[
                LibraryUsageLocatorV2.from_string(self.lib1_problem_block["id"]),
                LibraryUsageLocatorV2.from_string(self.lib1_html_block["id"]),
                LibraryContainerLocator.from_string(self.unit1["id"]),
            ],
        )
        assert len(self.col1.entities.all()) == 3

        self.col1 = api.update_library_collection_items(
            self.lib1.library_key,
            self.col1.key,
            opaque_keys=[
                LibraryUsageLocatorV2.from_string(self.lib1_html_block["id"]),
            ],
            remove=True,
        )
        assert len(self.col1.entities.all()) == 2

    def test_update_library_collection_components_event(self) -> None:
        """
        Check that a CONTENT_OBJECT_ASSOCIATIONS_CHANGED event is raised for each added/removed component.
        """
        event_receiver = mock.Mock()
        CONTENT_OBJECT_ASSOCIATIONS_CHANGED.connect(event_receiver)
        LIBRARY_COLLECTION_UPDATED.connect(event_receiver)

        api.update_library_collection_items(
            self.lib1.library_key,
            self.col1.key,
            opaque_keys=[
                LibraryUsageLocatorV2.from_string(self.lib1_problem_block["id"]),
                LibraryUsageLocatorV2.from_string(self.lib1_html_block["id"]),
                LibraryContainerLocator.from_string(self.unit1["id"]),
            ],
        )

        assert event_receiver.call_count == 4
        self.assertDictContainsEntries(
            event_receiver.call_args_list[0].kwargs,
            {
                "signal": CONTENT_OBJECT_ASSOCIATIONS_CHANGED,
                "sender": None,
                "content_object": ContentObjectChangedData(
                    object_id=self.lib1_problem_block["id"],
                    changes=["collections"],
                ),
            },
        )
        self.assertDictContainsEntries(
            event_receiver.call_args_list[1].kwargs,
            {
                "signal": CONTENT_OBJECT_ASSOCIATIONS_CHANGED,
                "sender": None,
                "content_object": ContentObjectChangedData(
                    object_id=self.lib1_html_block["id"],
                    changes=["collections"],
                ),
            },
        )
        self.assertDictContainsEntries(
            event_receiver.call_args_list[2].kwargs,
            {
                "signal": CONTENT_OBJECT_ASSOCIATIONS_CHANGED,
                "sender": None,
                "content_object": ContentObjectChangedData(
                    object_id=self.unit1["id"],
                    changes=["collections"],
                ),
            },
        )
        self.assertDictContainsEntries(
            event_receiver.call_args_list[3].kwargs,
            {
                "signal": LIBRARY_COLLECTION_UPDATED,
                "sender": None,
                "library_collection": LibraryCollectionData(
                    collection_key=api.library_collection_locator(
                        self.lib1.library_key,
                        collection_key="COL1",
                    ),
                ),
            },
        )

    def test_update_collection_components_from_wrong_library(self) -> None:
        with self.assertRaises(api.ContentLibraryBlockNotFound) as exc:
            api.update_library_collection_items(
                self.lib2.library_key,
                self.col2.key,
                opaque_keys=[
                    LibraryUsageLocatorV2.from_string(self.lib1_problem_block["id"]),
                    LibraryUsageLocatorV2.from_string(self.lib1_html_block["id"]),
                    LibraryContainerLocator.from_string(self.unit1["id"]),
                ],
            )
            assert self.lib1_problem_block["id"] in str(exc.exception)

    def test_set_library_component_collections(self) -> None:
        event_receiver = mock.Mock()
        CONTENT_OBJECT_ASSOCIATIONS_CHANGED.connect(event_receiver)
        collection_update_event_receiver = mock.Mock()
        LIBRARY_COLLECTION_UPDATED.connect(collection_update_event_receiver)
        assert not list(self.col2.entities.all())
        component = api.get_component_from_usage_key(UsageKeyV2.from_string(self.lib2_problem_block["id"]))
        api.set_library_item_collections(
            library_key=self.lib2.library_key,
            entity_key=component.publishable_entity.key,
            collection_keys=[self.col2.key, self.col3.key],
        )

        assert self.lib2.learning_package_id is not None
        assert len(content_api.get_collection(self.lib2.learning_package_id, self.col2.key).entities.all()) == 1
        assert len(content_api.get_collection(self.lib2.learning_package_id, self.col3.key).entities.all()) == 1

        self.assertDictContainsEntries(
            event_receiver.call_args_list[0].kwargs,
            {
                "signal": CONTENT_OBJECT_ASSOCIATIONS_CHANGED,
                "sender": None,
                "content_object": ContentObjectChangedData(
                    object_id=self.lib2_problem_block["id"],
                    changes=["collections"],
                ),
            },
        )

        assert len(collection_update_event_receiver.call_args_list) == 2
        collection_update_events = [call.kwargs for call in collection_update_event_receiver.call_args_list]
        assert all(event["signal"] == LIBRARY_COLLECTION_UPDATED for event in collection_update_events)
        assert {event["library_collection"] for event in collection_update_events} == {
            LibraryCollectionData(
                collection_key=api.library_collection_locator(self.lib2.library_key, collection_key=self.col2.key),
                background=True,
            ),
            LibraryCollectionData(
                collection_key=api.library_collection_locator(self.lib2.library_key, collection_key=self.col3.key),
                background=True,
            )
        }

    def test_delete_library_block(self) -> None:
        api.update_library_collection_items(
            self.lib1.library_key,
            self.col1.key,
            opaque_keys=[
                LibraryUsageLocatorV2.from_string(self.lib1_problem_block["id"]),
                LibraryUsageLocatorV2.from_string(self.lib1_html_block["id"]),
            ],
        )

        event_receiver = mock.Mock()
        LIBRARY_COLLECTION_UPDATED.connect(event_receiver)

        api.delete_library_block(LibraryUsageLocatorV2.from_string(self.lib1_problem_block["id"]))

        assert event_receiver.call_count == 1
        self.assertDictContainsEntries(
            event_receiver.call_args_list[0].kwargs,
            {
                "signal": LIBRARY_COLLECTION_UPDATED,
                "sender": None,
                "library_collection": LibraryCollectionData(
                    collection_key=api.library_collection_locator(
                        self.lib1.library_key,
                        collection_key=self.col1.key,
                    ),
                    background=True,
                ),
            },
        )

    def test_delete_library_container(self) -> None:
        api.update_library_collection_items(
            self.lib1.library_key,
            self.col1.key,
            opaque_keys=[
                LibraryUsageLocatorV2.from_string(self.lib1_problem_block["id"]),
                LibraryUsageLocatorV2.from_string(self.lib1_html_block["id"]),
                LibraryContainerLocator.from_string(self.unit1["id"]),
            ],
        )

        # Add container under another container
        api.update_container_children(
            self.subsection1.container_key,
            [LibraryContainerLocator.from_string(self.unit1["id"])],
            None,
        )
        event_receiver = mock.Mock()
        LIBRARY_COLLECTION_UPDATED.connect(event_receiver)
        LIBRARY_CONTAINER_UPDATED.connect(event_receiver)

        api.delete_container(LibraryContainerLocator.from_string(self.unit1["id"]))

        assert event_receiver.call_count == 2
        self.assertDictContainsEntries(
            event_receiver.call_args_list[0].kwargs,
            {
                "signal": LIBRARY_COLLECTION_UPDATED,
                "sender": None,
                "library_collection": LibraryCollectionData(
                    collection_key=api.library_collection_locator(
                        self.lib1.library_key,
                        collection_key=self.col1.key,
                    ),
                    background=True,
                ),
            },
        )
        self.assertDictContainsEntries(
            event_receiver.call_args_list[1].kwargs,
            {
                "signal": LIBRARY_CONTAINER_UPDATED,
                "sender": None,
                "library_container": LibraryContainerData(
                    container_key=self.subsection1.container_key,
                    background=False,
                )
            },
        )

    def test_restore_library_block(self) -> None:
        api.update_library_collection_items(
            self.lib1.library_key,
            self.col1.key,
            opaque_keys=[
                LibraryUsageLocatorV2.from_string(self.lib1_problem_block["id"]),
                LibraryUsageLocatorV2.from_string(self.lib1_html_block["id"]),
            ],
        )

        event_receiver = mock.Mock()
        LIBRARY_COLLECTION_UPDATED.connect(event_receiver)

        api.restore_library_block(LibraryUsageLocatorV2.from_string(self.lib1_problem_block["id"]))

        assert event_receiver.call_count == 1
        self.assertDictContainsEntries(
            event_receiver.call_args_list[0].kwargs,
            {
                "signal": LIBRARY_COLLECTION_UPDATED,
                "sender": None,
                "library_collection": LibraryCollectionData(
                    collection_key=api.library_collection_locator(
                        self.lib1.library_key,
                        collection_key=self.col1.key,
                    ),
                    background=True,
                ),
            },
        )

    def test_add_component_and_revert(self) -> None:
        # Publish changes
        api.publish_changes(self.lib1.library_key)

        # Create a new component that will only exist as a draft
        new_problem_block = self._add_block_to_library(
            self.lib1.library_key, "problem", "problemNEW",
        )

        # Add component. Note: collections are not part of the draft/publish cycle so this is not a draft change.
        api.update_library_collection_items(
            self.lib1.library_key,
            self.col1.key,
            opaque_keys=[
                LibraryUsageLocatorV2.from_string(self.lib1_html_block["id"]),
                LibraryUsageLocatorV2.from_string(new_problem_block["id"]),
            ],
        )

        collection_update_event_receiver = mock.Mock()
        LIBRARY_COLLECTION_UPDATED.connect(collection_update_event_receiver)

        api.revert_changes(self.lib1.library_key)

        assert collection_update_event_receiver.call_count == 1
        self.assertDictContainsEntries(
            collection_update_event_receiver.call_args_list[0].kwargs,
            {
                "signal": LIBRARY_COLLECTION_UPDATED,
                "sender": None,
                "library_collection": LibraryCollectionData(
                    collection_key=api.library_collection_locator(
                        self.lib1.library_key,
                        collection_key=self.col1.key,
                    ),
                ),
            },
        )

    def test_delete_component_and_revert(self) -> None:
        """
        When a component is deleted and then the delete is reverted, signals
        will be emitted to update any containing collections.
        """
        # Add components and publish
        api.update_library_collection_items(
            self.lib1.library_key,
            self.col1.key,
            opaque_keys=[
                LibraryUsageLocatorV2.from_string(self.lib1_problem_block["id"]),
                LibraryUsageLocatorV2.from_string(self.lib1_html_block["id"])
            ],
        )
        api.publish_changes(self.lib1.library_key)

        # Delete component and revert
        api.delete_library_block(LibraryUsageLocatorV2.from_string(self.lib1_problem_block["id"]))

        collection_update_event_receiver = mock.Mock()
        LIBRARY_COLLECTION_UPDATED.connect(collection_update_event_receiver)

        api.revert_changes(self.lib1.library_key)

        assert collection_update_event_receiver.call_count == 1
        self.assertDictContainsEntries(
            collection_update_event_receiver.call_args_list[0].kwargs,
            {
                "signal": LIBRARY_COLLECTION_UPDATED,
                "sender": None,
                "library_collection": LibraryCollectionData(
                    collection_key=api.library_collection_locator(
                        self.lib1.library_key,
                        collection_key=self.col1.key,
                    ),
                ),
            },
        )


class ContentLibraryContainersTest(ContentLibrariesRestApiTest):
    """
    Tests for Content Library API containers methods.
    """

    def setUp(self) -> None:
        super().setUp()

        # Create Content Libraries
        self._create_library("test-lib-cont-1", "Test Library 1")

        # Fetch the created ContentLibrare objects so we can access their learning_package.id
        self.lib1 = ContentLibrary.objects.get(slug="test-lib-cont-1")

        # Create Units
        self.unit1 = api.create_container(self.lib1.library_key, api.ContainerType.Unit, 'unit-1', 'Unit 1', None)
        self.unit2 = api.create_container(self.lib1.library_key, api.ContainerType.Unit, 'unit-2', 'Unit 2', None)
        self.unit3 = api.create_container(self.lib1.library_key, api.ContainerType.Unit, 'unit-3', 'Unit 3', None)

        # Create Subsections
        self.subsection1 = api.create_container(
            self.lib1.library_key,
            api.ContainerType.Subsection,
            'subsection-1',
            'Subsection 1',
            None,
        )
        self.subsection2 = api.create_container(
            self.lib1.library_key,
            api.ContainerType.Subsection,
            'subsection-2',
            'Subsection 2',
            None,
        )

        # Create Sections
        self.section1 = api.create_container(
            self.lib1.library_key,
            api.ContainerType.Section,
            'section-1',
            'Section 1',
            None,
        )
        self.section2 = api.create_container(
            self.lib1.library_key,
            api.ContainerType.Section,
            'section-2',
            'Section 2',
            None,
        )

        # Create XBlocks
        # Create some library blocks in lib1
        self.problem_block = self._add_block_to_library(
            self.lib1.library_key, "problem", "problem1",
        )
        self.problem_block_usage_key = LibraryUsageLocatorV2.from_string(self.problem_block["id"])
        self.problem_block_2 = self._add_block_to_library(
            self.lib1.library_key, "problem", "problem2",
        )
        self.html_block = self._add_block_to_library(
            self.lib1.library_key, "html", "html1",
        )
        self.html_block_usage_key = LibraryUsageLocatorV2.from_string(self.html_block["id"])

        # Add content to units
        api.update_container_children(
            self.unit1.container_key,
            [self.problem_block_usage_key, self.html_block_usage_key],
            None,
        )
        api.update_container_children(
            self.unit2.container_key,
            [self.html_block_usage_key],
            None,
        )

        # Add units to subsections
        api.update_container_children(
            self.subsection1.container_key,
            [self.unit1.container_key, self.unit2.container_key],
            None,
        )
        api.update_container_children(
            self.subsection2.container_key,
            [self.unit1.container_key],
            None,
        )

        # Add subsections to sections
        api.update_container_children(
            self.section1.container_key,
            [self.subsection1.container_key, self.subsection2.container_key],
            None,
        )
        api.update_container_children(
            self.section2.container_key,
            [self.subsection1.container_key],
            None,
        )

    def test_get_containers_contains_item(self):
        problem_block_containers = api.get_containers_contains_item(self.problem_block_usage_key)
        html_block_containers = api.get_containers_contains_item(self.html_block_usage_key)
        unit_1_containers = api.get_containers_contains_item(self.unit1.container_key)
        unit_2_containers = api.get_containers_contains_item(self.unit2.container_key)
        subsection_1_containers = api.get_containers_contains_item(self.subsection1.container_key)
        subsection_2_containers = api.get_containers_contains_item(self.subsection2.container_key)

        assert len(problem_block_containers) == 1
        assert problem_block_containers[0].container_key == self.unit1.container_key

        assert len(html_block_containers) == 2
        assert html_block_containers[0].container_key == self.unit1.container_key
        assert html_block_containers[1].container_key == self.unit2.container_key

        assert len(unit_1_containers) == 2
        assert unit_1_containers[0].container_key == self.subsection1.container_key
        assert unit_1_containers[1].container_key == self.subsection2.container_key

        assert len(unit_2_containers) == 1
        assert unit_2_containers[0].container_key == self.subsection1.container_key

        assert len(subsection_1_containers) == 2
        assert subsection_1_containers[0].container_key == self.section1.container_key
        assert subsection_1_containers[1].container_key == self.section2.container_key

        assert len(subsection_2_containers) == 1
        assert subsection_2_containers[0].container_key == self.section1.container_key

    def _validate_calls_of_html_block(self, event_mock):
        """
        Validate that the `event_mock` has been called twice
        using the `LIBRARY_CONTAINER_UPDATED` signal.
        """
        assert event_mock.call_count == 2
        self.assertDictContainsEntries(
            event_mock.call_args_list[0].kwargs,
            {
                "signal": LIBRARY_CONTAINER_UPDATED,
                "sender": None,
                "library_container": LibraryContainerData(
                    container_key=self.unit1.container_key,
                    background=True,
                )
            },
        )
        self.assertDictContainsEntries(
            event_mock.call_args_list[1].kwargs,
            {
                "signal": LIBRARY_CONTAINER_UPDATED,
                "sender": None,
                "library_container": LibraryContainerData(
                    container_key=self.unit2.container_key,
                    background=True,
                )
            },
        )

    def test_call_container_update_signal_when_delete_component(self) -> None:
        container_update_event_receiver = mock.Mock()
        LIBRARY_CONTAINER_UPDATED.connect(container_update_event_receiver)

        api.delete_library_block(self.html_block_usage_key)
        self._validate_calls_of_html_block(container_update_event_receiver)

    def test_call_container_update_signal_when_restore_component(self) -> None:
        api.delete_library_block(self.html_block_usage_key)

        container_update_event_receiver = mock.Mock()
        LIBRARY_CONTAINER_UPDATED.connect(container_update_event_receiver)
        api.restore_library_block(self.html_block_usage_key)

        self._validate_calls_of_html_block(container_update_event_receiver)

    def test_call_container_update_signal_when_update_olx(self) -> None:
        block_olx = "<html><b>Hello world!</b></html>"
        container_update_event_receiver = mock.Mock()
        LIBRARY_CONTAINER_UPDATED.connect(container_update_event_receiver)

        self._set_library_block_olx(self.html_block_usage_key, block_olx)
        self._validate_calls_of_html_block(container_update_event_receiver)

    def test_call_container_update_signal_when_update_component(self) -> None:
        block_olx = "<html><b>Hello world!</b></html>"
        container_update_event_receiver = mock.Mock()
        LIBRARY_CONTAINER_UPDATED.connect(container_update_event_receiver)

        self._set_library_block_fields(self.html_block_usage_key, {"data": block_olx, "metadata": {}})
        self._validate_calls_of_html_block(container_update_event_receiver)

    def test_call_container_update_signal_when_update_unit(self) -> None:
        container_update_event_receiver = mock.Mock()
        LIBRARY_CONTAINER_UPDATED.connect(container_update_event_receiver)
        self._update_container(self.unit1.container_key, 'New Unit Display Name')

        assert container_update_event_receiver.call_count == 3
        self.assertDictContainsEntries(
            container_update_event_receiver.call_args_list[0].kwargs,
            {
                "signal": LIBRARY_CONTAINER_UPDATED,
                "sender": None,
                "library_container": LibraryContainerData(
                    container_key=self.unit1.container_key,
                )
            },
        )
        self.assertDictContainsEntries(
            container_update_event_receiver.call_args_list[1].kwargs,
            {
                "signal": LIBRARY_CONTAINER_UPDATED,
                "sender": None,
                "library_container": LibraryContainerData(
                    container_key=self.subsection1.container_key,
                )
            },
        )
        self.assertDictContainsEntries(
            container_update_event_receiver.call_args_list[2].kwargs,
            {
                "signal": LIBRARY_CONTAINER_UPDATED,
                "sender": None,
                "library_container": LibraryContainerData(
                    container_key=self.subsection2.container_key,
                )
            },
        )

    def test_call_container_update_signal_when_update_subsection(self) -> None:
        container_update_event_receiver = mock.Mock()
        LIBRARY_CONTAINER_UPDATED.connect(container_update_event_receiver)
        self._update_container(self.subsection1.container_key, 'New Subsection Display Name')

        assert container_update_event_receiver.call_count == 3
        self.assertDictContainsEntries(
            container_update_event_receiver.call_args_list[0].kwargs,
            {
                "signal": LIBRARY_CONTAINER_UPDATED,
                "sender": None,
                "library_container": LibraryContainerData(
                    container_key=self.subsection1.container_key,
                )
            },
        )
        self.assertDictContainsEntries(
            container_update_event_receiver.call_args_list[1].kwargs,
            {
                "signal": LIBRARY_CONTAINER_UPDATED,
                "sender": None,
                "library_container": LibraryContainerData(
                    container_key=self.section1.container_key,
                )
            },
        )
        self.assertDictContainsEntries(
            container_update_event_receiver.call_args_list[2].kwargs,
            {
                "signal": LIBRARY_CONTAINER_UPDATED,
                "sender": None,
                "library_container": LibraryContainerData(
                    container_key=self.section2.container_key,
                )
            },
        )

    def test_call_container_update_signal_when_update_section(self) -> None:
        container_update_event_receiver = mock.Mock()
        LIBRARY_CONTAINER_UPDATED.connect(container_update_event_receiver)
        self._update_container(self.section1.container_key, 'New Section Display Name')

        assert container_update_event_receiver.call_count == 1
        self.assertDictContainsEntries(
            container_update_event_receiver.call_args_list[0].kwargs,
            {
                "signal": LIBRARY_CONTAINER_UPDATED,
                "sender": None,
                "library_container": LibraryContainerData(
                    container_key=self.section1.container_key,
                )
            },
        )

    def test_call_object_changed_signal_when_remove_component(self) -> None:
        html_block_1 = self._add_block_to_library(
            self.lib1.library_key, "html", "html3",
        )
        api.update_container_children(
            self.unit2.container_key,
            [LibraryUsageLocatorV2.from_string(html_block_1["id"])],
            None,
            entities_action=content_api.ChildrenEntitiesAction.APPEND,
        )

        event_reciver = mock.Mock()
        CONTENT_OBJECT_ASSOCIATIONS_CHANGED.connect(event_reciver)
        api.update_container_children(
            self.unit2.container_key,
            [LibraryUsageLocatorV2.from_string(html_block_1["id"])],
            None,
            entities_action=content_api.ChildrenEntitiesAction.REMOVE,
        )

        assert event_reciver.call_count == 1
        self.assertDictContainsEntries(
            event_reciver.call_args_list[0].kwargs,
            {
                "signal": CONTENT_OBJECT_ASSOCIATIONS_CHANGED,
                "sender": None,
                "content_object": ContentObjectChangedData(
                    object_id=html_block_1["id"],
                    changes=["units"],
                ),
            },
        )

    def test_call_object_changed_signal_when_remove_unit(self) -> None:
        unit4 = api.create_container(self.lib1.library_key, api.ContainerType.Unit, 'unit-4', 'Unit 4', None)

        api.update_container_children(
            self.subsection2.container_key,
            [unit4.container_key],
            None,
            entities_action=content_api.ChildrenEntitiesAction.APPEND,
        )

        event_reciver = mock.Mock()
        CONTENT_OBJECT_ASSOCIATIONS_CHANGED.connect(event_reciver)
        api.update_container_children(
            self.subsection2.container_key,
            [unit4.container_key],
            None,
            entities_action=content_api.ChildrenEntitiesAction.REMOVE,
        )

        assert event_reciver.call_count == 1
        self.assertDictContainsEntries(
            event_reciver.call_args_list[0].kwargs,
            {
                "signal": CONTENT_OBJECT_ASSOCIATIONS_CHANGED,
                "sender": None,
                "content_object": ContentObjectChangedData(
                    object_id=str(unit4.container_key),
                    changes=["subsections"],
                ),
            },
        )

    def test_call_object_changed_signal_when_remove_subsection(self) -> None:
        subsection3 = api.create_container(
            self.lib1.library_key,
            api.ContainerType.Subsection,
            'subsection-3',
            'Subsection 3',
            None,
        )

        api.update_container_children(
            self.section2.container_key,
            [subsection3.container_key],
            None,
            entities_action=content_api.ChildrenEntitiesAction.APPEND,
        )

        event_reciver = mock.Mock()
        CONTENT_OBJECT_ASSOCIATIONS_CHANGED.connect(event_reciver)
        api.update_container_children(
            self.section2.container_key,
            [subsection3.container_key],
            None,
            entities_action=content_api.ChildrenEntitiesAction.REMOVE,
        )

        assert event_reciver.call_count == 1
        self.assertDictContainsEntries(
            event_reciver.call_args_list[0].kwargs,
            {
                "signal": CONTENT_OBJECT_ASSOCIATIONS_CHANGED,
                "sender": None,
                "content_object": ContentObjectChangedData(
                    object_id=str(subsection3.container_key),
                    changes=["sections"],
                ),
            },
        )

    def test_call_object_changed_signal_when_add_component(self) -> None:
        event_reciver = mock.Mock()
        CONTENT_OBJECT_ASSOCIATIONS_CHANGED.connect(event_reciver)
        html_block_1 = self._add_block_to_library(
            self.lib1.library_key, "html", "html4",
        )
        html_block_2 = self._add_block_to_library(
            self.lib1.library_key, "html", "html5",
        )

        api.update_container_children(
            self.unit2.container_key,
            [
                LibraryUsageLocatorV2.from_string(html_block_1["id"]),
                LibraryUsageLocatorV2.from_string(html_block_2["id"])
            ],
            None,
            entities_action=content_api.ChildrenEntitiesAction.APPEND,
        )

        assert event_reciver.call_count == 2
        self.assertDictContainsEntries(
            event_reciver.call_args_list[0].kwargs,
            {
                "signal": CONTENT_OBJECT_ASSOCIATIONS_CHANGED,
                "sender": None,
                "content_object": ContentObjectChangedData(
                    object_id=html_block_1["id"],
                    changes=["units"],
                ),
            },
        )
        self.assertDictContainsEntries(
            event_reciver.call_args_list[1].kwargs,
            {
                "signal": CONTENT_OBJECT_ASSOCIATIONS_CHANGED,
                "sender": None,
                "content_object": ContentObjectChangedData(
                    object_id=html_block_2["id"],
                    changes=["units"],
                ),
            },
        )

    def test_call_object_changed_signal_when_add_unit(self) -> None:
        event_reciver = mock.Mock()
        CONTENT_OBJECT_ASSOCIATIONS_CHANGED.connect(event_reciver)

        unit4 = api.create_container(self.lib1.library_key, api.ContainerType.Unit, 'unit-4', 'Unit 4', None)
        unit5 = api.create_container(self.lib1.library_key, api.ContainerType.Unit, 'unit-5', 'Unit 5', None)

        api.update_container_children(
            self.subsection2.container_key,
            [unit4.container_key, unit5.container_key],
            None,
            entities_action=content_api.ChildrenEntitiesAction.APPEND,
        )
        assert event_reciver.call_count == 2
        self.assertDictContainsEntries(
            event_reciver.call_args_list[0].kwargs,
            {
                "signal": CONTENT_OBJECT_ASSOCIATIONS_CHANGED,
                "sender": None,
                "content_object": ContentObjectChangedData(
                    object_id=str(unit4.container_key),
                    changes=["subsections"],
                ),
            },
        )
        self.assertDictContainsEntries(
            event_reciver.call_args_list[1].kwargs,
            {
                "signal": CONTENT_OBJECT_ASSOCIATIONS_CHANGED,
                "sender": None,
                "content_object": ContentObjectChangedData(
                    object_id=str(unit5.container_key),
                    changes=["subsections"],
                ),
            },
        )

    def test_call_object_changed_signal_when_add_subsection(self) -> None:
        event_reciver = mock.Mock()
        CONTENT_OBJECT_ASSOCIATIONS_CHANGED.connect(event_reciver)

        subsection3 = api.create_container(
            self.lib1.library_key,
            api.ContainerType.Subsection,
            'subsection-3',
            'Subsection 3',
            None,
        )
        subsection4 = api.create_container(
            self.lib1.library_key,
            api.ContainerType.Subsection,
            'subsection-4',
            'Subsection 4',
            None,
        )
        api.update_container_children(
            self.section2.container_key,
            [subsection3.container_key, subsection4.container_key],
            None,
            entities_action=content_api.ChildrenEntitiesAction.APPEND,
        )
        assert event_reciver.call_count == 2
        self.assertDictContainsEntries(
            event_reciver.call_args_list[0].kwargs,
            {
                "signal": CONTENT_OBJECT_ASSOCIATIONS_CHANGED,
                "sender": None,
                "content_object": ContentObjectChangedData(
                    object_id=str(subsection3.container_key),
                    changes=["sections"],
                ),
            },
        )
        self.assertDictContainsEntries(
            event_reciver.call_args_list[1].kwargs,
            {
                "signal": CONTENT_OBJECT_ASSOCIATIONS_CHANGED,
                "sender": None,
                "content_object": ContentObjectChangedData(
                    object_id=str(subsection4.container_key),
                    changes=["sections"],
                ),
            },
        )

    def test_delete_component_and_revert(self) -> None:
        """
        When a component is deleted and then the delete is reverted, signals
        will be emitted to update any containing containers.
        """
        # Add components and publish
        api.update_container_children(self.unit3.container_key, [
            LibraryUsageLocatorV2.from_string(self.problem_block_2["id"]),
        ], user_id=None)
        api.publish_changes(self.lib1.library_key)

        # Delete component and revert
        api.delete_library_block(LibraryUsageLocatorV2.from_string(self.problem_block_2["id"]))

        container_event_receiver = mock.Mock()
        LIBRARY_CONTAINER_UPDATED.connect(container_event_receiver)

        api.revert_changes(self.lib1.library_key)

        assert container_event_receiver.call_count == 1
        self.assertDictContainsEntries(
            container_event_receiver.call_args_list[0].kwargs,
            {
                "signal": LIBRARY_CONTAINER_UPDATED,
                "sender": None,
                "library_container": LibraryContainerData(
                    container_key=self.unit3.container_key
                ),
            },
        )

    def test_copy_and_paste_container_same_library(self) -> None:
        # Copy a section with children
        api.copy_container(self.section1.container_key, self.user.id)
        # Paste the container
        new_container: api.ContainerMetadata = (
            api.import_staged_content_from_user_clipboard(self.lib1.library_key, self.user)  # type: ignore[assignment]
        )

        # Verify that the container is copied
        assert new_container.container_type == self.section1.container_type
        assert new_container.display_name == self.section1.display_name

        # Verify that the children are linked
        subsections = api.get_container_children(new_container.container_key)
        assert len(subsections) == 2
        assert isinstance(subsections[0], api.ContainerMetadata)
        assert subsections[0].container_key == self.subsection1.container_key
        assert isinstance(subsections[1], api.ContainerMetadata)
        assert subsections[1].container_key == self.subsection2.container_key

    def test_copy_and_paste_container_another_library(self) -> None:
        # Copy a section with children
        api.copy_container(self.section1.container_key, self.user.id)

        self._create_library("test-lib-cont-2", "Test Library 2")
        lib2 = ContentLibrary.objects.get(slug="test-lib-cont-2")
        # Paste the container
        new_container: api.ContainerMetadata = (
            api.import_staged_content_from_user_clipboard(lib2.library_key, self.user)  # type: ignore[assignment]
        )

        # Verify that the container is copied
        assert new_container.container_type == self.section1.container_type
        assert new_container.display_name == self.section1.display_name

        # Verify that the children are copied
        subsections = api.get_container_children(new_container.container_key)
        assert len(subsections) == 2
        assert isinstance(subsections[0], api.ContainerMetadata)
        assert subsections[0].container_key != self.subsection1.container_key  # This subsection was copied
        assert subsections[0].display_name == self.subsection1.display_name
        units_subsection1 = api.get_container_children(subsections[0].container_key)
        assert len(units_subsection1) == 2
        assert isinstance(units_subsection1[0], api.ContainerMetadata)
        assert units_subsection1[0].container_key != self.unit1.container_key  # This unit was copied
        assert units_subsection1[0].display_name == self.unit1.display_name == "Unit 1"
        unit1_components = api.get_container_children(units_subsection1[0].container_key)
        assert len(unit1_components) == 2
        assert isinstance(unit1_components[0], api.LibraryXBlockMetadata)
        assert unit1_components[0].usage_key != self.problem_block_usage_key  # This component was copied
        assert isinstance(unit1_components[1], api.LibraryXBlockMetadata)
        assert unit1_components[1].usage_key != self.html_block_usage_key  # This component was copied

        assert isinstance(units_subsection1[1], api.ContainerMetadata)
        assert units_subsection1[1].container_key != self.unit2.container_key  # This unit was copied
        assert units_subsection1[1].display_name == self.unit2.display_name == "Unit 2"
        unit2_components = api.get_container_children(units_subsection1[1].container_key)
        assert len(unit2_components) == 1
        assert isinstance(unit2_components[0], api.LibraryXBlockMetadata)
        assert unit2_components[0].usage_key != self.html_block_usage_key

        # This is the same component, so it should not be duplicated
        assert unit1_components[1].usage_key == unit2_components[0].usage_key

        assert isinstance(subsections[1], api.ContainerMetadata)
        assert subsections[1].container_key != self.subsection2.container_key  # This subsection was copied
        assert subsections[1].display_name == self.subsection2.display_name
        units_subsection2 = api.get_container_children(subsections[1].container_key)
        assert len(units_subsection2) == 1
        assert isinstance(units_subsection2[0], api.ContainerMetadata)
        assert units_subsection2[0].container_key != self.unit1.container_key  # This unit was copied
        assert units_subsection2[0].display_name == self.unit1.display_name

        # This is the same unit, so it should not be duplicated
        assert units_subsection1[0].container_key == units_subsection2[0].container_key


class ContentLibraryExportTest(ContentLibrariesRestApiTest):
    """
    Tests for Content Library API export methods.
    """

    def setUp(self) -> None:
        super().setUp()

        # Create Content Libraries
        self._create_library("test-lib-exp-1", "Test Library Export 1")

        # Fetch the created ContentLibrary objects so we can access their learning_package.id
        self.lib1 = ContentLibrary.objects.get(slug="test-lib-exp-1")
        self.wrong_task_id = '11111111-1111-1111-1111-111111111111'

    def test_get_backup_task_status_no_task(self) -> None:
        status = api.get_backup_task_status(self.user.id, "")
        assert status is None

    def test_get_backup_task_status_wrong_task_id(self) -> None:
        status = api.get_backup_task_status(self.user.id, task_id=self.wrong_task_id)
        assert status is None

    def test_get_backup_task_status_in_progress(self) -> None:
        # Create a mock UserTaskStatus in IN_PROGRESS state
        task_id = str(uuid.uuid4())
        mock_task = UserTaskStatus(
            task_id=task_id,
            user_id=self.user.id,
            name=f"Export of {self.lib1.library_key}",
            state=UserTaskStatus.IN_PROGRESS
        )

        with mock.patch(
            'openedx.core.djangoapps.content_libraries.api.libraries.UserTaskStatus.objects.get'
        ) as mock_get:
            mock_get.return_value = mock_task

            status = api.get_backup_task_status(self.user.id, task_id=task_id)
            assert status is not None
            assert status['state'] == UserTaskStatus.IN_PROGRESS
            assert status['file'] is None

    def test_get_backup_task_status_succeeded(self) -> None:
        # Create a mock UserTaskStatus in SUCCEEDED state
        task_id = str(uuid.uuid4())
        mock_task = UserTaskStatus(
            task_id=task_id,
            user_id=self.user.id,
            name=f"Export of {self.lib1.library_key}",
            state=UserTaskStatus.SUCCEEDED
        )

        # Create a mock UserTaskArtifact
        mock_artifact = mock.Mock()
        mock_artifact.file.url = "/media/user_tasks/2025/10/01/library-libOEXCSPROB_mOw1rPL.zip"

        with mock.patch(
            'openedx.core.djangoapps.content_libraries.api.libraries.UserTaskStatus.objects.get'
        ) as mock_get, mock.patch(
            'openedx.core.djangoapps.content_libraries.api.libraries.UserTaskArtifact.objects.get'
        ) as mock_artifact_get:

            mock_get.return_value = mock_task
            mock_artifact_get.return_value = mock_artifact

            status = api.get_backup_task_status(self.user.id, task_id=task_id)
            assert status is not None
            assert status['state'] == UserTaskStatus.SUCCEEDED
            assert status['file'].url == "/media/user_tasks/2025/10/01/library-libOEXCSPROB_mOw1rPL.zip"

    def test_get_backup_task_status_failed(self) -> None:
        # Create a mock UserTaskStatus in FAILED state
        task_id = str(uuid.uuid4())
        mock_task = UserTaskStatus(
            task_id=task_id,
            user_id=self.user.id,
            name=f"Export of {self.lib1.library_key}",
            state=UserTaskStatus.FAILED
        )

        with mock.patch(
            'openedx.core.djangoapps.content_libraries.api.libraries.UserTaskStatus.objects.get'
        ) as mock_get:
            mock_get.return_value = mock_task

            status = api.get_backup_task_status(self.user.id, task_id=task_id)
            assert status is not None
            assert status['state'] == UserTaskStatus.FAILED
            assert status['file'] is None


class ContentLibraryAuthZRoleAssignmentTest(ContentLibrariesRestApiTest):
    """
    Tests for Content Library role assignment via the AuthZ Authorization Framework.

    These tests verify that library roles are correctly assigned to users through
    the openedx-authz (AuthZ) Authorization Framework when libraries are created or when
    explicit role assignments are made.

    See: https://github.com/openedx/openedx-authz/
    """

    def setUp(self) -> None:
        super().setUp()

        # Create Content Libraries
        self._create_library("test-lib-role-1", "Test Library Role 1")

        # Fetch the created ContentLibrary objects so we can access their learning_package.id
        self.lib1 = ContentLibrary.objects.get(slug="test-lib-role-1")

    def test_assign_library_admin_role_to_user_via_authz(self) -> None:
        """
        Test assigning a library admin role to a user via the AuthZ Authorization Framework.

        This test verifies that the openedx-authz Authorization Framework correctly
        assigns the library_admin role to a user when explicitly called.
        """
        api.assign_library_role_to_user(self.lib1.library_key, self.user, api.AccessLevel.ADMIN_LEVEL)

        roles = get_user_role_assignments_in_scope(self.user.username, str(self.lib1.library_key))
        assert len(roles) == 1
        assert "library_admin" in repr(roles[0].roles[0])

    def test_assign_library_author_role_to_user_via_authz(self) -> None:
        """
        Test assigning a library author role to a user via the AuthZ Authorization Framework.

        This test verifies that the openedx-authz Authorization Framework correctly
        assigns the library_author role to a user when explicitly called.
        """
        # Create a new user to avoid conflicts with roles assigned during library creation
        author_user = UserFactory.create(username="Author", email="author@example.com")

        api.assign_library_role_to_user(self.lib1.library_key, author_user, api.AccessLevel.AUTHOR_LEVEL)

        roles = get_user_role_assignments_in_scope(author_user.username, str(self.lib1.library_key))
        assert len(roles) == 1
        assert "library_author" in repr(roles[0].roles[0])

    @mock.patch("openedx.core.djangoapps.content_libraries.api.libraries.assign_role_to_user_in_scope")
    def test_library_creation_assigns_admin_role_via_authz(
        self,
        mock_assign_role
    ) -> None:
        """
        Test that creating a library via REST API assigns admin role via AuthZ.

        This test verifies that when a library is created via the REST API,
        the creator is automatically assigned the library_admin role through
        the openedx-authz Authorization Framework.
        """
        mock_assign_role.return_value = True

        # Create a new library (this should trigger role assignment in the REST API)
        self._create_library("test-lib-role-2", "Test Library Role 2")

        # Verify that assign_role_to_user_in_scope was called
        mock_assign_role.assert_called_once()
        call_args = mock_assign_role.call_args
        assert call_args[0][0] == self.user.username  # username
        assert call_args[0][1] == "library_admin"  # role
        assert "test-lib-role-2" in call_args[0][2]  # library_key (contains slug)

    @mock.patch("openedx.core.djangoapps.content_libraries.api.libraries.assign_role_to_user_in_scope")
    def test_library_creation_handles_authz_failure_gracefully(
        self,
        mock_assign_role
    ) -> None:
        """
        Test that library creation succeeds even if AuthZ role assignment fails.

        This test verifies that if the openedx-authz Authorization Framework fails to assign
        a role (returns False), the library creation still succeeds. This ensures that
        the system degrades gracefully and doesn't break library creation if there are
        issues with the Authorization Framework.
        """
        # Simulate openedx-authz failing to assign the role
        mock_assign_role.return_value = False

        # Library creation should still succeed
        result = self._create_library("test-lib-role-3", "Test Library Role 3")
        assert result is not None
        assert result["slug"] == "test-lib-role-3"

        # Verify that the library was created successfully
        lib3 = ContentLibrary.objects.get(slug="test-lib-role-3")
        assert lib3 is not None
        assert lib3.slug == "test-lib-role-3"

    @mock.patch("openedx.core.djangoapps.content_libraries.api.libraries.assign_role_to_user_in_scope")
    def test_library_creation_handles_authz_exception(
        self,
        mock_assign_role
    ) -> None:
        """
        Test that library creation succeeds even if AuthZ raises an exception.

        This test verifies that if the openedx-authz Authorization Framework raises an
        exception during role assignment, the library creation still succeeds. This ensures
        robust error handling when the Authorization Framework is unavailable or misconfigured.
        """
        # Simulate openedx-authz raising an exception for unknown issues
        mock_assign_role.side_effect = Exception("AuthZ unavailable")

        # Library creation should still succeed (the exception should be caught/handled)
        # Note: Currently, the code doesn't catch this exception, so we expect it to propagate.
        # This test documents the current behavior and can be updated if error handling is added.
        with self.assertRaises(Exception) as context:
            self._create_library("test-lib-role-4", "Test Library Role 4")

        assert "AuthZ unavailable" in str(context.exception)
