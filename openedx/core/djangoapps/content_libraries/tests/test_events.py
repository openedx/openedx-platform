"""
Tests for openedx_content-based Content Libraries
"""
from unittest import mock

from django.db import transaction
from opaque_keys.edx.locator import (
    LibraryCollectionLocator,
    LibraryContainerLocator,
    LibraryLocatorV2,
    LibraryUsageLocatorV2,
)
from openedx_content import api as content_api
from openedx_content import models_api as content_models
from openedx_events.content_authoring.signals import (
    CONTENT_LIBRARY_CREATED,
    CONTENT_LIBRARY_DELETED,
    CONTENT_LIBRARY_UPDATED,
    CONTENT_OBJECT_ASSOCIATIONS_CHANGED,
    LIBRARY_BLOCK_CREATED,
    LIBRARY_BLOCK_DELETED,
    LIBRARY_BLOCK_PUBLISHED,
    LIBRARY_BLOCK_UPDATED,
    LIBRARY_COLLECTION_CREATED,
    LIBRARY_COLLECTION_DELETED,
    LIBRARY_COLLECTION_UPDATED,
    LIBRARY_CONTAINER_CREATED,
    LIBRARY_CONTAINER_DELETED,
    LIBRARY_CONTAINER_PUBLISHED,
    LIBRARY_CONTAINER_UPDATED,
    ContentLibraryData,
    ContentObjectChangedData,
    LibraryBlockData,
    LibraryCollectionData,
    LibraryContainerData,
)

from openedx.core.djangoapps.content_libraries.tests.base import ContentLibrariesRestApiTest
from openedx.core.djangolib.testing.utils import skip_unless_cms

from .. import api


class BaseEventsTestCase(ContentLibrariesRestApiTest):
    """
    Base class for testing library events

    These tests use the REST API, which in turn relies on the Python API.
    """

    # Note: we assume all events are already enabled, as they should be. We do
    # NOT use OpenEdxEventsTestMixin, because it disables any events that you
    # don't explicitly enable and does so in a way that interferes with other
    # test cases, causing flakiness and failures in *other* test modules.
    ALL_EVENTS = [
        CONTENT_LIBRARY_CREATED,
        CONTENT_LIBRARY_DELETED,
        CONTENT_LIBRARY_UPDATED,
        CONTENT_OBJECT_ASSOCIATIONS_CHANGED,
        LIBRARY_BLOCK_CREATED,
        LIBRARY_BLOCK_DELETED,
        LIBRARY_BLOCK_UPDATED,
        LIBRARY_BLOCK_PUBLISHED,
        LIBRARY_COLLECTION_CREATED,
        LIBRARY_COLLECTION_DELETED,
        LIBRARY_COLLECTION_UPDATED,
        LIBRARY_CONTAINER_CREATED,
        LIBRARY_CONTAINER_DELETED,
        LIBRARY_CONTAINER_UPDATED,
        LIBRARY_CONTAINER_PUBLISHED,
    ]

    def setUp(self) -> None:
        super().setUp()

        # Create some useful data:
        self.lib1 = self._create_library(
            slug="test_lib_1",
            title="Library 1",
            description="First Library for testing",
        )
        self.lib1_key = LibraryLocatorV2.from_string(self.lib1['id'])

        # From now on, every time an event is emitted, add it to this set:
        self.new_events: list[dict] = []

        def event_receiver(**kwargs) -> None:
            self.new_events.append(kwargs)

        for e in self.ALL_EVENTS:
            e.connect(event_receiver)

        def disconnect_all() -> None:
            for e in self.ALL_EVENTS:
                e.disconnect(event_receiver)

        self.addCleanup(disconnect_all)

    def clear_events(self) -> None:
        """ Clear the log of events that we've seen so far. """
        self.new_events.clear()

    def expect_new_events(self, *expected_events: dict) -> None:
        """
        assert the the specified events have been emitted since the last call to
        this function.
        """
        # We assume the events may not be in order. Assuming a specific order can lead to flaky tests.
        for expected in expected_events:
            found = False
            for i, actual in enumerate(self.new_events):
                if expected.items() <= actual.items():
                    self.new_events.pop(i)
                    found = True
                    break
            if not found:
                raise AssertionError(f"Event {expected} not found among actual events:\n{self.new_events_str}")
        if len(self.new_events) > 0:
            raise AssertionError(f"Events were emitted but not expected:\n{self.new_events_str}")
        self.clear_events()

    @property
    def new_events_str(self) -> str:
        """Friendly-ish string representation of self.new_events"""
        simplified_events = [e.copy() for e in self.new_events]
        for e in simplified_events:
            if e["sender"] is None:
                del e["sender"]
            if e["from_event_bus"] is False:
                del e["from_event_bus"]
            del e["metadata"]
        return "\n".join([str(e) for e in simplified_events])


