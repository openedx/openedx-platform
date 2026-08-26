"""
Test for content tagging handlers
"""
from __future__ import annotations

from django.test import LiveServerTestCase
from organizations.models import Organization

from common.djangoapps.student.tests.factories import UserFactory
from openedx.core.djangoapps.content_libraries.api import (
    create_library,
    create_library_block,
    delete_library_block,
    restore_library_block,
)
from openedx.core.djangolib.testing.utils import skip_unless_cms
from xmodule.modulestore.tests.django_utils import (
    TEST_DATA_SPLIT_MODULESTORE,
    ImmediateOnCommitMixin,
    ModuleStoreTestCase,
)

from .. import api
from ..types import ContentKey


@skip_unless_cms  # Automatically deleting tags when an object is deleted only applies to the CMS
class TestContentTaggingHandlers(  # type: ignore[misc]
    ImmediateOnCommitMixin,
    ModuleStoreTestCase,
    LiveServerTestCase
):
    """
    Test Content Tag handling when tagged courses or libraries are deleted or restored
    """

    MODULESTORE = TEST_DATA_SPLIT_MODULESTORE

    def _check_tag(self, object_key: ContentKey | str, taxonomy_id: int, value: str | None) -> bool:
        """
        Check if the ObjectTag exists for the given object_id and taxonomy_id

        If value is None, check if the ObjectTag does not exists
        """
        object_tags = list(api.get_object_tags(str(object_key), taxonomy_id=taxonomy_id))
        object_tag = object_tags[0] if len(object_tags) == 1 else None
        if len(object_tags) > 1:
            raise ValueError("Found too many object tags")
        if value is None:
            assert not object_tag, f"Expected no tag for taxonomy_id={taxonomy_id}, " \
                f"but one found with value={object_tag.value}"
        else:
            assert object_tag, f"Tag for taxonomy_id={taxonomy_id} with value={value} with expected, but none found"
            assert object_tag.value == value, f"Tag value mismatch {object_tag.value} != {value}"

        return True

    def setUp(self) -> None:
        super().setUp()
        # Create user
        self.user = UserFactory.create()
        self.user_id = self.user.id

        self.orgA = Organization.objects.create(name="Organization A", short_name="orgA")

        # Create a taxonomy and tag
        self.taxonomy = api.create_taxonomy("Test Taxonomy")
        self.tag = api.add_tag_to_taxonomy(self.taxonomy, "new tag for testing")
        api.set_taxonomy_orgs(self.taxonomy, all_orgs=True)

    def test_create_delete_xblock(self) -> None:
        # Create course
        course = self.store.create_course(
            self.orgA.short_name,
            "test_course",
            "test_run",
            self.user_id,
        )

        # Create XBlocks
        sequential = self.store.create_child(self.user_id, course.location, "sequential", "test_sequential")
        vertical = self.store.create_child(self.user_id, sequential.location, "vertical", "test_vertical")

        usage_key_str = str(vertical.location)

        # Apply a tag to the XBlock
        api.tag_object(usage_key_str, self.taxonomy, [self.tag.value])
        assert self._check_tag(usage_key_str, self.taxonomy.id, self.tag.value)

        # Delete the XBlock
        self.store.delete_item(vertical.location, self.user_id)

        # Check if the tags are deleted
        assert self._check_tag(usage_key_str, self.taxonomy.id, None)

    def test_create_delete_restore_library_block(self) -> None:
        # Create library
        library = create_library(
            org=self.orgA,
            slug="lib_a",
            title="Library Org A",
            description="This is a library from Org A",
        )

        library_block = create_library_block(library.key, "problem", "Problem1")
        usage_key_str = str(library_block.usage_key)

        # Apply a tag to the XBlock
        api.tag_object(usage_key_str, self.taxonomy, [self.tag.value])
        assert self._check_tag(usage_key_str, self.taxonomy.id, self.tag.value)

        # Soft delete the XBlock
        delete_library_block(library_block.usage_key)

        # Check that the tags are not deleted
        assert self._check_tag(usage_key_str, self.taxonomy.id, self.tag.value)

        # Restore the XBlock
        restore_library_block(library_block.usage_key)

        # Check if the tags are still present for the Library Block
        assert self._check_tag(usage_key_str, self.taxonomy.id, self.tag.value)
