"""
Tests of modulestore semantics: How do the interfaces methods of ModuleStore relate to each other?
"""


import itertools
from collections import namedtuple
from unittest.mock import patch

import pytest
import ddt
from xblock.core import XBlock, XBlockAside
from xblock.fields import Scope, String
from xblock.runtime import DictKeyValueStore, KvsFieldData
from xblock.test.tools import TestRuntime

from xmodule.course_block import CourseSummary
from xmodule.modulestore import ModuleStoreEnum
from xmodule.modulestore.draft_and_published import DIRECT_ONLY_CATEGORIES
from xmodule.modulestore.exceptions import ItemNotFoundError
from xmodule.modulestore.tests.factories import CourseFactory
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase

DETACHED_BLOCK_TYPES = dict(XBlock.load_tagged_classes('detached'))

# These tests won't work with courses, since they're creating blocks inside courses
TESTABLE_BLOCK_TYPES = set(DIRECT_ONLY_CATEGORIES)
TESTABLE_BLOCK_TYPES.discard('course')
TESTABLE_BLOCK_TYPES = list(TESTABLE_BLOCK_TYPES)
TESTABLE_BLOCK_TYPES.sort()

TestField = namedtuple('TestField', ['field_name', 'initial', 'updated'])


class AsideTest(XBlockAside):
    """
    Test xblock aside class
    """
    content = String(default="content", scope=Scope.content)


