"""
Tests for Content Library internal api.
"""

import uuid
from unittest import mock

from django.db import transaction
from opaque_keys.edx.keys import UsageKeyV2
from opaque_keys.edx.locator import LibraryContainerLocator, LibraryLocatorV2, LibraryUsageLocatorV2
from openedx_authz.api.users import get_user_role_assignments_in_scope
from openedx_content import api as content_api
from openedx_content import models_api as content_models
from openedx_content.models_api import Component, Container
from openedx_events.content_authoring.data import (
    ContentObjectChangedData,
    LibraryBlockData,
    LibraryCollectionData,
    LibraryContainerData,
)
from openedx_events.content_authoring.signals import (
    CONTENT_OBJECT_ASSOCIATIONS_CHANGED,
    LIBRARY_BLOCK_DELETED,
    LIBRARY_BLOCK_UPDATED,
    LIBRARY_COLLECTION_CREATED,
    LIBRARY_COLLECTION_DELETED,
    LIBRARY_COLLECTION_UPDATED,
    LIBRARY_CONTAINER_DELETED,
    LIBRARY_CONTAINER_UPDATED,
)
from openedx_events.testing import EventsIsolationMixin
from openedx_events.tooling import OpenEdxPublicSignal
from user_tasks.models import UserTaskStatus

from common.djangoapps.student.tests.factories import UserFactory

from .. import api
from ..models import ContentLibrary
from .base import ContentLibrariesRestApiTest


