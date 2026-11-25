"""
Tests for the split modulestore's request-scoped structure cache.
"""
import copy
from unittest.mock import patch

from edx_django_utils.cache import RequestCache

from xmodule.modulestore import ModuleStoreEnum
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase
from xmodule.modulestore.tests.factories import BlockFactory, CourseFactory


class TestStructureRequestCache(ModuleStoreTestCase):
    """
    Structures read outside of bulk operations are loaded once per request.
    """

    def setUp(self):
        super().setUp()
        self.course = CourseFactory.create()
        self.chapter = BlockFactory.create(parent=self.course, category="chapter", display_name="Week 1")
        self.split = self.store._get_modulestore_by_type(ModuleStoreEnum.Type.split)  # pylint: disable=protected-access
        # Start each test as if it were a new request.
        RequestCache.clear_all_namespaces()

    def count_structure_loads(self):
        """
        Patch the persistence layer so that tests can count structure loads.
        """
        connection = self.split.db_connection
        return patch.object(connection, "get_structure", wraps=connection.get_structure)

    @staticmethod
    def storable_blocks(structure):
        """
        A deep, comparable snapshot of a structure's blocks.
        """
        return copy.deepcopy({key: block.to_storable() for key, block in structure["blocks"].items()})

    def test_reads_share_one_load(self):
        with self.count_structure_loads() as get_structure:
            self.store.get_course(self.course.id)
            self.store.get_course(self.course.id)
            self.store.has_item(self.chapter.location)
            self.store.get_parent_location(self.chapter.location)

        assert get_structure.call_count == 1

    def test_bulk_operations_keep_their_own_copy(self):
        """
        Bulk operations are where structures get modified, so they don't share the cached one.
        """
        with self.count_structure_loads() as get_structure:
            self.store.get_course(self.course.id)
            with self.store.bulk_operations(self.course.id):
                self.store.get_course(self.course.id)

        assert get_structure.call_count == 2

    def test_new_request_loads_again(self):
        with self.count_structure_loads() as get_structure:
            self.store.get_course(self.course.id)
            RequestCache.clear_all_namespaces()
            self.store.get_course(self.course.id)

        assert get_structure.call_count == 2

    def test_no_request_cache(self):
        with self.count_structure_loads() as get_structure, patch.object(self.split, "request_cache", None):
            self.store.get_course(self.course.id)
            self.store.get_course(self.course.id)

        assert get_structure.call_count == 2

    def test_changes_are_visible_to_later_reads(self):
        chapter = self.store.get_item(self.chapter.location)
        chapter.display_name = "Week One"
        self.store.update_item(chapter, self.user.id)

        assert self.store.get_item(self.chapter.location).display_name == "Week One"

    def test_clone_leaves_cached_source_structure_alone(self):
        """
        A clone starts out pointing at the source course's draft structure. Editing the clone
        in the same bulk operation must not change the source structure that other readers in
        the request already hold.
        """
        sequential = BlockFactory.create(parent=self.chapter, category="sequential")
        vertical = BlockFactory.create(parent=sequential, category="vertical", display_name="Unit 1")
        draft_key = self.course.id.for_branch(ModuleStoreEnum.BranchName.draft)
        source_structure = self.split._lookup_course(draft_key).structure  # pylint: disable=protected-access
        blocks_before = self.storable_blocks(source_structure)

        dest_course_id = self.store.make_course_key("org.clone", "course.clone", "run.clone")
        dest_vertical_key = dest_course_id.make_usage_key("vertical", vertical.location.block_id)
        with self.store.bulk_operations(dest_course_id):
            self.store.clone_course(self.course.id, dest_course_id, self.user.id)
            with self.store.branch_setting(ModuleStoreEnum.Branch.draft_preferred, dest_course_id):
                dest_vertical = self.store.get_item(dest_vertical_key)
                dest_vertical.display_name = "Cloned unit"
                self.store.update_item(dest_vertical, self.user.id)

        assert self.storable_blocks(source_structure) == blocks_before