@skip_unless_cms
class ContentLibrariesEventsTestCase(BaseEventsTestCase):
    """
    Event tests for openedx_content-based Content Libraries
    """

    ############################## Libraries ##################################

    def test_content_library_crud_events(self) -> None:
        """
        Check that CONTENT_LIBRARY_CREATED event is sent when a content library is created, updated, and deleted
        """
        # Setup: none
        # Action - create a library
        new_lib = self._create_library(
            slug="new_lib",
            title="New Testing Library",
            description="New Library for testing",
        )
        lib_key = LibraryLocatorV2.from_string(new_lib['id'])

        # Expect a CREATED event:
        self.expect_new_events({
            "signal": CONTENT_LIBRARY_CREATED,
            "content_library": ContentLibraryData(library_key=lib_key),
        })

        # Action - change the library name:
        self._update_library(lib_key=str(lib_key), title="New title")
        # Expect an UPDATED event:
        self.expect_new_events({
            "signal": CONTENT_LIBRARY_UPDATED,
            "content_library": ContentLibraryData(library_key=lib_key),
        })

        # Action - delete the library:
        self._delete_library(str(lib_key))
        # Expect a DELETED event:
        self.expect_new_events({
            "signal": CONTENT_LIBRARY_DELETED,
            "content_library": ContentLibraryData(library_key=lib_key),
        })

    # Should deleting a library send out _DELETED events for all the items in the library too?

    ############################## Components (XBlocks) ##################################

    def test_library_block_create_event(self) -> None:
        """
        Check that LIBRARY_BLOCK_CREATED event is sent when a library block is created.
        """
        add_result = self._add_block_to_library(self.lib1_key, "problem", "problem1")
        usage_key = LibraryUsageLocatorV2.from_string(add_result["id"])

        self.expect_new_events({
            "signal": LIBRARY_BLOCK_CREATED,
            "library_block": LibraryBlockData(self.lib1_key, usage_key),
        })

    def test_library_block_update_and_publish_events(self) -> None:
        """
        Check that appropriate events are emitted when an existing block is updated.
        """
        # This block should be ignored:
        self._add_block_to_library(self.lib1_key, "problem", "problem1")
        # This block will be used in the tests:
        add_result = self._add_block_to_library(self.lib1_key, "problem", "problem2")
        usage_key = LibraryUsageLocatorV2.from_string(add_result["id"])
        # Clear events from creating the blocks:
        self.clear_events()

        # Now update the block's OLX:
        new_olx = """
        <problem display_name="New Multi Choice Question" max_attempts="5">
            <multiplechoiceresponse>...</multiplechoiceresponse>
        </problem>
        """.strip()
        self._set_library_block_olx(usage_key, new_olx)
        self.expect_new_events({
            "signal": LIBRARY_BLOCK_UPDATED,
            "library_block": LibraryBlockData(self.lib1_key, usage_key),
        })

        # Now add a static asset file to the block:
        self._set_library_block_asset(usage_key, "static/test.txt", b"data")
        self.expect_new_events({
            "signal": LIBRARY_BLOCK_UPDATED,
            "library_block": LibraryBlockData(self.lib1_key, usage_key),
        })

        # Then delete the static asset:
        self._delete_library_block_asset(usage_key, 'static/text.txt')
        self.expect_new_events({
            "signal": LIBRARY_BLOCK_UPDATED,
            "library_block": LibraryBlockData(self.lib1_key, usage_key),
        })

        # Then publish the block:
        self._publish_library_block(usage_key)
        self.expect_new_events({
            "signal": LIBRARY_BLOCK_PUBLISHED,
            "library_block": LibraryBlockData(self.lib1_key, usage_key),
        })

    def test_revert_delete(self) -> None:
        """
        Test that when a block is deleted and then the delete is reverted, a
        _CREATED event is sent.
        """
        # This block should be ignored:
        self._add_block_to_library(self.lib1_key, "problem", "problem1")
        # This block will be used in the tests:
        add_result = self._add_block_to_library(self.lib1_key, "problem", "problem2")
        usage_key = LibraryUsageLocatorV2.from_string(add_result["id"])
        # Publish changes
        self._commit_library_changes(self.lib1_key)
        # Clear events from creating the blocks:
        self.clear_events()

        # Delete the block:
        self._delete_library_block(usage_key)
        # That should emit a _DELETED event:
        self.expect_new_events({
            "signal": LIBRARY_BLOCK_DELETED,
            "library_block": LibraryBlockData(self.lib1_key, usage_key),
        })

        # Revert the change:
        self._revert_library_changes(self.lib1_key)
        # That should result in a _CREATED event:
        self.expect_new_events({
            "signal": LIBRARY_BLOCK_CREATED,
            "library_block": LibraryBlockData(self.lib1_key, usage_key),
        })

    def test_revert_create(self) -> None:
        """
        Test that when a block is created and then the changes are reverted, a
        _DELETED event is sent.
        """
        # Publish any changes from setUp()
        self._commit_library_changes(self.lib1_key)
        # Clear events:
        self.clear_events()

        # Create the block:
        add_result = self._add_block_to_library(self.lib1_key, "problem", "problem2")
        usage_key = LibraryUsageLocatorV2.from_string(add_result["id"])
        # That should result in a _CREATED event:
        self.expect_new_events({
            "signal": LIBRARY_BLOCK_CREATED,
            "library_block": LibraryBlockData(self.lib1_key, usage_key),
        })

        # Revert the change:
        self._revert_library_changes(self.lib1_key)
        # That should result in a _DELETED event:
        self.expect_new_events({
            "signal": LIBRARY_BLOCK_DELETED,
            "library_block": LibraryBlockData(self.lib1_key, usage_key),
        })

    ############################## Containers ##################################

    def test_unit_crud(self) -> None:
        """
        Test Create, Read, Update, and Delete of a Unit
        """
        # Create a unit:
        container_data = self._create_container(self.lib1_key, "unit", slug="u1", display_name="Test Unit")
        container_key = LibraryContainerLocator.from_string(container_data["id"])

        self.expect_new_events({
            "signal": LIBRARY_CONTAINER_CREATED,
            "library_container": LibraryContainerData(container_key),
        })

        # Update the unit:
        self._update_container(container_key, display_name="Unit ABC")

        self.expect_new_events({
            "signal": LIBRARY_CONTAINER_UPDATED,
            "library_container": LibraryContainerData(container_key),
        })

        # Delete the unit
        self._delete_container(container_key)
        self._get_container(container_key, expect_response=404)
        self.expect_new_events({
            "signal": LIBRARY_CONTAINER_DELETED,
            "library_container": LibraryContainerData(container_key),
        })

    def test_publish_all_lib_changes(self) -> None:
        """
        Test the events that get emitted when we publish all changes in the library
        """
        # Create two containers and add some components
        # -> container 1: problem_block, html_block
        # -> container 2: html_block, html_block2
        container1 = self._create_container(self.lib1_key, "unit", display_name="Alpha Unit", slug=None)
        container2 = self._create_container(self.lib1_key, "unit", display_name="Bravo Unit", slug=None)
        problem_block = self._add_block_to_library(self.lib1_key, "problem", "Problem1", can_stand_alone=False)
        html_block = self._add_block_to_library(self.lib1_key, "html", "Html1", can_stand_alone=False)
        html_block2 = self._add_block_to_library(self.lib1_key, "html", "Html2", can_stand_alone=False)
        self._add_container_children(container1["id"], children_ids=[problem_block["id"], html_block["id"]])
        self._add_container_children(container2["id"], children_ids=[html_block["id"], html_block2["id"]])

        # Now publish only Container 2 (which will auto-publish both HTML blocks since they're children)
        self._publish_container(container2["id"])
        # Container 2 is published, container 1 and its contents is unpublished:
        assert self._get_container(container2["id"])["has_unpublished_changes"] is False
        assert self._get_container(container1["id"])["has_unpublished_changes"]
        assert self._get_library_block(problem_block["id"])["has_unpublished_changes"]
        assert self._get_library_block(html_block["id"])["has_unpublished_changes"] is False  # in containers 1+2

        # clear event log up to this point
        self.clear_events()

        # Now publish ALL remaining changes in the library:
        self._commit_library_changes(self.lib1_key)
        # Container 1 is now published:
        assert self._get_container(container1["id"])["has_unpublished_changes"] is False
        # And publish events were emitted:
        self.expect_new_events(
            {  # An event for container 1 being published:
                "signal": LIBRARY_CONTAINER_PUBLISHED,
                "library_container": LibraryContainerData(
                    container_key=LibraryContainerLocator.from_string(container1["id"]),
                ),
            },
            {  # An event for the problem block in container 1:
                "signal": LIBRARY_BLOCK_PUBLISHED,
                "library_block": LibraryBlockData(
                    self.lib1_key, LibraryUsageLocatorV2.from_string(problem_block["id"]),
                ),
            },
            # The HTML block in container 1 is not part of this publish event group, because it was
            # already published when we published container 2
        )

    def test_publish_child_block(self) -> None:
        """
        Test the events that get emitted when we publish changes to a child of a container
        """
        # Create a container and a block
        container1 = self._create_container(self.lib1_key, "unit", display_name="Alpha Unit", slug=None)
        problem_block = self._add_block_to_library(self.lib1_key, "problem", "Problem1", can_stand_alone=False)
        self._add_container_children(container1["id"], children_ids=[problem_block["id"]])
        # Publish all changes
        self._commit_library_changes(self.lib1_key)
        assert self._get_container(container1["id"])["has_unpublished_changes"] is False

        # Change only the block, not the container:
        self._set_library_block_olx(problem_block["id"], "<problem>UPDATED</problem>")
        # Since we modified the block, the container now contains changes (technically it is unchanged and its
        # version is the same, but it *contains* unpublished changes)
        assert self._get_library_block(problem_block["id"])["has_unpublished_changes"]
        assert self._get_container(container1["id"])["has_unpublished_changes"]
        # clear event log up to this point
        self.clear_events()

        # Now publish ALL remaining changes in the library - should only affect the problem block
        self._commit_library_changes(self.lib1_key)
        # The container no longer contains unpublished changes:
        assert self._get_container(container1["id"])["has_unpublished_changes"] is False
        # And publish events were emitted:
        self.expect_new_events(
            {  # An event for container 1 being affected indirectly by the child being published:
                # TODO: should this be a CONTAINER_CHILD_PUBLISHED event?
                "signal": LIBRARY_CONTAINER_PUBLISHED,
                "library_container": LibraryContainerData(
                    container_key=LibraryContainerLocator.from_string(container1["id"]),
                ),
            },
            {  # An event for the problem block:
                "signal": LIBRARY_BLOCK_PUBLISHED,
                "library_block": LibraryBlockData(
                    self.lib1_key, LibraryUsageLocatorV2.from_string(problem_block["id"]),
                ),
            },
        )

    def test_publish_container(self) -> None:
        """
        Test the events that get emitted when we publish the changes to a specific container
        """
        # Create two containers and add some components
        container1 = self._create_container(self.lib1_key, "unit", display_name="Alpha Unit", slug=None)
        container2 = self._create_container(self.lib1_key, "unit", display_name="Bravo Unit", slug=None)
        problem_block = self._add_block_to_library(self.lib1_key, "problem", "Problem1", can_stand_alone=False)
        html_block = self._add_block_to_library(self.lib1_key, "html", "Html1", can_stand_alone=False)
        html_block2 = self._add_block_to_library(self.lib1_key, "html", "Html2", can_stand_alone=False)
        self._add_container_children(container1["id"], children_ids=[problem_block["id"], html_block["id"]])
        self._add_container_children(container2["id"], children_ids=[html_block["id"], html_block2["id"]])
        # At first everything is unpublished:
        c1_before = self._get_container(container1["id"])
        assert c1_before["has_unpublished_changes"]
        c2_before = self._get_container(container2["id"])
        assert c2_before["has_unpublished_changes"]

        # clear event log after the initial mock data setup is complete:
        self.clear_events()

        # Now publish only Container 1
        self._publish_container(container1["id"])

        # Now it is published:
        c1_after = self._get_container(container1["id"])
        assert c1_after["has_unpublished_changes"] is False
        # And publish events were emitted:
        self.expect_new_events(
            {  # An event for container 1 being published:
                "signal": LIBRARY_CONTAINER_PUBLISHED,
                "library_container": LibraryContainerData(
                    container_key=LibraryContainerLocator.from_string(container1["id"]),
                ),
            },
            {  # An event for the problem block in container 1:
                "signal": LIBRARY_BLOCK_PUBLISHED,
                "library_block": LibraryBlockData(
                    self.lib1_key, LibraryUsageLocatorV2.from_string(problem_block["id"]),
                ),
            },
            {  # An event for the html block in container 1 (and container 2):
                "signal": LIBRARY_BLOCK_PUBLISHED,
                "library_block": LibraryBlockData(
                    self.lib1_key, LibraryUsageLocatorV2.from_string(html_block["id"]),
                ),
            },
            # No PUBLISHED event is emitted for container 2, because it doesn't have a published version yet.
            # Publishing 'html_block' would have potentially affected it if container 2's published version had a
            # reference to 'html_block', but it doesn't yet until we publish it.
        )

        # note that container 2 is still unpublished
        c2_after = self._get_container(container2["id"])
        assert c2_after["has_unpublished_changes"]

        # publish container2 now:
        self._publish_container(container2["id"])
        self.expect_new_events(
            {  # An event for container 1 being published:
                "signal": LIBRARY_CONTAINER_PUBLISHED,
                "library_container": LibraryContainerData(
                    container_key=LibraryContainerLocator.from_string(container2["id"]),
                ),
            },
            {  # An event for the html block in container 2 only:
                "signal": LIBRARY_BLOCK_PUBLISHED,
                "library_block": LibraryBlockData(
                    self.lib1_key, LibraryUsageLocatorV2.from_string(html_block2["id"]),
                ),
            },
        )

    def test_publish_container_propagation(self) -> None:
        """
        Test the events that get emitted when we publish the changes to an entity
        that is used in multiple published containers
        """
        # Create two containers and add the same component to both:
        container1 = self._create_container(self.lib1_key, "unit", display_name="Alpha Unit", slug=None)
        container2 = self._create_container(self.lib1_key, "unit", display_name="Bravo Unit", slug=None)
        problem_block = self._add_block_to_library(self.lib1_key, "problem", "Problem1", can_stand_alone=False)
        self._add_container_children(container1["id"], children_ids=[problem_block["id"]])
        self._add_container_children(container2["id"], children_ids=[problem_block["id"]])
        # Publish everything:
        self._commit_library_changes(self.lib1_key)

        # clear event log after the initial mock data setup is complete:
        self.clear_events()

        # Now modify the problem that's shared by both containers and publish the new version
        self._set_library_block_olx(problem_block["id"], "<problem>UPDATED</problem>")
        self.clear_events()  # Clears the LIBRARY_BLOCK_UPDATED event + 2x LIBRARY_CONTAINER_UPDATED events

        # Now both containers have unpublished changes:
        assert self._get_container(container1["id"])["has_unpublished_changes"]
        assert self._get_container(container2["id"])["has_unpublished_changes"]
        # Publish container1, which also published the shared problem component:
        self._publish_container(container1["id"])
        # Now neither container has unpublished changes (even though we never touched container2):
        assert self._get_container(container1["id"])["has_unpublished_changes"] is False
        assert self._get_container(container2["id"])["has_unpublished_changes"] is False

        # And publish events were emitted:
        self.expect_new_events(
            # An event for the problem block in container 1 being indirectly published:
            {
                "signal": LIBRARY_BLOCK_PUBLISHED,
                "library_block": LibraryBlockData(
                    self.lib1_key, LibraryUsageLocatorV2.from_string(problem_block["id"]),
                ),
            },
            # An event for container 1 being published *directly*:
            {
                "signal": LIBRARY_CONTAINER_PUBLISHED,
                "library_container": LibraryContainerData(
                    container_key=LibraryContainerLocator.from_string(container1["id"]),
                ),
            },
            # And this time a PUBLISHED event should also be emitted for container2.
            # It's published version hasn't changed, but its "contains unpublished changes" status has.
            {
                "signal": LIBRARY_CONTAINER_PUBLISHED,
                "library_container": LibraryContainerData(
                    container_key=LibraryContainerLocator.from_string(container2["id"]),
                ),
            },
        )

    def test_publish_child_container(self):
        """
        Test the events that get emitted when we publish the changes to a container that is child of another container
        """
        # Create some containers
        unit = self._create_container(self.lib1_key, "unit", display_name="Alpha Unit", slug=None)
        subsection = self._create_container(self.lib1_key, "subsection", display_name="Bravo Subsection", slug=None)

        # Add one container as child
        self._add_container_children(subsection["id"], children_ids=[unit["id"]])

        # At first everything is unpublished:
        c1_before = self._get_container(unit["id"])
        assert c1_before["has_unpublished_changes"]
        c2_before = self._get_container(subsection["id"])
        assert c2_before["has_unpublished_changes"]

        # clear event log after the initial mock data setup is complete:
        self.clear_events()

        # Now publish only the unit
        self._publish_container(unit["id"])

        # Now it is published:
        c1_after = self._get_container(unit["id"])
        assert c1_after["has_unpublished_changes"] is False

        # And publish events were emitted:
        self.expect_new_events(
            {  # An event for the unit being published:
                "signal": LIBRARY_CONTAINER_PUBLISHED,
                "library_container": LibraryContainerData(
                    container_key=LibraryContainerLocator.from_string(unit["id"]),
                ),
            },
            # No PUBLISHED event is emitted for the subsection, because it doesn't have a published version yet.
        )

        # note that subsection is still unpublished
        c2_after = self._get_container(subsection["id"])
        assert c2_after["has_unpublished_changes"]

        # Now publish the subsection
        self._publish_container(subsection["id"])
        self.expect_new_events(
            {  # An event for the subsection being published:
                "signal": LIBRARY_CONTAINER_PUBLISHED,
                "library_container": LibraryContainerData(
                    container_key=LibraryContainerLocator.from_string(subsection["id"]),
                ),
            },
        )

        # Now rename the unit:
        self._update_container(unit["id"], 'New Unit Display Name')
        self.clear_events()
        # Publish changes to the unit:
        self._publish_container(unit["id"])
        self.expect_new_events(
            {  # An event for the unit being published:
                "signal": LIBRARY_CONTAINER_PUBLISHED,
                "library_container": LibraryContainerData(
                    container_key=LibraryContainerLocator.from_string(unit["id"]),
                ),
            },
            # And this time we DO get notified that the parent container is affected, because the unit is in its
            # published version, and this publish affects the parent's "contains_unpublished_changes" status.
            {
                "signal": LIBRARY_CONTAINER_PUBLISHED,
                "library_container": LibraryContainerData(
                    container_key=LibraryContainerLocator.from_string(subsection["id"]),
                ),
            },
        )

    def test_restore_unit(self) -> None:
        """
        Test restoring a deleted unit via the "restore" API.
        """
        # Create a unit:
        container_data = self._create_container(self.lib1_key, "unit", slug="u1", display_name="Test Unit")
        container_key = LibraryContainerLocator.from_string(container_data["id"])

        self.expect_new_events({
            "signal": LIBRARY_CONTAINER_CREATED,
            "library_container": LibraryContainerData(container_key),
        })

        # Delete the unit
        self._delete_container(container_data["id"])

        self.expect_new_events({
            "signal": LIBRARY_CONTAINER_DELETED,
            "library_container": LibraryContainerData(container_key),
        })

        # Restore the unit
        self._restore_container(container_data["id"])

        self.expect_new_events(
            {
                "signal": LIBRARY_CONTAINER_CREATED,
                "library_container": LibraryContainerData(container_key),
            },
            # We used to emit CONTENT_OBJECT_ASSOCIATIONS_CHANGED here for the restored container, specifically noting
            # that changes=["collections", "tags"], because deleted things may have collections+tags that are once
            # again relevant when it is restored. However, the CREATED event should be sufficient for notifying of that.
            # (Or should we emit CREATED+UPDATED to be extra sure?)
        )

    def test_restore_unit_via_revert(self) -> None:
        """
        Test restoring a deleted unit by reverting changes.
        """
        # Publish the existing setup and clear events
        self._commit_library_changes(self.lib1_key)
        self.clear_events()

        # Create a unit:
        container_data = self._create_container(self.lib1_key, "unit", slug="u1", display_name="Test Unit")
        container_key = LibraryContainerLocator.from_string(container_data["id"])

        self.expect_new_events({
            "signal": LIBRARY_CONTAINER_CREATED,
            "library_container": LibraryContainerData(container_key),
        })

        # Publish changes
        self._publish_container(container_key)
        self.expect_new_events({
            "signal": LIBRARY_CONTAINER_PUBLISHED,
            "library_container": LibraryContainerData(container_key),
        })

        # Delete the unit
        self._delete_container(container_data["id"])

        self.expect_new_events({
            "signal": LIBRARY_CONTAINER_DELETED,
            "library_container": LibraryContainerData(container_key),
        })

        # Revert changes, which will re-create the unit:
        self._revert_library_changes(self.lib1_key)

        self.expect_new_events({
            "signal": LIBRARY_CONTAINER_CREATED,
            "library_container": LibraryContainerData(container_key),
        })

    ############################## Collections ##################################

    def test_collection_crud(self) -> None:
        """ Test basic create, update, and delete events for collections """
        collection = self._create_collection(self.lib1_key, "Test Collection")
        # To fix? The response from _create_collection should have the opaque key as the "id" field, not an integer.
        collection_key = LibraryCollectionLocator(lib_key=self.lib1_key, collection_id=collection["key"])
        self.expect_new_events({
            "signal": LIBRARY_COLLECTION_CREATED,
            "library_collection": LibraryCollectionData(collection_key),
        })

        # Update the collection:
        self._update_collection(collection_key, description="Updated description")
        self.expect_new_events({
            "signal": LIBRARY_COLLECTION_UPDATED,
            "library_collection": LibraryCollectionData(collection_key),
        })

        # Soft delete the collection. Whether we "soft" or "hard" delete, it sends a "DELETED" event.
        # If we later restore it, it would send a "CREATED" event.
        self._soft_delete_collection(collection_key)
        self.expect_new_events({
            "signal": LIBRARY_COLLECTION_DELETED,
            "library_collection": LibraryCollectionData(collection_key),
        })