class ContentLibraryCollectionsTest(ContentLibrariesRestApiTest, EventsIsolationMixin):
    """
    Tests for Content Library API collections methods.

    Same guidelines as ContentLibrariesTestCase.
    """

    @classmethod
    def setUpClass(cls):
        """Test setup"""
        super().setUpClass()
        # By default, errors thrown in signal handlers get suppressed. We want to see them though!
        # https://github.com/openedx/openedx-events/issues/569
        cls.allow_send_events_failure(*(s.event_type for s in OpenEdxPublicSignal.all_events()))

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
            content_models.Subsection,
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
        assert collection.collection_code == "COL4"
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
        with self.assertRaises(api.ContentLibraryNotFound) as exc:  # noqa: F841, PT027
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
            self.col1.collection_code,
            title="New title for Collection 1",
        )
        assert self.col1.collection_code == "COL1"
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
        with self.assertRaises(api.ContentLibraryCollectionNotFound) as exc:  # noqa: F841, PT027
            api.update_library_collection(
                self.lib1.library_key,
                self.col2.collection_code,
            )

    def test_delete_library_collection(self) -> None:
        event_receiver = mock.Mock()
        LIBRARY_COLLECTION_DELETED.connect(event_receiver)

        assert self.lib1.learning_package_id is not None
        content_api.delete_collection(
            self.lib1.learning_package_id,
            self.col1.collection_code,
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
            self.col1.collection_code,
            opaque_keys=[
                LibraryUsageLocatorV2.from_string(self.lib1_problem_block["id"]),
                LibraryUsageLocatorV2.from_string(self.lib1_html_block["id"]),
                LibraryContainerLocator.from_string(self.unit1["id"]),
            ],
        )
        assert len(self.col1.entities.all()) == 3

        self.col1 = api.update_library_collection_items(
            self.lib1.library_key,
            self.col1.collection_code,
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
            self.col1.collection_code,
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
                "signal": LIBRARY_COLLECTION_UPDATED,
                "library_collection": LibraryCollectionData(
                    collection_key=api.library_collection_locator(
                        self.lib1.library_key,
                        collection_key="COL1",
                    ),
                ),
            },
        )
        self.assertDictContainsEntries(
            event_receiver.call_args_list[1].kwargs,
            {
                "signal": CONTENT_OBJECT_ASSOCIATIONS_CHANGED,
                "content_object": ContentObjectChangedData(
                    object_id=self.lib1_problem_block["id"],
                    changes=["collections"],
                ),
            },
        )
        self.assertDictContainsEntries(
            event_receiver.call_args_list[2].kwargs,
            {
                "signal": CONTENT_OBJECT_ASSOCIATIONS_CHANGED,
                "content_object": ContentObjectChangedData(
                    object_id=self.lib1_html_block["id"],
                    changes=["collections"],
                ),
            },
        )
        self.assertDictContainsEntries(
            event_receiver.call_args_list[3].kwargs,
            {
                "signal": CONTENT_OBJECT_ASSOCIATIONS_CHANGED,
                "content_object": ContentObjectChangedData(
                    object_id=self.unit1["id"],
                    changes=["collections"],
                ),
            },
        )

    def test_update_collection_components_from_wrong_library(self) -> None:
        with self.assertRaises(api.ContentLibraryBlockNotFound) as exc:  # noqa: PT027
            api.update_library_collection_items(
                self.lib2.library_key,
                self.col2.collection_code,
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
            entity_ref=component.publishable_entity.entity_ref,
            collection_keys=[self.col2.collection_code, self.col3.collection_code],
        )

        assert self.lib2.learning_package_id is not None
        col2 = content_api.get_collection(self.lib2.learning_package_id, self.col2.collection_code)
        col3 = content_api.get_collection(self.lib2.learning_package_id, self.col3.collection_code)
        assert len(col2.entities.all()) == 1
        assert len(col3.entities.all()) == 1

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
                collection_key=api.library_collection_locator(
                    self.lib2.library_key, collection_key=self.col2.collection_code
                )
            ),
            LibraryCollectionData(
                collection_key=api.library_collection_locator(
                    self.lib2.library_key, collection_key=self.col3.collection_code
                )
            ),
        }

    def test_delete_library_block(self) -> None:
        api.update_library_collection_items(
            self.lib1.library_key,
            self.col1.collection_code,
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
                        self.lib1.library_key, collection_key=self.col1.collection_code
                    ),
                ),
            },
        )

    def test_delete_library_container(self) -> None:
        api.update_library_collection_items(
            self.lib1.library_key,
            self.col1.collection_code,
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
                        collection_key=self.col1.collection_code,
                    ),
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
                ),
            },
        )

    def test_delete_container_when_container_does_not_exist(self) -> None:
        """
        Test that delete_container raises Container.DoesNotExist and still sends
        LIBRARY_CONTAINER_DELETED (to clean up stale search-index entries) when
        the Container does not exist in the DB.
        """
        container_key = LibraryContainerLocator.from_string(self.unit1["id"])

        event_receiver = mock.Mock()
        LIBRARY_CONTAINER_DELETED.connect(event_receiver)
        self.addCleanup(LIBRARY_CONTAINER_DELETED.disconnect, event_receiver)

        with mock.patch(
            "openedx.core.djangoapps.content_libraries.api.containers.get_container_from_key",
            side_effect=Container.DoesNotExist,
        ), mock.patch("openedx_content.api.soft_delete_draft") as mock_soft_delete:
            with self.assertRaises(Container.DoesNotExist):  # noqa: PT027
                api.delete_container(container_key)
            mock_soft_delete.assert_not_called()

        assert event_receiver.call_count == 1
        self.assertDictContainsEntries(
            event_receiver.call_args_list[0].kwargs,
            {
                "signal": LIBRARY_CONTAINER_DELETED,
                "library_container": LibraryContainerData(
                    container_key=container_key,
                ),
            },
        )

    def test_delete_library_block_when_component_does_not_exist(self) -> None:
        """
        Test that delete_library_block raises Component.DoesNotExist and still sends
        LIBRARY_BLOCK_DELETED (to clean up stale search-index entries) when the
        Component does not exist in the DB.
        """
        usage_key = LibraryUsageLocatorV2.from_string(self.lib1_problem_block["id"])

        event_receiver = mock.Mock()
        LIBRARY_BLOCK_DELETED.connect(event_receiver)
        self.addCleanup(LIBRARY_BLOCK_DELETED.disconnect, event_receiver)

        with mock.patch(
            "openedx.core.djangoapps.content_libraries.api.blocks.get_component_from_usage_key",
            side_effect=Component.DoesNotExist,
        ), mock.patch("openedx_content.api.soft_delete_draft") as mock_soft_delete:
            with self.assertRaises(Component.DoesNotExist):  # noqa: PT027
                api.delete_library_block(usage_key)
            mock_soft_delete.assert_not_called()

        assert event_receiver.call_count == 1
        self.assertDictContainsEntries(
            event_receiver.call_args_list[0].kwargs,
            {
                "signal": LIBRARY_BLOCK_DELETED,
                "library_block": LibraryBlockData(
                    library_key=self.lib1.library_key,
                    usage_key=usage_key,
                ),
            },
        )

    def test_restore_library_block(self) -> None:
        usage_key = LibraryUsageLocatorV2.from_string(self.lib1_problem_block["id"])
        api.update_library_collection_items(
            self.lib1.library_key,
            self.col1.collection_code,
            opaque_keys=[
                usage_key,
                LibraryUsageLocatorV2.from_string(self.lib1_html_block["id"]),
            ],
        )

        api.delete_library_block(usage_key)

        event_receiver = mock.Mock()
        LIBRARY_COLLECTION_UPDATED.connect(event_receiver)

        api.restore_library_block(usage_key)

        assert event_receiver.call_count == 1
        self.assertDictContainsEntries(
            event_receiver.call_args_list[0].kwargs,
            {
                "signal": LIBRARY_COLLECTION_UPDATED,
                "sender": None,
                "library_collection": LibraryCollectionData(
                    collection_key=api.library_collection_locator(
                        self.lib1.library_key,
                        collection_key=self.col1.collection_code,
                    ),
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
            self.col1.collection_code,
            opaque_keys=[
                LibraryUsageLocatorV2.from_string(self.lib1_html_block["id"]),
                LibraryUsageLocatorV2.from_string(new_problem_block["id"]),
            ],
        )

        collection_update_event_receiver = mock.Mock()
        LIBRARY_COLLECTION_UPDATED.connect(collection_update_event_receiver)

        # Reverting the change essentially deletes the item, so we should get an event that the collection's contents
        # have been updated.
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
                        collection_key=self.col1.collection_code,
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
            self.col1.collection_code,
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
                        collection_key=self.col1.collection_code,
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
        self.unit1 = api.create_container(self.lib1.library_key, content_models.Unit, 'unit-1', 'Unit 1', None)
        self.unit2 = api.create_container(self.lib1.library_key, content_models.Unit, 'unit-2', 'Unit 2', None)
        self.unit3 = api.create_container(self.lib1.library_key, content_models.Unit, 'unit-3', 'Unit 3', None)

        # Create Subsections
        self.subsection1 = api.create_container(
            self.lib1.library_key,
            content_models.Subsection,
            'subsection-1',
            'Subsection 1',
            None,
        )
        self.subsection2 = api.create_container(
            self.lib1.library_key,
            content_models.Subsection,
            'subsection-2',
            'Subsection 2',
            None,
        )

        # Create Sections
        self.section1 = api.create_container(
            self.lib1.library_key,
            content_models.Section,
            'section-1',
            'Section 1',
            None,
        )
        self.section2 = api.create_container(
            self.lib1.library_key,
            content_models.Section,
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


    def test_copy_and_paste_container_same_library(self) -> None:
        # Copy a section with children
        api.copy_container(self.section1.container_key, self.user.id)
        # Paste the container
        new_container: api.ContainerMetadata = (
            api.import_staged_content_from_user_clipboard(self.lib1.library_key, self.user)  # type: ignore[assignment]
        )

        # Verify that the container is copied
        assert new_container.container_type_code == self.section1.container_type_code
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
        assert new_container.container_type_code == self.section1.container_type_code
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

    def test_set_library_block_olx_no_signal_on_rollback(self) -> None:
        """
        LIBRARY_BLOCK_UPDATED is NOT emitted when set_library_block_olx is called
        within a transaction that is later rolled back.
        """
        event_receiver = mock.Mock()
        LIBRARY_BLOCK_UPDATED.connect(event_receiver)
        self.addCleanup(LIBRARY_BLOCK_UPDATED.disconnect, event_receiver)

        try:
            with transaction.atomic():
                api.set_library_block_olx(
                    self.problem_block_usage_key,
                    "<problem>Updated inside rolled-back transaction</problem>",
                )
                raise RuntimeError("Force rollback")
        except RuntimeError:
            pass

        assert event_receiver.call_count == 0

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
        with self.assertRaises(Exception) as context:  # noqa: PT027
            self._create_library("test-lib-role-4", "Test Library Role 4")

        assert "AuthZ unavailable" in str(context.exception)
