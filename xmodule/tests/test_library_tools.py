"""
Tests for legacy library tools service (only used by CMS)

The only known user of the LegacyLibraryToolsService is the
LegacyLibraryContentBlock, so these tests are all written with only that
block type in mind.
"""

from unittest import mock

import ddt
from opaque_keys.edx.locator import LibraryLocator

from common.djangoapps.student.tests.factories import UserFactory
from openedx.core.djangoapps.content_libraries.tests.base import ContentLibrariesRestApiTest
from openedx.core.djangolib.testing.utils import skip_unless_cms
from xmodule.library_tools import LegacyLibraryToolsService
from xmodule.modulestore.tests.factories import CourseFactory, LibraryFactory
from xmodule.modulestore.tests.utils import MixedSplitTestCase


@skip_unless_cms
@ddt.ddt
class ContentLibraryToolsTest(MixedSplitTestCase, ContentLibrariesRestApiTest):
    """
    Tests for LegacyLibraryToolsService.
    """
    def setUp(self):
        super().setUp()
        UserFactory(is_staff=True, id=self.user_id)
        self.tools = LegacyLibraryToolsService(self.store, self.user_id)

    def test_list_available_libraries(self):
        """
        Test listing of v1 libraries.
        """
        # create V1 library
        _ = LibraryFactory.create(modulestore=self.store)
        # create V2 library (should not be included in this list)
        self._create_library(slug="testlib1_preview", title="Test Library 1", description="Testing XBlocks")
        all_libraries = self.tools.list_available_libraries()
        assert len(all_libraries) == 1

    @mock.patch('xmodule.modulestore.split_mongo.split.SplitMongoModuleStore.get_library_summaries')
    def test_list_available_libraries_fetch(self, mock_get_library_summaries):
        """
        Test that library list is compiled using light weight library summary objects.
        """
        _ = self.tools.list_available_libraries()
        assert mock_get_library_summaries.called

    def test_get_latest_library_version(self):
        """
        Test get_v1_library_version for V1 libraries.

        Covers getting results for either string library key or LibraryLocator.
        """
        lib_key = LibraryFactory.create(modulestore=self.store).location.library_key
        # Re-load the library from the modulestore, explicitly including version information:
        lib = self.store.get_library(lib_key, remove_version=False, remove_branch=False)
        # check the result using the LibraryLocator
        assert isinstance(lib_key, LibraryLocator)
        result = self.tools.get_latest_library_version(lib_key)
        assert result
        assert result == str(lib.location.library_key.version_guid)
        # the same check for string representation of the LibraryLocator
        str_key = str(lib_key)
        result = self.tools.get_latest_library_version(str_key)
        assert result
        assert result == str(lib.location.library_key.version_guid)

    @ddt.data(
        'library-v1:Fake+Key',
        LibraryLocator.from_string('library-v1:Fake+Key'),
    )
    def test_get_latest_library_version_no_library(self, lib_key):
        """
        Test get_latest_library_version result when the library does not exist.
        """
        assert self.tools.get_latest_library_version(lib_key) is None

    def test_update_children(self):
        """
        Test update_children with V1 library as a source.

        As for now, covers usage of update_children for the library content module only.
        """
        library = LibraryFactory.create(modulestore=self.store)
        self.make_block("html", library, data="Hello world from the block")
        course = CourseFactory.create(modulestore=self.store)
        content_block = self.make_block(
            "library_content",
            course,
            max_count=1,
            source_library_id=str(library.location.library_key)
        )

        assert len(content_block.children) == 0
        self.tools.trigger_library_sync(content_block, library_version=None)
        content_block = self.store.get_item(content_block.location)
        assert len(content_block.children) == 1

    def _make_library_content_with_child(self):
        """Build a library + course + populated library_content block."""
        library = LibraryFactory.create(modulestore=self.store)
        self.make_block("html", library, data="Hello from the library")
        course = CourseFactory.create(modulestore=self.store)
        source_lc = self.make_block(
            "library_content",
            course,
            max_count=1,
            source_library_id=str(library.location.library_key),
        )
        self.tools.trigger_library_sync(source_lc, library_version=None)
        return course, library, self.store.get_item(source_lc.location)

    def test_unit_trigger_duplication_does_not_enqueue_async_task(self):
        """
        Unit test for bug #36544: trigger_duplication must invoke
        duplicate_children.apply (synchronous), never .delay (async).
        """
        course, library, source_lc = self._make_library_content_with_child()
        dest_lc = self.make_block(
            "library_content",
            course,
            max_count=1,
            source_library_id=str(library.location.library_key),
            source_library_version=source_lc.source_library_version,
        )
        with mock.patch(
            "openedx.core.djangoapps.content_libraries.tasks.duplicate_children.delay"
        ) as mocked_delay, mock.patch(
            "openedx.core.djangoapps.content_libraries.tasks.duplicate_children.apply"
        ) as mocked_apply:
            self.tools.trigger_duplication(source_block=source_lc, dest_block=dest_lc)
        assert not mocked_delay.called, "must not enqueue an async task; see #36544"
        assert mocked_apply.called

    def test_integration_trigger_duplication_inside_bulk_operations(self):
        """
        Integration test for bug #36544: trigger_duplication must run in-process
        so its writes land inside the enclosing store.bulk_operations() context,
        producing a duplicate block whose children are visible immediately.
        """
        course, library, source_lc = self._make_library_content_with_child()
        assert len(source_lc.children) == 1

        with self.store.bulk_operations(course.id):
            dest_lc = self.make_block(
                "library_content",
                course,
                max_count=1,
                source_library_id=str(library.location.library_key),
                source_library_version=source_lc.source_library_version,
            )
            self.tools.trigger_duplication(source_block=source_lc, dest_block=dest_lc)
            dest_lc_reloaded = self.store.get_item(dest_lc.location)
            assert len(dest_lc_reloaded.children) == 1

    def test_bug_36544_regression_cross_context_guard(self):
        """
        Regression for bug #36544: the cross-learning-context guard was a
        self-comparison (always False) and never fired. Verify that passing
        source and dest in different courses now raises ValueError.
        """
        course_a = CourseFactory.create(modulestore=self.store)
        course_b = CourseFactory.create(modulestore=self.store)
        library = LibraryFactory.create(modulestore=self.store)
        source_lc = self.make_block(
            "library_content", course_a,
            source_library_id=str(library.location.library_key),
        )
        dest_lc = self.make_block(
            "library_content", course_b,
            source_library_id=str(library.location.library_key),
        )
        with self.assertRaisesRegex(ValueError, "different learning contexts"):
            self.tools.trigger_duplication(source_block=source_lc, dest_block=dest_lc)