@skip_unless_cms
class ContentLibraryContainerEventsTest(BaseEventsTestCase):
    """
    Event tests for container operations: signals emitted when components and
    containers are created, updated, deleted, and associated with one another.

    setUp() builds the following structure in lib1 (note that some entities
    are shared across multiple parents, so this is a DAG, not a strict tree)::

        Section 1                                    Section 2
        ├── Subsection 1 ◄───────── (shared) ────────┴── Subsection 1
        │   ├── Unit 1 ◄────────────────┐ (shared)
        │   │   ├── problem1            │
        │   │   └── html1 ◄──┐          │
        │   └── Unit 2       │(shared)  │
        │       └── html1 ◄──┘          │
        └── Subsection 2                │
            └── Unit 1 ◄────────────────┘

        Orphans (created but not attached to any parent):
            Unit 3, problem2
    """

    def setUp(self) -> None:
        super().setUp()

        # Create Units
        self.unit1 = api.create_container(self.lib1_key, content_models.Unit, 'unit-1', 'Unit 1', None)
        self.unit2 = api.create_container(self.lib1_key, content_models.Unit, 'unit-2', 'Unit 2', None)
        self.unit3 = api.create_container(self.lib1_key, content_models.Unit, 'unit-3', 'Unit 3', None)

        # Create Subsections
        self.subsection1 = api.create_container(
            self.lib1_key, content_models.Subsection, 'subsection-1', 'Subsection 1', None,
        )
        self.subsection2 = api.create_container(
            self.lib1_key, content_models.Subsection, 'subsection-2', 'Subsection 2', None,
        )

        # Create Sections
        self.section1 = api.create_container(
            self.lib1_key, content_models.Section, 'section-1', 'Section 1', None,
        )
        self.section2 = api.create_container(
            self.lib1_key, content_models.Section, 'section-2', 'Section 2', None,
        )

        # Create XBlocks
        self.problem_block = self._add_block_to_library(self.lib1_key, "problem", "problem1")
        self.problem_block_usage_key = LibraryUsageLocatorV2.from_string(self.problem_block["id"])
        self.problem_block_2 = self._add_block_to_library(self.lib1_key, "problem", "problem2")
        self.html_block = self._add_block_to_library(self.lib1_key, "html", "html1")
        self.html_block_usage_key = LibraryUsageLocatorV2.from_string(self.html_block["id"])

        # Add content to units
        api.update_container_children(
            self.unit1.container_key, [self.problem_block_usage_key, self.html_block_usage_key], None,
        )
        api.update_container_children(
            self.unit2.container_key, [self.html_block_usage_key], None,
        )

        # Add units to subsections
        api.update_container_children(
            self.subsection1.container_key, [self.unit1.container_key, self.unit2.container_key], None,
        )
        api.update_container_children(
            self.subsection2.container_key, [self.unit1.container_key], None,
        )

        # Add subsections to sections
        api.update_container_children(
            self.section1.container_key, [self.subsection1.container_key, self.subsection2.container_key], None,
        )
        api.update_container_children(
            self.section2.container_key, [self.subsection1.container_key], None,
        )

        # Clear events emitted during setUp
        self.clear_events()

    ############################## Component update signals ##################################

    def test_container_updated_when_component_deleted(self) -> None:
        api.delete_library_block(self.html_block_usage_key)
        self.expect_new_events(
            # The block itself was deleted:
            {
                "signal": LIBRARY_BLOCK_DELETED,
                "library_block": LibraryBlockData(self.lib1_key, self.html_block_usage_key),
            },
            # That block was a child of two units, so both parent units are flagged as updated
            # e.g. to update their "child_display_names" in the search index.
            {
                "signal": LIBRARY_CONTAINER_UPDATED,
                "library_container": LibraryContainerData(container_key=self.unit1.container_key),
            },
            {
                "signal": LIBRARY_CONTAINER_UPDATED,
                "library_container": LibraryContainerData(container_key=self.unit2.container_key),
            },
            # openedx_content also lists ancestor containers of the affected units as changed.
            # We don't strictly need this at the moment, at least as far as keeping our search index updated.
            {
                "signal": LIBRARY_CONTAINER_UPDATED,
                "library_container": LibraryContainerData(container_key=self.subsection1.container_key),
            },
            {
                "signal": LIBRARY_CONTAINER_UPDATED,
                "library_container": LibraryContainerData(container_key=self.subsection2.container_key),
            },
            {
                "signal": LIBRARY_CONTAINER_UPDATED,
                "library_container": LibraryContainerData(container_key=self.section1.container_key),
            },
            {
                "signal": LIBRARY_CONTAINER_UPDATED,
                "library_container": LibraryContainerData(container_key=self.section2.container_key),
            },
        )

    def test_container_updated_when_component_restored(self) -> None:
        api.delete_library_block(self.html_block_usage_key)
        self.clear_events()

        api.restore_library_block(self.html_block_usage_key)
        self.expect_new_events(
            # Restoring the block re-creates it:
            {
                "signal": LIBRARY_BLOCK_CREATED,
                "library_block": LibraryBlockData(self.lib1_key, self.html_block_usage_key),
            },
            # We used to emit CONTENT_OBJECT_ASSOCIATIONS_CHANGED here for the restored block, specifically noting
            # that changes=["collections", "tags", "units"], because deleted things may have collections+tags+containers
            # that are once again relevant when it is restored. However, the CREATED event should be sufficient for
            # notifying of that. (Or should we emit CREATED+UPDATED to be extra sure?)
            # The restored block is a child of two units, so both parent units are flagged as updated:
            {
                "signal": LIBRARY_CONTAINER_UPDATED,
                "library_container": LibraryContainerData(container_key=self.unit1.container_key),
            },
            {
                "signal": LIBRARY_CONTAINER_UPDATED,
                "library_container": LibraryContainerData(container_key=self.unit2.container_key),
            },
            # openedx_content also lists ancestor containers of the affected units as changed.
            # We don't strictly need this at the moment, at least as far as keeping our search index updated.
            {
                "signal": LIBRARY_CONTAINER_UPDATED,
                "library_container": LibraryContainerData(container_key=self.subsection1.container_key),
            },
            {
                "signal": LIBRARY_CONTAINER_UPDATED,
                "library_container": LibraryContainerData(container_key=self.subsection2.container_key),
            },
            {
                "signal": LIBRARY_CONTAINER_UPDATED,
                "library_container": LibraryContainerData(container_key=self.section1.container_key),
            },
            {
                "signal": LIBRARY_CONTAINER_UPDATED,
                "library_container": LibraryContainerData(container_key=self.section2.container_key),
            },
        )

    def test_container_updated_when_component_olx_updated(self) -> None:
        self._set_library_block_olx(self.html_block_usage_key, "<html><b>Hello world!</b></html>")
        self.expect_new_events(
            # The block's OLX changed:
            {
                "signal": LIBRARY_BLOCK_UPDATED,
                "library_block": LibraryBlockData(self.lib1_key, self.html_block_usage_key),
            },
            # That block is used in two units, so both parent units are flagged as updated
            # e.g. to update their "child_display_names" in the search index, if the child's name has changed.
            {
                "signal": LIBRARY_CONTAINER_UPDATED,
                "library_container": LibraryContainerData(container_key=self.unit1.container_key),
            },
            {
                "signal": LIBRARY_CONTAINER_UPDATED,
                "library_container": LibraryContainerData(container_key=self.unit2.container_key),
            },
            # openedx_content also lists ancestor containers of the affected units as changed.
            # We don't strictly need this at the moment, at least as far as keeping our search index updated.
            {
                "signal": LIBRARY_CONTAINER_UPDATED,
                "library_container": LibraryContainerData(container_key=self.subsection1.container_key),
            },
            {
                "signal": LIBRARY_CONTAINER_UPDATED,
                "library_container": LibraryContainerData(container_key=self.subsection2.container_key),
            },
            {
                "signal": LIBRARY_CONTAINER_UPDATED,
                "library_container": LibraryContainerData(container_key=self.section1.container_key),
            },
            {
                "signal": LIBRARY_CONTAINER_UPDATED,
                "library_container": LibraryContainerData(container_key=self.section2.container_key),
            },
        )

    def test_container_updated_when_component_fields_updated(self) -> None:
        block_olx = "<html><b>Hello world!</b></html>"
        self._set_library_block_fields(self.html_block_usage_key, {"data": block_olx, "metadata": {}})
        self.expect_new_events(
            {
                "signal": LIBRARY_BLOCK_UPDATED,
                "library_block": LibraryBlockData(self.lib1_key, self.html_block_usage_key),
            },
            # That block is used in two containers, so we expect events for them too:
            # This is used e.g. to update "child_display_names" in the search index, if the child's name has changed.
            {
                "signal": LIBRARY_CONTAINER_UPDATED,
                "library_container": LibraryContainerData(container_key=self.unit1.container_key),
            },
            {
                "signal": LIBRARY_CONTAINER_UPDATED,
                "library_container": LibraryContainerData(container_key=self.unit2.container_key),
            },
            # openedx_content also lists and parent containers of affected containers as changed, and so on...
            # We don't strictly need this at the moment, at least as far as keeping our search index updated.
            {
                "signal": LIBRARY_CONTAINER_UPDATED,
                "library_container": LibraryContainerData(container_key=self.subsection1.container_key),
            },
            {
                "signal": LIBRARY_CONTAINER_UPDATED,
                "library_container": LibraryContainerData(container_key=self.subsection2.container_key),
            },
            {
                "signal": LIBRARY_CONTAINER_UPDATED,
                "library_container": LibraryContainerData(container_key=self.section1.container_key),
            },
            {
                "signal": LIBRARY_CONTAINER_UPDATED,
                "library_container": LibraryContainerData(container_key=self.section2.container_key),
            },
        )

    ############################## Container update signals ##################################

    def test_container_updated_when_unit_updated(self) -> None:
        self._update_container(self.unit1.container_key, 'New Unit Display Name')
        self.expect_new_events(
            # We renamed this unit, so we get an UPDATED event for it:
            {
                "signal": LIBRARY_CONTAINER_UPDATED,
                "library_container": LibraryContainerData(container_key=self.unit1.container_key),
            },
            # We also get events for its parent containers
            # e.g. to update their "child_display_names" in the search index.
            {
                "signal": LIBRARY_CONTAINER_UPDATED,
                "library_container": LibraryContainerData(container_key=self.subsection1.container_key),
            },
            {
                "signal": LIBRARY_CONTAINER_UPDATED,
                "library_container": LibraryContainerData(container_key=self.subsection2.container_key),
            },
            # openedx_content also lists ancestor containers of the affected unit as changed.
            # We don't strictly need this at the moment, at least as far as keeping our search index updated.
            {
                "signal": LIBRARY_CONTAINER_UPDATED,
                "library_container": LibraryContainerData(container_key=self.section1.container_key),
            },
            {
                "signal": LIBRARY_CONTAINER_UPDATED,
                "library_container": LibraryContainerData(container_key=self.section2.container_key),
            },
            # Finally, any child components receive a "units changed" notification
            # e.g. to update the "units this component is used in" in the search index.
            {
                "signal": CONTENT_OBJECT_ASSOCIATIONS_CHANGED,
                "content_object": ContentObjectChangedData(
                    object_id=str(self.problem_block_usage_key), changes=["units"],
                ),
            },
            {
                "signal": CONTENT_OBJECT_ASSOCIATIONS_CHANGED,
                "content_object": ContentObjectChangedData(
                    object_id=str(self.html_block_usage_key), changes=["units"],
                ),
            },
        )

    def test_container_updated_when_subsection_updated(self) -> None:
        self._update_container(self.subsection1.container_key, 'New Subsection Display Name')
        self.expect_new_events(
            # We renamed this container, so we get an UPDATED event for it:
            {
                "signal": LIBRARY_CONTAINER_UPDATED,
                "library_container": LibraryContainerData(container_key=self.subsection1.container_key),
            },
            # We also get events for its parent containers
            # e.g. to update their "child_display_names" in the search index
            {
                "signal": LIBRARY_CONTAINER_UPDATED,
                "library_container": LibraryContainerData(container_key=self.section1.container_key),
            },
            {
                "signal": LIBRARY_CONTAINER_UPDATED,
                "library_container": LibraryContainerData(container_key=self.section2.container_key),
            },
            # Finally, any child containers receive a "subsections changed" notification
            # e.g. to update the "subsections this unit is used in" in the search index.
            {
                "signal": CONTENT_OBJECT_ASSOCIATIONS_CHANGED,
                "content_object": ContentObjectChangedData(
                    object_id=str(self.unit1.container_key), changes=["subsections"],
                ),
            },
            {
                "signal": CONTENT_OBJECT_ASSOCIATIONS_CHANGED,
                "content_object": ContentObjectChangedData(
                    object_id=str(self.unit2.container_key), changes=["subsections"],
                ),
            },
        )

    def test_container_updated_when_section_updated(self) -> None:
        self._update_container(self.section1.container_key, 'New Section Display Name')
        self.expect_new_events(
            {
                "signal": LIBRARY_CONTAINER_UPDATED,
                "library_container": LibraryContainerData(container_key=self.section1.container_key),
            },
            {
                "signal": CONTENT_OBJECT_ASSOCIATIONS_CHANGED,
                "content_object": ContentObjectChangedData(
                    object_id=str(self.subsection1.container_key), changes=["sections"],
                ),
            },
            {
                "signal": CONTENT_OBJECT_ASSOCIATIONS_CHANGED,
                "content_object": ContentObjectChangedData(
                    object_id=str(self.subsection2.container_key), changes=["sections"],
                ),
            },
        )

    ############################## Association change signals ##################################

    def test_associations_changed_when_component_removed(self) -> None:
        html_block_3 = self._add_block_to_library(self.lib1_key, "html", "html3")
        api.update_container_children(
            self.unit2.container_key,
            [LibraryUsageLocatorV2.from_string(html_block_3["id"])],
            None,
            entities_action=content_api.ChildrenEntitiesAction.APPEND,
        )
        self.clear_events()

        api.update_container_children(
            self.unit2.container_key,
            [LibraryUsageLocatorV2.from_string(html_block_3["id"])],
            None,
            entities_action=content_api.ChildrenEntitiesAction.REMOVE,
        )
        self.expect_new_events(
            {
                "signal": CONTENT_OBJECT_ASSOCIATIONS_CHANGED,
                "content_object": ContentObjectChangedData(
                    object_id=html_block_3["id"], changes=["units"],
                ),
            },
            {
                "signal": LIBRARY_CONTAINER_UPDATED,
                "library_container": LibraryContainerData(container_key=self.unit2.container_key),
            },
            # Because we removed html3 from unit2, the ancestor containers of unit2 are also emitted as changed.
            # We don't strictly need this at the moment, at least as far as keeping our search index updated.
            {
                "signal": LIBRARY_CONTAINER_UPDATED,
                "library_container": LibraryContainerData(container_key=self.subsection1.container_key),
            },
            {
                "signal": LIBRARY_CONTAINER_UPDATED,
                "library_container": LibraryContainerData(container_key=self.section1.container_key),
            },
            {
                "signal": LIBRARY_CONTAINER_UPDATED,
                "library_container": LibraryContainerData(container_key=self.section2.container_key),
            },
        )

    def test_associations_changed_when_unit_removed(self) -> None:
        unit4 = api.create_container(self.lib1_key, content_models.Unit, 'unit-4', 'Unit 4', None)
        api.update_container_children(
            self.subsection2.container_key,
            [unit4.container_key],
            None,
            entities_action=content_api.ChildrenEntitiesAction.APPEND,
        )
        self.clear_events()

        api.update_container_children(
            self.subsection2.container_key,
            [unit4.container_key],
            None,
            entities_action=content_api.ChildrenEntitiesAction.REMOVE,
        )
        self.expect_new_events(
            # unit4 was removed from subsection2, so we get a notification that "parent subsection(s) have changed":
            {
                "signal": CONTENT_OBJECT_ASSOCIATIONS_CHANGED,
                "content_object": ContentObjectChangedData(
                    object_id=str(unit4.container_key), changes=["subsections"],
                ),
            },
            # We modified subsection2 by changing its list of children, so we get a CONTAINER_UPDATED event for it:
            {
                "signal": LIBRARY_CONTAINER_UPDATED,
                "library_container": LibraryContainerData(container_key=self.subsection2.container_key),
            },
            # Because subsection2 itself was changed, we get change notifications for its ancestors.
            # We don't strictly need this at the moment, at least as far as keeping our search index updated.
            {
                "signal": LIBRARY_CONTAINER_UPDATED,
                "library_container": LibraryContainerData(container_key=self.section1.container_key),
            },
        )

    def test_associations_changed_when_subsection_removed(self) -> None:
        subsection3 = api.create_container(
            self.lib1_key, content_models.Subsection, 'subsection-3', 'Subsection 3', None,
        )
        api.update_container_children(
            self.section2.container_key,
            [subsection3.container_key],
            None,
            entities_action=content_api.ChildrenEntitiesAction.APPEND,
        )
        self.clear_events()

        api.update_container_children(
            self.section2.container_key,
            [subsection3.container_key],
            None,
            entities_action=content_api.ChildrenEntitiesAction.REMOVE,
        )
        self.expect_new_events(
            {
                "signal": CONTENT_OBJECT_ASSOCIATIONS_CHANGED,
                "content_object": ContentObjectChangedData(
                    object_id=str(subsection3.container_key), changes=["sections"],
                ),
            },
            {
                "signal": LIBRARY_CONTAINER_UPDATED,
                "library_container": LibraryContainerData(container_key=self.section2.container_key),
            },
        )

    def test_associations_changed_when_components_added(self) -> None:
        html_block_1 = self._add_block_to_library(self.lib1_key, "html", "html4")
        html_block_2 = self._add_block_to_library(self.lib1_key, "html", "html5")
        self.clear_events()

        api.update_container_children(
            self.unit2.container_key,
            [
                LibraryUsageLocatorV2.from_string(html_block_1["id"]),
                LibraryUsageLocatorV2.from_string(html_block_2["id"]),
            ],
            None,
            entities_action=content_api.ChildrenEntitiesAction.APPEND,
        )
        self.expect_new_events(
            # We added html4 and html4 to a new unit, so they get "parent unit(s) changed" events:
            {
                "signal": CONTENT_OBJECT_ASSOCIATIONS_CHANGED,
                "content_object": ContentObjectChangedData(
                    object_id=html_block_1["id"], changes=["units"],
                ),
            },
            {
                "signal": CONTENT_OBJECT_ASSOCIATIONS_CHANGED,
                "content_object": ContentObjectChangedData(
                    object_id=html_block_2["id"], changes=["units"],
                ),
            },
            # We modified unit2 by changing its list of children, so we get a CONTAINER_UPDATED event for it:
            {
                "signal": LIBRARY_CONTAINER_UPDATED,
                "library_container": LibraryContainerData(container_key=self.unit2.container_key),
            },
            # Because the unit itself was changed, we get change notifications for its ancestors.
            # We don't strictly need this at the moment, at least as far as keeping our search index updated.
            {
                "signal": LIBRARY_CONTAINER_UPDATED,
                "library_container": LibraryContainerData(container_key=self.subsection1.container_key),
            },
            {
                "signal": LIBRARY_CONTAINER_UPDATED,
                "library_container": LibraryContainerData(container_key=self.section1.container_key),
            },
            {
                "signal": LIBRARY_CONTAINER_UPDATED,
                "library_container": LibraryContainerData(container_key=self.section2.container_key),
            },
        )

    def test_associations_changed_when_units_added(self) -> None:
        # Create "unit4" and "unit5" and add them to subsection2:
        unit4 = api.create_container(self.lib1_key, content_models.Unit, 'unit-4', 'Unit 4', None)
        unit5 = api.create_container(self.lib1_key, content_models.Unit, 'unit-5', 'Unit 5', None)
        self.clear_events()

        api.update_container_children(
            self.subsection2.container_key,
            [unit4.container_key, unit5.container_key],
            None,
            entities_action=content_api.ChildrenEntitiesAction.APPEND,
        )
        self.expect_new_events(
            # Each unit was added to a new subsection, so we get a "subsections changed" event for each:
            {
                "signal": CONTENT_OBJECT_ASSOCIATIONS_CHANGED,
                "content_object": ContentObjectChangedData(
                    object_id=str(unit4.container_key), changes=["subsections"],
                ),
            },
            {
                "signal": CONTENT_OBJECT_ASSOCIATIONS_CHANGED,
                "content_object": ContentObjectChangedData(
                    object_id=str(unit5.container_key), changes=["subsections"],
                ),
            },
            # The subsection itself was updated (its list of children changed):
            {
                "signal": LIBRARY_CONTAINER_UPDATED,
                "library_container": LibraryContainerData(container_key=self.subsection2.container_key),
            },
            # And because the subsection itself was changed, we get change notifications for its ancestors.
            {
                "signal": LIBRARY_CONTAINER_UPDATED,
                "library_container": LibraryContainerData(container_key=self.section1.container_key),
            },
        )

    def test_associations_changed_when_subsections_added(self) -> None:
        subsection3 = api.create_container(
            self.lib1_key, content_models.Subsection, 'subsection-3', 'Subsection 3', None,
        )
        subsection4 = api.create_container(
            self.lib1_key, content_models.Subsection, 'subsection-4', 'Subsection 4', None,
        )
        self.clear_events()

        api.update_container_children(
            self.section2.container_key,
            [subsection3.container_key, subsection4.container_key],
            None,
            entities_action=content_api.ChildrenEntitiesAction.APPEND,
        )
        self.expect_new_events(
            {
                "signal": CONTENT_OBJECT_ASSOCIATIONS_CHANGED,
                "content_object": ContentObjectChangedData(
                    object_id=str(subsection3.container_key), changes=["sections"],
                ),
            },
            {
                "signal": CONTENT_OBJECT_ASSOCIATIONS_CHANGED,
                "content_object": ContentObjectChangedData(
                    object_id=str(subsection4.container_key), changes=["sections"],
                ),
            },
            {
                "signal": LIBRARY_CONTAINER_UPDATED,
                "library_container": LibraryContainerData(container_key=self.section2.container_key),
            },
        )

    ############################## Revert signals ##################################

    def test_container_updated_when_component_delete_reverted(self) -> None:
        """
        When a component is deleted and then the delete is reverted, signals
        will be emitted to update any containing containers.
        """
        problem_block_2_key = LibraryUsageLocatorV2.from_string(self.problem_block_2["id"])
        api.update_container_children(self.unit3.container_key, [problem_block_2_key], user_id=None)
        api.publish_changes(self.lib1_key)
        api.delete_library_block(problem_block_2_key)
        self.clear_events()

        api.revert_changes(self.lib1_key)
        self.expect_new_events(
            {
                "signal": LIBRARY_BLOCK_CREATED,
                "library_block": LibraryBlockData(library_key=self.lib1_key, usage_key=problem_block_2_key),
            },
            {
                "signal": LIBRARY_CONTAINER_UPDATED,
                "library_container": LibraryContainerData(container_key=self.unit3.container_key),
            },
        )

    ############################## Transaction/signal correctness ##################################

    def test_no_signal_on_set_block_olx_rollback(self) -> None:
        """
        LIBRARY_BLOCK_UPDATED is NOT emitted when set_library_block_olx is called
        within a transaction that is later rolled back.
        """
        try:
            with transaction.atomic():
                api.set_library_block_olx(
                    self.problem_block_usage_key,
                    "<problem>Updated inside rolled-back transaction</problem>",
                )
                raise RuntimeError("Force rollback")
        except RuntimeError:
            pass

        self.expect_new_events()

    def test_signal_emitted_when_set_block_olx_succeeds(self) -> None:
        """
        LIBRARY_BLOCK_UPDATED IS emitted when set_library_block_olx completes
        successfully.
        """
        api.set_library_block_olx(
            self.problem_block_usage_key,
            "<problem>Updated successfully</problem>",
        )
        self.expect_new_events(
            {
                "signal": LIBRARY_BLOCK_UPDATED,
                "library_block": LibraryBlockData(
                    library_key=self.lib1_key,
                    usage_key=self.problem_block_usage_key,
                ),
            },
            # Since the problem is part of a unit, we also get LIBRARY_CONTAINER_UPDATED on the parent unit.
            # This is used e.g. to update "child_display_names" in the search index, if the child's name has changed.
            {
                "signal": LIBRARY_CONTAINER_UPDATED,
                "library_container": LibraryContainerData(container_key=self.unit1.container_key),
            },
            # openedx_content also lists and parent containers of affected containers as changed, and so on...
            # We don't strictly need this at the moment, at least as far as keeping our search index updated.
            {
                "signal": LIBRARY_CONTAINER_UPDATED,
                "library_container": LibraryContainerData(container_key=self.subsection1.container_key),
            },
            {
                "signal": LIBRARY_CONTAINER_UPDATED,
                "library_container": LibraryContainerData(container_key=self.subsection2.container_key),
            },
            {
                "signal": LIBRARY_CONTAINER_UPDATED,
                "library_container": LibraryContainerData(container_key=self.section1.container_key),
            },
            {
                "signal": LIBRARY_CONTAINER_UPDATED,
                "library_container": LibraryContainerData(container_key=self.section2.container_key),
            },
        )

    def test_no_signals_on_import_container_failure(self) -> None:
        """
        When import_staged_content_from_user_clipboard fails mid-way, none of
        LIBRARY_CONTAINER_CREATED, LIBRARY_BLOCK_CREATED, or LIBRARY_BLOCK_UPDATED
        are emitted, so the search index is not polluted with orphan entries.
        """
        api.copy_container(self.unit1.container_key, self.user.id)

        with mock.patch(
            "openedx.core.djangoapps.content_libraries.api.blocks.update_container_children",
            side_effect=RuntimeError("Simulated failure"),
        ), self.assertRaises(RuntimeError):  # noqa: PT027
            api.import_staged_content_from_user_clipboard(self.lib1_key, self.user)

        self.expect_new_events()

    def test_signals_emitted_on_import_container_success(self) -> None:
        """
        When import_staged_content_from_user_clipboard succeeds, LIBRARY_CONTAINER_CREATED
        is emitted for the new container, along with association change events for its children.
        """
        api.copy_container(self.unit1.container_key, self.user.id)
        new_container = api.import_staged_content_from_user_clipboard(self.lib1_key, self.user)
        new_container_key = new_container.container_key  # type: ignore[attr-defined]
        self.expect_new_events(
            {
                "signal": LIBRARY_CONTAINER_CREATED,
                "library_container": LibraryContainerData(container_key=new_container_key),
            },
            {
                "signal": CONTENT_OBJECT_ASSOCIATIONS_CHANGED,
                "content_object": ContentObjectChangedData(
                    object_id=str(self.problem_block_usage_key), changes=["units"],
                ),
            },
            {
                "signal": CONTENT_OBJECT_ASSOCIATIONS_CHANGED,
                "content_object": ContentObjectChangedData(
                    object_id=str(self.html_block_usage_key), changes=["units"],
                ),
            },
            {
                "signal": LIBRARY_CONTAINER_UPDATED,
                "library_container": LibraryContainerData(container_key=new_container_key),
            },
        )
