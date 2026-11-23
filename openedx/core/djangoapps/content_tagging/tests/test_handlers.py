"""
Test for content tagging handlers
"""
from __future__ import annotations

from unittest.mock import patch

from ccx_keys.locator import CCXLocator
from django.db.models import ProtectedError
from django.test import LiveServerTestCase
from openedx_tagging.models import ObjectTag
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

from .. import api, handlers
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
    ENABLED_SIGNALS = ['pre_item_delete', 'course_deleted']

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

    def _create_course_with_section_tree(self) -> dict:
        """
        Create a course with section > subsection > unit > component, plus a second section
        holding a unit, and tag every block with the test tag. Returns the blocks by name.
        """
        course = self.store.create_course(self.orgA.short_name, "test_course", "test_run", self.user_id)
        blocks = {"course": course}
        blocks["section"] = self.store.create_child(self.user_id, course.location, "chapter", "section")
        blocks["subsection"] = self.store.create_child(
            self.user_id, blocks["section"].location, "sequential", "subsection"
        )
        blocks["unit"] = self.store.create_child(self.user_id, blocks["subsection"].location, "vertical", "unit")
        blocks["component"] = self.store.create_child(self.user_id, blocks["unit"].location, "html", "component")
        blocks["outside_section"] = self.store.create_child(self.user_id, course.location, "chapter", "outside")
        blocks["outside_unit"] = self.store.create_child(
            self.user_id, blocks["outside_section"].location, "vertical", "outside_unit"
        )
        for name, block in blocks.items():
            if name != "course":
                api.tag_object(str(block.location), self.taxonomy, [self.tag.value])
        return blocks

    def test_delete_section_deletes_descendant_tags(self) -> None:
        blocks = self._create_course_with_section_tree()

        self.store.delete_item(blocks["section"].location, self.user_id)

        for name in ("section", "subsection", "unit", "component"):
            assert self._check_tag(blocks[name].location, self.taxonomy.id, None), name
        for name in ("outside_section", "outside_unit"):
            assert self._check_tag(blocks[name].location, self.taxonomy.id, self.tag.value), name

    def test_delete_unit_deletes_component_tags(self) -> None:
        blocks = self._create_course_with_section_tree()

        self.store.delete_item(blocks["unit"].location, self.user_id)

        for name in ("unit", "component"):
            assert self._check_tag(blocks[name].location, self.taxonomy.id, None), name
        for name in ("section", "subsection", "outside_section", "outside_unit"):
            assert self._check_tag(blocks[name].location, self.taxonomy.id, self.tag.value), name

    def test_delete_section_reaches_dynamic_children(self) -> None:
        blocks = self._create_course_with_section_tree()
        split_test = self.store.create_child(self.user_id, blocks["unit"].location, "split_test", "split_test")
        split_child = self.store.create_child(self.user_id, split_test.location, "html", "split_child")
        library_content = self.store.create_child(
            self.user_id, blocks["unit"].location, "library_content", "library_content"
        )
        library_child = self.store.create_child(self.user_id, library_content.location, "html", "library_child")
        dynamic_children = [split_child, library_child]
        for block in dynamic_children:
            api.tag_object(str(block.location), self.taxonomy, [self.tag.value])

        self.store.delete_item(blocks["section"].location, self.user_id)

        for block in dynamic_children:
            assert self._check_tag(block.location, self.taxonomy.id, None), block.location

    def test_descendant_tag_cleanup_failure_is_logged_as_a_warning(self) -> None:
        """
        An unexpected error while deleting descendant tags is logged as a warning with its traceback,
        and the receiver returns normally.
        """
        blocks = self._create_course_with_section_tree()

        with patch.object(handlers, "_delete_tags", side_effect=RuntimeError("boom")):
            with self.assertLogs(handlers.log, level="WARNING") as logs:
                handlers.delete_descendant_tags(usage_key=blocks["section"].location, user_id=self.user_id)

        assert any("Unable to delete descendant tags" in line and "boom" in line for line in logs.output)

    def test_descendant_tag_cleanup_skips_a_protected_object_and_continues(self) -> None:
        blocks = self._create_course_with_section_tree()
        real_delete_object_tags = api.delete_object_tags
        calls = []

        def delete_object_tags(object_id):
            # The receiver fires once per branch, so the same object is attempted again.
            if not calls:
                calls.append(object_id)
            if object_id == calls[0]:
                raise ProtectedError("protected", set())
            real_delete_object_tags(object_id)

        with patch.object(api, "delete_object_tags", side_effect=delete_object_tags):
            with self.assertLogs(handlers.log, level="WARNING") as logs:
                self.store.delete_item(blocks["section"].location, self.user_id)

        protected_key = calls[0]
        assert any("other data protects them" in line and protected_key in line for line in logs.output)
        for name in ("subsection", "unit", "component"):
            location = blocks[name].location
            expected = self.tag.value if str(location) == protected_key else None
            assert self._check_tag(location, self.taxonomy.id, expected), name

    def test_descendant_tag_cleanup_of_an_already_deleted_block_logs_a_debug_line_and_returns(self) -> None:
        """
        The signal fires once per branch. When the block is already gone, the receiver logs a debug line,
        reports no warning or error, and leaves the tags of other blocks alone.
        """
        blocks = self._create_course_with_section_tree()
        self.store.delete_item(blocks["section"].location, self.user_id)

        with (
            patch.object(handlers, "_delete_tags") as delete_tags,
            patch.object(handlers.log, "debug") as log_debug,
            patch.object(handlers.log, "error") as log_error,
            patch.object(handlers.log, "warning") as log_warning,
        ):
            handlers.delete_descendant_tags(
                usage_key=blocks["section"].location, user_id=self.user_id
            )

        delete_tags.assert_not_called()
        log_debug.assert_called_once()
        log_error.assert_not_called()
        log_warning.assert_not_called()

    def _create_tagged_course(self, run: str) -> dict:
        """
        Create a course in the ``orgA`` org with two blocks, and tag the course key itself
        and every block. Returns the course and the blocks by name.
        """
        course = self.store.create_course(self.orgA.short_name, "test_course", run, self.user_id)
        section = self.store.create_child(self.user_id, course.location, "chapter", "section")
        unit = self.store.create_child(self.user_id, section.location, "vertical", "unit")
        blocks = {"course": course, "section": section, "unit": unit}
        api.tag_object(str(course.id), self.taxonomy, [self.tag.value])
        for block in blocks.values():
            api.tag_object(str(block.location), self.taxonomy, [self.tag.value])
        return blocks

    def test_delete_course_deletes_all_its_tags(self) -> None:
        blocks = self._create_tagged_course("test_run")
        object_ids = {name: str(block.location) for name, block in blocks.items()}
        object_ids["course key"] = str(blocks["course"].id)

        self.store.delete_course(blocks["course"].id, self.user_id)

        for name, object_id in object_ids.items():
            assert self._check_tag(object_id, self.taxonomy.id, None), name

    def test_delete_course_keeps_sibling_course_tags(self) -> None:
        deleted = self._create_tagged_course("test_run")
        # The sibling's run starts with the deleted course's run, so a prefix that
        # lacks the "+type@" delimiter would match both courses.
        sibling = self._create_tagged_course("test_run2")

        self.store.delete_course(deleted["course"].id, self.user_id)

        for name, block in deleted.items():
            assert self._check_tag(block.location, self.taxonomy.id, None), name
        assert self._check_tag(deleted["course"].id, self.taxonomy.id, None)
        for name, block in sibling.items():
            assert self._check_tag(block.location, self.taxonomy.id, self.tag.value), name
        assert self._check_tag(sibling["course"].id, self.taxonomy.id, self.tag.value)

    def test_delete_course_of_a_non_course_v1_key_covers_only_the_exact_key(self) -> None:
        ccx_key = CCXLocator.from_string("ccx-v1:orgA+test_course+test_run+ccx@1")
        exact = ObjectTag.objects.create(object_id=str(ccx_key), taxonomy=None, tag=None, _value="a", _export_id="9-x")
        # A prefix built from the key string would match this id.
        prefixed = ObjectTag.objects.create(
            object_id=f"{ccx_key}+type@sequential+block@s", taxonomy=None, tag=None, _value="b", _export_id="9-x",
        )

        handlers.delete_course_tags(sender=None, course_key=ccx_key)

        assert not ObjectTag.objects.filter(pk=exact.pk).exists()
        assert ObjectTag.objects.filter(pk=prefixed.pk).exists()

    def test_course_tag_cleanup_failure_is_logged_as_a_warning(self) -> None:
        """
        An unexpected error while deleting a course's tags is logged as a warning with its traceback,
        and the receiver returns normally.
        """
        blocks = self._create_tagged_course("test_run")

        with patch.object(api, "delete_object_tags", side_effect=RuntimeError("boom")):
            with self.assertLogs(handlers.log, level="WARNING") as logs:
                handlers.delete_course_tags(sender=None, course_key=blocks["course"].id)

        assert any("Unable to delete tags of deleted course" in line and "boom" in line for line in logs.output)

    def test_course_tag_cleanup_skips_a_protected_object_and_continues(self) -> None:
        blocks = self._create_tagged_course("test_run")
        object_ids = {str(block.location) for block in blocks.values()} | {str(blocks["course"].id)}
        protected_id = sorted(object_ids)[0]
        real_delete_object_tags = api.delete_object_tags

        def delete_object_tags(object_id):
            if object_id == protected_id:
                raise ProtectedError("protected", set())
            real_delete_object_tags(object_id)

        with patch.object(api, "delete_object_tags", side_effect=delete_object_tags):
            with self.assertLogs(handlers.log, level="WARNING") as logs:
                handlers.delete_course_tags(sender=None, course_key=blocks["course"].id)

        assert any("other data protects them" in line and protected_id in line for line in logs.output)
        for object_id in object_ids:
            expected = self.tag.value if object_id == protected_id else None
            assert self._check_tag(object_id, self.taxonomy.id, expected), object_id

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