@ddt.ddt
class DirectOnlyCategorySemantics(ModuleStoreTestCase):
    """
    Verify the behavior of Direct Only items
    blocks intended to store snippets of course content.
    """

    __test__ = False

    DATA_FIELDS = {
        'about': TestField('data', '<div>test data</div>', '<div>different test data</div>'),
        'chapter': TestField('is_entrance_exam', True, False),
        'sequential': TestField('is_entrance_exam', True, False),
        'static_tab': TestField('data', '<div>test data</div>', '<div>different test data</div>'),
        'course_info': TestField('data', '<div>test data</div>', '<div>different test data</div>'),
    }

    ASIDE_DATA_FIELD = TestField('content', '<div>aside test data</div>', '<div>aside different test data</div>')

    def setUp(self):
        super().setUp()
        self.course = CourseFactory.create(
            org='test_org',
            number='999',
            run='test_run',
            display_name='My Test Course',
            modulestore=self.store
        )

    def assertBlockDoesntExist(self, block_usage_key, draft=None):
        """
        Verify that loading ``block_usage_key`` raises an ItemNotFoundError.

        Arguments:
            block_usage_key: The xblock to check.
            draft (optional): If omitted, verify both published and draft branches.
                If True, verify only the draft branch. If False, verify only the
                published branch.
        """
        if draft is None or draft:
            with self.store.branch_setting(ModuleStoreEnum.Branch.draft_preferred):
                with pytest.raises(ItemNotFoundError):
                    self.store.get_item(block_usage_key)

        if draft is None or not draft:
            with self.store.branch_setting(ModuleStoreEnum.Branch.published_only):
                with pytest.raises(ItemNotFoundError):
                    self.store.get_item(block_usage_key)

    def assertBlockHasContent(self, block_usage_key, field_name, content,
                              aside_field_name=None, aside_content=None, draft=None):
        """
        Assert that the block ``block_usage_key`` has the value ``content`` for ``field_name``
        when it is loaded.

        Arguments:
            block_usage_key: The xblock to check.
            field_name (string): The name of the field to check.
            content: The value to assert is in the field.
            aside_field_name (string): The name of the field to check (in connected xblock aside)
            aside_content: The value to assert is in the xblock aside field.
            draft (optional): If omitted, verify both published and draft branches.
                If True, verify only the draft branch. If False, verify only the
                published branch.
        """
        if draft is None or not draft:
            with self.store.branch_setting(ModuleStoreEnum.Branch.published_only):
                target_block = self.store.get_item(
                    block_usage_key,
                )
                assert content == target_block.fields[field_name].read_from(target_block)
                if aside_field_name and aside_content:
                    aside = self._get_aside(target_block)
                    assert aside is not None
                    assert aside_content == aside.fields[aside_field_name].read_from(aside)

        if draft is None or draft:
            with self.store.branch_setting(ModuleStoreEnum.Branch.draft_preferred):
                target_block = self.store.get_item(
                    block_usage_key,
                )
                assert content == target_block.fields[field_name].read_from(target_block)
                if aside_field_name and aside_content:
                    aside = self._get_aside(target_block)
                    assert aside is not None
                    assert aside_content == aside.fields[aside_field_name].read_from(aside)

    def assertParentOf(self, parent_usage_key, child_usage_key, draft=None):
        """
        Assert that the block ``parent_usage_key`` has ``child_usage_key`` listed
        as one of its ``.children``.

        Arguments:
            parent_usage_key: The xblock to check as a parent.
            child_usage_key: The xblock to check as a child.
            draft (optional): If omitted, verify both published and draft branches.
                If True, verify only the draft branch. If False, verify only the
                published branch.
        """
        if draft is None or not draft:
            with self.store.branch_setting(ModuleStoreEnum.Branch.published_only):
                parent_block = self.store.get_item(
                    parent_usage_key,
                )
                assert child_usage_key in parent_block.children

        if draft is None or draft:
            with self.store.branch_setting(ModuleStoreEnum.Branch.draft_preferred):
                parent_block = self.store.get_item(
                    parent_usage_key,
                )
                assert child_usage_key in parent_block.children

    def assertNotParentOf(self, parent_usage_key, child_usage_key, draft=None):
        """
        Assert that the block ``parent_usage_key`` does not have ``child_usage_key`` listed
        as one of its ``.children``.

        Arguments:
            parent_usage_key: The xblock to check as a parent.
            child_usage_key: The xblock to check as a child.
            draft (optional): If omitted, verify both published and draft branches.
                If True, verify only the draft branch. If False, verify only the
                published branch.
        """
        if draft is None or not draft:
            with self.store.branch_setting(ModuleStoreEnum.Branch.published_only):
                parent_block = self.store.get_item(
                    parent_usage_key,
                )
                assert child_usage_key not in parent_block.children

        if draft is None or draft:
            with self.store.branch_setting(ModuleStoreEnum.Branch.draft_preferred):
                parent_block = self.store.get_item(
                    parent_usage_key,
                )
                assert child_usage_key not in parent_block.children

    def assertCoursePointsToBlock(self, block_usage_key, draft=None):
        """
        Assert that the context course for the test has ``block_usage_key`` listed
        as one of its ``.children``.

        Arguments:
            block_usage_key: The xblock to check.
            draft (optional): If omitted, verify both published and draft branches.
                If True, verify only the draft branch. If False, verify only the
                published branch.
        """
        self.assertParentOf(self.course.scope_ids.usage_id, block_usage_key, draft=draft)

    def assertCourseDoesntPointToBlock(self, block_usage_key, draft=None):
        """
        Assert that the context course for the test does not have ``block_usage_key`` listed
        as one of its ``.children``.

        Arguments:
            block_usage_key: The xblock to check.
            draft (optional): If omitted, verify both published and draft branches.
                If True, verify only the draft branch. If False, verify only the
                published branch.
        """
        self.assertNotParentOf(self.course.scope_ids.usage_id, block_usage_key, draft=draft)

    def assertCourseSummaryFields(self, course_summaries):
        """
        Assert that the `course_summary` of a course has all expected fields.

        Arguments:
            course_summaries: list of CourseSummary class objects.
        """
        def verify_course_summery_fields(course_summary):
            """ Verify that every `course_summary` object has all the required fields """
            expected_fields = CourseSummary.course_info_fields + ['id', 'location', 'has_ended']
            return all(hasattr(course_summary, field) for field in expected_fields)

        assert all(verify_course_summery_fields(course_summary) for course_summary in course_summaries)

    def is_detached(self, block_type):
        """
        Return True if ``block_type`` is a detached block.
        """
        return block_type in DETACHED_BLOCK_TYPES

    @ddt.data(*TESTABLE_BLOCK_TYPES)
    def test_create(self, block_type):
        self._do_create(block_type)

    def _prepare_asides(self, scope_ids):
        """
        Return list with connected aside xblocks
        """
        key_store = DictKeyValueStore()
        field_data = KvsFieldData(key_store)

        aside = AsideTest(scope_ids=scope_ids, runtime=TestRuntime(services={'field-data': field_data}))
        aside.fields[self.ASIDE_DATA_FIELD.field_name].write_to(aside, self.ASIDE_DATA_FIELD.initial)  # pylint: disable=unsubscriptable-object
        return [aside]

    def _get_aside(self, block):
        """
        Return connected xblock aside
        """
        for aside in block.runtime.get_asides(block):
            if isinstance(aside, AsideTest):
                return aside
        return None

    # This function is split out from the test_create method so that it can be called
    # by other tests
    def _do_create(self, block_type, with_asides=False):
        """
        Create a block of block_type (which should be a DIRECT_ONLY_CATEGORY),
        and then verify that it was created successfully, and is visible in
        both published and draft branches.
        """
        block_usage_key = self.course.id.make_usage_key(block_type, 'test_block')

        self.assertBlockDoesntExist(block_usage_key)
        self.assertCourseDoesntPointToBlock(block_usage_key)

        test_data = self.DATA_FIELDS[block_type]

        initial_field_value = test_data.initial

        with self.store.branch_setting(ModuleStoreEnum.Branch.draft_preferred):
            if self.is_detached(block_type):
                block = self.store.create_xblock(
                    self.course.runtime,
                    self.course.id,
                    block_usage_key.block_type,
                    block_id=block_usage_key.block_id
                )
                block.fields[test_data.field_name].write_to(block, initial_field_value)
                asides = []
                if with_asides:
                    asides = self._prepare_asides(self.course.scope_ids.usage_id)
                self.store.update_item(block, ModuleStoreEnum.UserID.test, asides=asides, allow_not_found=True)
            else:
                asides = []
                if with_asides:
                    asides = self._prepare_asides(self.course.scope_ids.usage_id)
                self.store.create_child(
                    user_id=ModuleStoreEnum.UserID.test,
                    parent_usage_key=self.course.scope_ids.usage_id,
                    block_type=block_type,
                    block_id=block_usage_key.block_id,
                    fields={test_data.field_name: initial_field_value},
                    asides=asides
                )

        if self.is_detached(block_type):
            self.assertCourseDoesntPointToBlock(block_usage_key)
        else:
            self.assertCoursePointsToBlock(block_usage_key)

        if with_asides:
            self.assertBlockHasContent(block_usage_key, test_data.field_name, initial_field_value,
                                       self.ASIDE_DATA_FIELD.field_name, self.ASIDE_DATA_FIELD.initial)
        else:
            self.assertBlockHasContent(block_usage_key, test_data.field_name, initial_field_value)

        return block_usage_key

    @ddt.data(*TESTABLE_BLOCK_TYPES)
    def test_update(self, block_type):
        block_usage_key = self._do_create(block_type)

        with self.store.branch_setting(ModuleStoreEnum.Branch.draft_preferred):
            block = self.store.get_item(block_usage_key)

            test_data = self.DATA_FIELDS[block_type]

            updated_field_value = test_data.updated
            assert updated_field_value != block.fields[test_data.field_name].read_from(block)

            block.fields[test_data.field_name].write_to(block, updated_field_value)

            self.store.update_item(block, ModuleStoreEnum.UserID.test, allow_not_found=True)

        self.assertBlockHasContent(block_usage_key, test_data.field_name, updated_field_value)

    @ddt.data(*TESTABLE_BLOCK_TYPES)
    def test_delete(self, block_type):
        block_usage_key = self._do_create(block_type)

        with self.store.branch_setting(ModuleStoreEnum.Branch.draft_preferred):
            self.store.delete_item(block_usage_key, ModuleStoreEnum.UserID.test)

        self.assertCourseDoesntPointToBlock(block_usage_key)
        self.assertBlockDoesntExist(block_usage_key)

    @ddt.data(ModuleStoreEnum.Branch.draft_preferred, ModuleStoreEnum.Branch.published_only)
    def test_course_summaries(self, branch):
        """ Test that `get_course_summaries` method in modulestore work as expected. """
        with self.store.branch_setting(branch_setting=branch):
            course_summaries = self.store.get_course_summaries()

            # Verify course summaries
            assert len(course_summaries) == 1

            # Verify that all course summary objects have the required attributes.
            self.assertCourseSummaryFields(course_summaries)

            # Verify fetched accessible courses list is a list of CourseSummery instances
            assert all(isinstance(course, CourseSummary) for course in course_summaries)

    @ddt.data(*itertools.product(['chapter', 'sequential'], [True, False]))
    @ddt.unpack
    def test_delete_child(self, block_type, child_published):
        block_usage_key = self.course.id.make_usage_key(block_type, 'test_block')
        child_usage_key = self.course.id.make_usage_key('html', 'test_child')

        self.assertCourseDoesntPointToBlock(block_usage_key)
        self.assertBlockDoesntExist(block_usage_key)
        self.assertBlockDoesntExist(child_usage_key)

        test_data = self.DATA_FIELDS[block_type]
        child_data = '<div>child value</div>'

        with self.store.branch_setting(ModuleStoreEnum.Branch.draft_preferred):
            self.store.create_child(
                user_id=ModuleStoreEnum.UserID.test,
                parent_usage_key=self.course.scope_ids.usage_id,
                block_type=block_type,
                block_id=block_usage_key.block_id,
                fields={test_data.field_name: test_data.initial},
            )

            self.store.create_child(
                user_id=ModuleStoreEnum.UserID.test,
                parent_usage_key=block_usage_key,
                block_type=child_usage_key.block_type,
                block_id=child_usage_key.block_id,
                fields={'data': child_data},
            )

            if child_published:
                self.store.publish(child_usage_key, ModuleStoreEnum.UserID.test)

        self.assertCoursePointsToBlock(block_usage_key)

        if child_published:
            self.assertParentOf(block_usage_key, child_usage_key)
        else:
            self.assertParentOf(block_usage_key, child_usage_key, draft=True)
            # N.B. whether the direct-only parent block points to the child in the publish branch
            # is left as undefined behavior

        self.assertBlockHasContent(block_usage_key, test_data.field_name, test_data.initial)

        if child_published:
            self.assertBlockHasContent(child_usage_key, 'data', child_data)
        else:
            self.assertBlockHasContent(child_usage_key, 'data', child_data, draft=True)
            self.assertBlockDoesntExist(child_usage_key, draft=False)

        with self.store.branch_setting(ModuleStoreEnum.Branch.draft_preferred):
            self.store.delete_item(child_usage_key, ModuleStoreEnum.UserID.test)

        self.assertCoursePointsToBlock(block_usage_key)
        self.assertNotParentOf(block_usage_key, child_usage_key)

        self.assertBlockDoesntExist(child_usage_key)


