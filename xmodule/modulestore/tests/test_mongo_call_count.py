"""
Tests to verify correct number of MongoDB calls during course import/export and traversal
when using the Split modulestore.
"""


from shutil import rmtree
from tempfile import mkdtemp
from unittest import skip

import ddt
from django.test import TestCase  # lint-amnesty, pylint: disable=reimported

from xmodule.modulestore import ModuleStoreEnum
from xmodule.modulestore.tests.factories import check_mongo_calls
from xmodule.modulestore.tests.utils import (
    TEST_DATA_DIR,
    MemoryCache,
    MixedModulestoreBuilder,
    VersioningModulestoreBuilder,
)
from xmodule.modulestore.xml_exporter import export_course_to_xml
from xmodule.modulestore.xml_importer import import_course_from_xml

MIXED_SPLIT_MODULESTORE_BUILDER = MixedModulestoreBuilder([('split', VersioningModulestoreBuilder())])


@ddt.ddt
@skip("Fix call counts below - sometimes the counts are off by 1.")
class CountMongoCallsXMLRoundtrip(TestCase):
    """
    This class exists to test XML import and export to/from Split.
    """

    def setUp(self):
        super().setUp()
        self.export_dir = mkdtemp()
        self.addCleanup(rmtree, self.export_dir, ignore_errors=True)

    @ddt.data(
        (MIXED_SPLIT_MODULESTORE_BUILDER, 37, 16, 190, 189),
    )
    @ddt.unpack
    def test_import_export(self, store_builder, export_reads, import_reads, first_import_writes, second_import_writes):
        with store_builder.build() as (source_content, source_store):
            with store_builder.build() as (dest_content, dest_store):
                source_course_key = source_store.make_course_key('a', 'course', 'course')
                dest_course_key = dest_store.make_course_key('a', 'course', 'course')

                # An extra import write occurs in the first Split import due to the mismatch between
                # the course id and the wiki_slug in the test XML course. The course must be updated
                # with the correct wiki_slug during import.
                with check_mongo_calls(import_reads, first_import_writes):
                    import_course_from_xml(
                        source_store,
                        ModuleStoreEnum.UserID.test,
                        TEST_DATA_DIR,
                        source_dirs=['manual-testing-complete'],
                        static_content_store=source_content,
                        target_id=source_course_key,
                        create_if_not_present=True,
                        raise_on_failure=True,
                    )

                with check_mongo_calls(export_reads):
                    export_course_to_xml(
                        source_store,
                        source_content,
                        source_course_key,
                        self.export_dir,
                        'exported_source_course',
                    )

                with check_mongo_calls(import_reads, second_import_writes):
                    import_course_from_xml(
                        dest_store,
                        ModuleStoreEnum.UserID.test,
                        self.export_dir,
                        source_dirs=['exported_source_course'],
                        static_content_store=dest_content,
                        target_id=dest_course_key,
                        create_if_not_present=True,
                        raise_on_failure=True,
                    )


@ddt.ddt
class CountMongoCallsCourseTraversal(TestCase):
    """
    Tests the number of Mongo calls made when traversing a course tree from the top course root
    to the leaf nodes.
    """

    def _traverse_blocks_in_course(self, course, access_all_block_fields):
        """
        Traverses all the blocks in the given course.
        If access_all_block_fields is True, also reads all the
        xblock fields in each block in the course.
        """
        all_blocks = []
        stack = [course]
        while stack:
            curr_block = stack.pop()
            all_blocks.append(curr_block)
            if curr_block.has_children:
                for block in reversed(curr_block.get_children()):
                    stack.append(block)

        if access_all_block_fields:
            # Read the fields on each block in order to ensure each block and its definition is loaded.
            for xblock in all_blocks:
                for __, field in xblock.fields.items():
                    if field.is_set_on(xblock):
                        __ = field.read_from(xblock)

    def _import_course(self, content_store, modulestore):
        """
        Imports a course for testing.
        Returns the course key.
        """
        course_key = modulestore.make_course_key('a', 'course', 'course')
        import_course_from_xml(
            modulestore,
            ModuleStoreEnum.UserID.test,
            TEST_DATA_DIR,
            source_dirs=['manual-testing-complete'],
            static_content_store=content_store,
            target_id=course_key,
            create_if_not_present=True,
            raise_on_failure=True,
        )
        return course_key

    # Suppose you want to traverse a course - maybe accessing the fields of each XBlock in the course,
    # maybe not. What parameters should one use for get_course() in order to minimize the number of
    # mongo calls? The tests below both ensure that code changes don't increase the number of mongo calls
    # during traversal -and- demonstrate how to minimize the number of calls.
    @ddt.data(
        # These two lines show the way this traversal *should* be done
        # (if you'll eventually access all the fields and load all the definitions anyway).
        (MIXED_SPLIT_MODULESTORE_BUILDER, None, False, True, 2),
        (MIXED_SPLIT_MODULESTORE_BUILDER, None, True, True, 37),
        (MIXED_SPLIT_MODULESTORE_BUILDER, 0, False, True, 37),
        (MIXED_SPLIT_MODULESTORE_BUILDER, 0, True, True, 37),
        (MIXED_SPLIT_MODULESTORE_BUILDER, None, False, False, 2),
        (MIXED_SPLIT_MODULESTORE_BUILDER, None, True, False, 2),
        (MIXED_SPLIT_MODULESTORE_BUILDER, 0, False, False, 2),
        (MIXED_SPLIT_MODULESTORE_BUILDER, 0, True, False, 2),
    )
    @ddt.unpack
    def test_number_mongo_calls(self, store_builder, depth, lazy, access_all_block_fields, num_mongo_calls):
        request_cache = MemoryCache()
        with store_builder.build(request_cache=request_cache) as (content_store, modulestore):
            course_key = self._import_course(content_store, modulestore)

            # Starting at the root course block, do a breadth-first traversal using
            # get_children() to retrieve each block's children.
            with check_mongo_calls(num_mongo_calls):
                with modulestore.bulk_operations(course_key):
                    start_block = modulestore.get_course(course_key, depth=depth, lazy=lazy)
                    self._traverse_blocks_in_course(start_block, access_all_block_fields)

    @ddt.data(
        (MIXED_SPLIT_MODULESTORE_BUILDER, 3),
    )
    @ddt.unpack
    def test_lazy_when_course_previously_cached(self, store_builder, num_mongo_calls):
        request_cache = MemoryCache()
        with store_builder.build(request_cache=request_cache) as (content_store, modulestore):
            course_key = self._import_course(content_store, modulestore)

            with check_mongo_calls(num_mongo_calls):
                with modulestore.bulk_operations(course_key):
                    # assume the course was retrieved earlier
                    course = modulestore.get_course(course_key, depth=0, lazy=True)

                    # and then subsequently retrieved with the lazy and depth=None values
                    course = modulestore.get_item(course.location, depth=None, lazy=False)
                    self._traverse_blocks_in_course(course, access_all_block_fields=True)