@ddt.ddt
class TestSplitDirectOnlyCategorySemantics(DirectOnlyCategorySemantics):
    """
    Verify DIRECT_ONLY_CATEGORY semantics against the SplitMongoModulestore.
    """
    __test__ = True

    @ddt.data(*TESTABLE_BLOCK_TYPES)
    @XBlockAside.register_temp_plugin(AsideTest, 'test_aside')
    @patch('xmodule.modulestore.split_mongo.runtime.SplitModuleStoreRuntime.applicable_aside_types',
           lambda self, block: ['test_aside'])
    def test_create_with_asides(self, block_type):
        self._do_create(block_type, with_asides=True)

    @ddt.data(*TESTABLE_BLOCK_TYPES)
    @XBlockAside.register_temp_plugin(AsideTest, 'test_aside')
    @patch('xmodule.modulestore.split_mongo.runtime.SplitModuleStoreRuntime.applicable_aside_types',
           lambda self, block: ['test_aside'])
    def test_update_asides(self, block_type):
        block_usage_key = self._do_create(block_type, with_asides=True)
        test_data = self.DATA_FIELDS[block_type]

        with self.store.branch_setting(ModuleStoreEnum.Branch.draft_preferred):
            block = self.store.get_item(block_usage_key)
            aside = self._get_aside(block)
            assert aside is not None
            aside.fields[self.ASIDE_DATA_FIELD.field_name].write_to(aside, self.ASIDE_DATA_FIELD.updated)

            self.store.update_item(block, ModuleStoreEnum.UserID.test, allow_not_found=True, asides=[aside])

        self.assertBlockHasContent(block_usage_key, test_data.field_name, test_data.initial,
                                   self.ASIDE_DATA_FIELD.field_name, self.ASIDE_DATA_FIELD.updated)
