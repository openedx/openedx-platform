"""
Test the publish code (mostly testing that publishing doesn't result in orphans)
"""


import itertools
import os
import re
import unittest
import uuid
import xml.etree.ElementTree as ET
from contextlib import contextmanager
from shutil import rmtree
from tempfile import mkdtemp

import pytest
import ddt

from xmodule.exceptions import InvalidVersionError
from xmodule.modulestore import ModuleStoreEnum
from xmodule.modulestore.exceptions import ItemNotFoundError
from xmodule.modulestore.tests.factories import CourseFactory, BlockFactory
from xmodule.modulestore.tests.utils import (
    SPLIT_MODULESTORE_SETUP,
    MongoContentstoreBuilder,
)
from xmodule.modulestore.xml_exporter import export_course_to_xml


@pytest.mark.django_db  # required if using split modulestore
class DraftPublishedOpTestCourseSetup(unittest.TestCase):
    """
    This class exists to test XML import and export between different modulestore
    classes.
    """

    def _create_course(self, store):
        """
        Create the course that'll be published below. The course has a binary structure, meaning:
        The course has two chapters (chapter_0 & chapter_1),
        each of which has two sequentials (sequential_0/1 & sequential_2/3),
        each of which has two verticals (vertical_0/1 - vertical_6/7),
        each of which has two units (unit_0/1 - unit_14/15).
        """
        def _make_block_id(block_type, num):
            """
            Given a block_type/num, return a block id.
            """
            return f'{block_type}{num:02d}'

        def _make_course_db_entry(parent_type, parent_id, block_id, idx, child_block_type, child_block_id_base):
            """
            Make a single entry for the course DB.
            """
            return {
                'parent_type': parent_type,
                'parent_id': parent_id,
                'index_in_children_list': idx % 2,
                'filename': block_id,
                'child_ids': (
                    (child_block_type, _make_block_id(child_block_id_base, idx * 2)),
                    (child_block_type, _make_block_id(child_block_id_base, idx * 2 + 1)),
                )
            }

        def _add_course_db_entry(parent_type, parent_id, block_id, block_type, idx, child_type, child_base):
            """
            Add a single entry for the course DB referenced by the tests below.
            """
            self.course_db.update(
                {
                    (block_type, block_id): _make_course_db_entry(
                        parent_type, parent_id, block_id, idx, child_type, child_base
                    )
                }
            )

        def _create_binary_structure_items(parent_type, block_type, num_items, child_block_type):
            """
            Add a level of the binary course structure by creating the items as children of the proper parents.
            """
            parent_id = 'course'
            for idx in range(num_items):
                if parent_type != 'course':
                    parent_id = _make_block_id(parent_type, idx // 2)
                parent_item = getattr(self, parent_id)
                block_id = _make_block_id(block_type, idx)
                setattr(self, block_id, BlockFactory.create(
                    parent_location=parent_item.location,
                    category=block_type,
                    modulestore=store,
                    publish_item=False,
                    location=self.course.id.make_usage_key(block_type, block_id)
                ))
                _add_course_db_entry(
                    parent_type, parent_id, block_id, block_type, idx, child_block_type, child_block_type
                )

        # Create all the course items on the draft branch.
        with store.branch_setting(ModuleStoreEnum.Branch.draft_preferred):
            # Create course.
            self.course = CourseFactory.create(
                org='test_org',
                number='999',
                run='test_run',
                display_name='My Test Course',
                modulestore=store
            )

            with store.bulk_operations(self.course.id):
                # Create chapters.
                _create_binary_structure_items('course', 'chapter', 2, 'sequential')
                _create_binary_structure_items('chapter', 'sequential', 4, 'vertical')
                _create_binary_structure_items('sequential', 'vertical', 8, 'html')
                _create_binary_structure_items('vertical', 'html', 16, '')

        # Create a list of all verticals for convenience.
        block_type = 'vertical'
        for idx in range(8):
            block_id = _make_block_id(block_type, idx)
            self.all_verticals.append((block_type, block_id))

        # Create a list of all html units for convenience.
        block_type = 'html'
        for idx in range(16):
            block_id = _make_block_id(block_type, idx)
            self.all_units.append((block_type, block_id))

    def setUp(self):
        self.user_id = -3
        self.course = None

        # For convenience, maintain a list of (block_type, block_id) pairs for all verticals/units.
        self.all_verticals = []
        self.all_units = []

        # Course block database is keyed on (block_type, block_id) pairs.
        # It's built during the course creation below and contains all the parent/child
        # data needed to check the OLX.
        self.course_db = {}

        super().setUp()


class OLXFormatChecker(unittest.TestCase):
    """
    Examines the on-disk course export to verify that specific items are present/missing
    in the course export.
    Currently assumes that the course is broken up into different subdirs.

    Requires from subclasses:
        self.root_export_dir - absolute root directory of course exports
        self.export_dir - top-level course export directory name
        self._ensure_exported() - A method that will export the course under test
            to self.export_dir.
    """
    unittest.TestCase.longMessage = True

    def _ensure_exported(self):
        """
        Method to ensure a course export - defined by subclass.
        """
        raise NotImplementedError()

    def _get_course_export_dir(self):
        """
        Ensure that the course has been exported and return course export dir.
        """
        self._ensure_exported()

        block_path = os.path.join(self.root_export_dir, self.export_dir)  # pylint: disable=no-member
        assert os.path.isdir(block_path), f'{block_path} is not a dir.'
        return block_path

    def _get_block_type_path(self, course_export_dir, block_type, draft):
        """
        Return the path to the block type subdirectory, factoring in drafts.
        """
        block_path = course_export_dir
        if draft:
            block_path = os.path.join(block_path, 'drafts')
        return os.path.join(block_path, block_type)

    def _get_block_filename(self, block_id):
        """
        Return the course export filename for a block.
        """
        return f'{block_id}.xml'

    def _get_block_contents(self, block_subdir_path, block_id):
        """
        Determine the filename containing the block info.
        Return the file contents.
        """
        self._ensure_exported()

        block_file = self._get_block_filename(block_id)
        block_file_path = os.path.join(block_subdir_path, block_file)
        assert os.path.isfile(block_file_path), f'{block_file_path} is not an existing file.'
        with open(block_file_path) as file_handle:
            return file_handle.read()

    def assertElementTag(self, element, tag):
        """
        Assert than an XML element has a specific tag.

        Arguments:
            element (ElementTree.Element): the element to check.
            tag (str): The tag to validate.
        """
        assert element.tag == tag

    def assertElementAttrsSubset(self, element, attrs):
        """
        Assert that an XML element has at least the specified set of
        attributes.

        Arguments:
            element (ElementTree.Element): the element to check.
            attrs (dict): A dict mapping {attr: regex} where
                each value in the dict is a regular expression
                to match against the named attribute.
        """
        for attribute, regex in attrs.items():
            self.assertRegex(element.get(attribute), regex)

    def parse_olx(self, block_type, block_id, **kwargs):
        """
        Arguments:
            block_type (str): The block-type of the XBlock to check.
            block_id (str): The block-id of the XBlock to check.
            draft (bool): If ``True``, run the assertions against the draft version of the
                identified XBlock.
        """
        course_export_dir = self._get_course_export_dir()
        is_draft = kwargs.pop('draft', False)

        block_path = self._get_block_type_path(course_export_dir, block_type, is_draft)
        block_contents = self._get_block_contents(block_path, block_id)
        return ET.fromstring(block_contents)

    def assertOLXMissing(self, block_type, block_id, **kwargs):
        """
        Assert that a particular block does not exist in a particular draft/published location.

        Arguments:
            block_type (str): The block-type of the XBlock to check.
            block_id (str): The block-id of the XBlock to check.
            draft (bool): If ``True``, assert that the block identified by ``block_type``
                ``block_id`` isn't a draft in the exported OLX.
        """
        course_export_dir = self._get_course_export_dir()
        is_draft = kwargs.pop('draft', False)
        block_path = self._get_block_type_path(course_export_dir, block_type, is_draft)
        block_file_path = os.path.join(block_path, self._get_block_filename(block_id))
        assert not os.path.exists(block_file_path), f'{block_file_path} exists but should not!'

    def assertParentReferences(self, element, course_key, parent_type, parent_id, index_in_children_list):
        """
        Assert that the supplied element references the supplied parents.

        Arguments:
            element: The element to check.
            course_key: The course the element is from.
            parent_type: The block_type of the expected parent node.
            parent_id: The block_id of the expected parent node.
            index_in_children_list: The expected index in the parent.
        """
        parent_key = course_key.make_usage_key(parent_type, parent_id)

        self.assertElementAttrsSubset(element, {
            'parent_url': re.escape(str(parent_key)),
            'index_in_children_list': re.escape(str(index_in_children_list)),
        })

    def assertOLXProperties(self, element, block_type, course_key, draft, **kwargs):
        """
        Assert that OLX properties (parent and child references) are satisfied.
        """
        child_types_ids = kwargs.pop('child_ids', None)
        filename = kwargs.pop('filename', None)

        self.assertElementTag(element, block_type)

        # Form the checked attributes based on the block type.
        if block_type == 'html':
            self.assertElementAttrsSubset(element, {'filename': filename})

        elif draft:
            # Draft items are expected to have certain XML attributes.
            self.assertParentReferences(
                element,
                course_key,
                **kwargs
            )

        # If children exist, construct regular expressions to check them.
        child_id_regex = None
        child_type = None
        if child_types_ids:
            # Grab the type of the first child as the type of all the children.
            child_type = child_types_ids[0][0]
            # Construct regex out of all the child_ids that are included.
            child_id_regex = '|'.join([child[1] for child in child_types_ids])

        for child in element:
            self.assertElementTag(child, child_type)
            self.assertElementAttrsSubset(child, {'url_name': child_id_regex})

    def _assertOLXBase(self, block_list, draft, published):  # pylint: disable=invalid-name
        """
        Check that all blocks in the list are draft blocks in the OLX format when the course is exported.
        """
        for block_data in block_list:
            block_params = self.course_db.get(block_data)
            assert block_params is not None
            (block_type, block_id) = block_data
            if draft:
                element = self.parse_olx(block_type, block_id, draft=True)
                self.assertOLXProperties(element, block_type, self.course.id, draft=True, **block_params)
            else:
                self.assertOLXMissing(block_type, block_id, draft=True)
            if published:
                element = self.parse_olx(block_type, block_id, draft=False)
                self.assertOLXProperties(element, block_type, self.course.id, draft=False, **block_params)
            else:
                self.assertOLXMissing(block_type, block_id, draft=False)

    def assertOLXIsDraftOnly(self, block_list):
        """
        Check that all blocks in the list are only draft blocks in the OLX format when the course is exported.
        """
        self._assertOLXBase(block_list, draft=True, published=False)

    def assertOLXIsPublishedOnly(self, block_list):
        """
        Check that all blocks in the list are only published blocks in the OLX format when the course is exported.
        """
        self._assertOLXBase(block_list, draft=False, published=True)

    def assertOLXIsDraftAndPublished(self, block_list):
        """
        Check that all blocks in the list are both draft and published in the OLX format when the course is exported.
        """
        self._assertOLXBase(block_list, draft=True, published=True)

    def assertOLXIsDeleted(self, block_list):
        """
        Check that all blocks in the list are no longer in the OLX format when the course is exported.
        """
        for block_data in block_list:
            (block_type, block_id) = block_data
            self.assertOLXMissing(block_type, block_id, draft=True)
            self.assertOLXMissing(block_type, block_id, draft=False)


class DraftPublishedOpBaseTestSetup(OLXFormatChecker, DraftPublishedOpTestCourseSetup):
    """
    Setup base class for draft/published/OLX tests.
    """

    EXPORTED_COURSE_BEFORE_DIR_NAME = 'exported_course_before'
    EXPORTED_COURSE_AFTER_DIR_NAME = 'exported_course_after_{}'

    def setUp(self):
        super().setUp()
        self.export_dir = self.EXPORTED_COURSE_BEFORE_DIR_NAME
        self.root_export_dir = None
        self.contentstore = None
        self.store = None

    @contextmanager
    def _create_export_dir(self):
        """
        Create a temporary export dir - and clean it up when done.
        """
        try:
            export_dir = mkdtemp()
            yield export_dir
        finally:
            rmtree(export_dir, ignore_errors=True)

    @contextmanager
    def _setup_test(self, modulestore_builder):
        """
        Create the export dir, contentstore, and modulestore for a test.
        """
        with self._create_export_dir() as self.root_export_dir:
            # Construct the contentstore for storing the first import
            with MongoContentstoreBuilder().build() as self.contentstore:
                # Construct the modulestore for storing the first import (using the previously created contentstore)
                with modulestore_builder.build(contentstore=self.contentstore) as self.store:
                    # Create the course.
                    self._create_course(self.store)
                    yield

    def _ensure_exported(self):
        """
        Check that the course has been exported. If not, export it.
        """
        exported_course_path = os.path.join(self.root_export_dir, self.export_dir)
        if not (os.path.exists(exported_course_path) and os.path.isdir(exported_course_path)):
            # Export the course.
            export_course_to_xml(
                self.store,
                self.contentstore,
                self.course.id,
                self.root_export_dir,
                self.export_dir,
            )

    @property
    def is_split_modulestore(self):
        """
        ``True`` when modulestore under test is a SplitMongoModuleStore.
        """
        return self.store.get_modulestore_type(self.course.id) == ModuleStoreEnum.Type.split

    def _make_new_export_dir_name(self):
        """
        Make a unique name for the new export dir.
        """
        return self.EXPORTED_COURSE_AFTER_DIR_NAME.format(str(uuid.uuid4())[:8])

    def publish(self, block_list):
        """
        Get each item, publish it, and shift to a new course export dir.
        """
        for (block_type, block_id) in block_list:
            # Get the specified test item from the draft branch.
            with self.store.branch_setting(ModuleStoreEnum.Branch.draft_preferred):
                test_item = self.store.get_item(
                    self.course.id.make_usage_key(block_type=block_type, block_id=block_id)
                )
            # Publish the draft item to the published branch.
            self.store.publish(test_item.location, self.user_id)
        # Since the elemental operation is now complete, shift to the post-operation export directory name.
        self.export_dir = self._make_new_export_dir_name()

    def unpublish(self, block_list):
        """
        Get each item, unpublish it, and shift to a new course export dir.
        """
        for (block_type, block_id) in block_list:
            # Get the specified test item from the published branch.
            with self.store.branch_setting(ModuleStoreEnum.Branch.published_only):
                test_item = self.store.get_item(
                    self.course.id.make_usage_key(block_type=block_type, block_id=block_id)
                )
            # Unpublish the draft item from the published branch.
            self.store.unpublish(test_item.location, self.user_id)
        # Since the elemental operation is now complete, shift to the post-operation export directory name.
        self.export_dir = self._make_new_export_dir_name()

    def delete_item(self, block_list, revision):
        """
        Get each item, delete it, and shift to a new course export dir.
        """
        for (block_type, block_id) in block_list:
            # Get the specified test item from the draft branch.
            with self.store.branch_setting(ModuleStoreEnum.Branch.draft_preferred):
                test_item = self.store.get_item(
                    self.course.id.make_usage_key(block_type=block_type, block_id=block_id)
                )
            # Delete the item from the specified branch.
            self.store.delete_item(test_item.location, self.user_id, revision=revision)
        # Since the elemental operation is now complete, shift to the post-operation export directory name.
        self.export_dir = self._make_new_export_dir_name()

    def convert_to_draft(self, block_list):
        """
        Get each item, convert it to draft, and shift to a new course export dir.
        """
        for (block_type, block_id) in block_list:
            # Get the specified test item from the draft branch.
            with self.store.branch_setting(ModuleStoreEnum.Branch.published_only):
                test_item = self.store.get_item(
                    self.course.id.make_usage_key(block_type=block_type, block_id=block_id)
                )
            # Convert the item from the specified branch from published to draft.
            self.store.convert_to_draft(test_item.location, self.user_id)
        # Since the elemental operation is now complete, shift to the post-operation export directory name.
        self.export_dir = self._make_new_export_dir_name()

    def revert_to_published(self, block_list):
        """
        Get each item, revert it to published, and shift to a new course export dir.
        """
        for (block_type, block_id) in block_list:
            # Get the specified test item from the draft branch.
            with self.store.branch_setting(ModuleStoreEnum.Branch.draft_preferred):
                test_item = self.store.get_item(
                    self.course.id.make_usage_key(block_type=block_type, block_id=block_id)
                )
            # Revert the item from the specified branch from draft to published.
            self.store.revert_to_published(test_item.location, self.user_id)
        # Since the elemental operation is now complete, shift to the post-operation export directory name.
        self.export_dir = self._make_new_export_dir_name()


@ddt.ddt
class ElementalPublishingTests(DraftPublishedOpBaseTestSetup):
    """
    Tests for the publish() operation.
    """
    @ddt.data(SPLIT_MODULESTORE_SETUP)
    def test_autopublished_chapters_sequentials(self, modulestore_builder):
        with self._setup_test(modulestore_builder):
            # When a course is created out of chapters/sequentials/verticals/units
            # as this course is, the chapters/sequentials are auto-published
            # and the verticals/units are not.
            # Ensure that this is indeed the case by verifying the OLX.
            block_list_autopublished = (
                ('chapter', 'chapter00'),
                ('chapter', 'chapter01'),
                ('sequential', 'sequential00'),
                ('sequential', 'sequential01'),
                ('sequential', 'sequential02'),
                ('sequential', 'sequential03'),
            )
            block_list_draft = self.all_verticals + self.all_units
            self.assertOLXIsPublishedOnly(block_list_autopublished)
            self.assertOLXIsDraftOnly(block_list_draft)

    @ddt.data(SPLIT_MODULESTORE_SETUP)
    def test_publish_split_unit(self, modulestore_builder):
        with self._setup_test(modulestore_builder):

            # MODULESTORE_DIFFERENCE:
            # In Split, you cannot publish an item whose parents are unpublished.
            # Split will raise an exception when the item's parent(s) aren't found
            # in the published branch.
            with pytest.raises(ItemNotFoundError):
                self.publish((('html', 'html00'),))

    @ddt.data(SPLIT_MODULESTORE_SETUP)
    def test_publish_multiple_verticals(self, modulestore_builder):
        with self._setup_test(modulestore_builder):

            block_list_parents_to_publish = (
                ('vertical', 'vertical03'),
                ('vertical', 'vertical04'),
            )
            block_list_publish = block_list_parents_to_publish + (
                ('html', 'html06'),
                ('html', 'html07'),
                ('html', 'html08'),
                ('html', 'html09'),
            )
            block_list_untouched = (
                ('vertical', 'vertical00'),
                ('vertical', 'vertical01'),
                ('vertical', 'vertical02'),
                ('vertical', 'vertical05'),
                ('vertical', 'vertical06'),
                ('vertical', 'vertical07'),
                ('html', 'html00'),
                ('html', 'html01'),
                ('html', 'html02'),
                ('html', 'html03'),
                ('html', 'html04'),
                ('html', 'html05'),
                ('html', 'html10'),
                ('html', 'html11'),
                ('html', 'html12'),
                ('html', 'html13'),
                ('html', 'html14'),
                ('html', 'html15'),
            )

            # Ensure that both groups of verticals and children are drafts in the exported OLX.
            self.assertOLXIsDraftOnly(block_list_publish)
            self.assertOLXIsDraftOnly(block_list_untouched)

            # Publish both vertical03 and vertical 04.
            self.publish(block_list_parents_to_publish)

            # Ensure that the published verticals and children are indeed published in the exported OLX.
            self.assertOLXIsPublishedOnly(block_list_publish)
            # Ensure that the untouched vertical and children are still untouched.
            self.assertOLXIsDraftOnly(block_list_untouched)

    @ddt.data(SPLIT_MODULESTORE_SETUP)
    def test_publish_single_sequential(self, modulestore_builder):
        """
        Sequentials are auto-published. But publishing them explictly publishes their children,
        changing the OLX of each sequential - the vertical children are in the sequential post-publish.
        """
        with self._setup_test(modulestore_builder):

            block_list_autopublished = (
                ('sequential', 'sequential00'),
            )
            block_list = (
                ('vertical', 'vertical00'),
                ('vertical', 'vertical01'),
                ('html', 'html00'),
                ('html', 'html01'),
                ('html', 'html02'),
                ('html', 'html03'),
            )
            # Ensure that the autopublished sequential exists as such in the exported OLX.
            self.assertOLXIsPublishedOnly(block_list_autopublished)
            # Ensure that the verticals and their children are drafts in the exported OLX.
            self.assertOLXIsDraftOnly(block_list)
            # Publish the sequential block.
            self.publish(block_list_autopublished)
            # Ensure that the sequential is still published in the exported OLX.
            self.assertOLXIsPublishedOnly(block_list_autopublished)
            # Ensure that the verticals and their children are published in the exported OLX.
            self.assertOLXIsPublishedOnly(block_list)

    @ddt.data(SPLIT_MODULESTORE_SETUP)
    def test_publish_single_chapter(self, modulestore_builder):
        """
        Chapters are auto-published.
        """
        with self._setup_test(modulestore_builder):

            block_list_autopublished = (
                ('chapter', 'chapter00'),
            )
            block_list_published = (
                ('vertical', 'vertical00'),
                ('vertical', 'vertical01'),
                ('vertical', 'vertical02'),
                ('vertical', 'vertical03'),
                ('html', 'html00'),
                ('html', 'html01'),
                ('html', 'html02'),
                ('html', 'html03'),
                ('html', 'html04'),
                ('html', 'html05'),
                ('html', 'html06'),
                ('html', 'html07'),
            )
            block_list_untouched = (
                ('vertical', 'vertical04'),
                ('vertical', 'vertical05'),
                ('vertical', 'vertical06'),
                ('vertical', 'vertical07'),
                ('html', 'html08'),
                ('html', 'html09'),
                ('html', 'html10'),
                ('html', 'html11'),
                ('html', 'html12'),
                ('html', 'html13'),
                ('html', 'html14'),
                ('html', 'html15'),
            )
            # Ensure that the autopublished chapter exists as such in the exported OLX.
            self.assertOLXIsPublishedOnly(block_list_autopublished)
            # Ensure that the verticals and their children are drafts in the exported OLX.
            self.assertOLXIsDraftOnly(block_list_published)
            self.assertOLXIsDraftOnly(block_list_untouched)
            # Publish the chapter block.
            self.publish(block_list_autopublished)
            # Ensure that the chapter is still published in the exported OLX.
            self.assertOLXIsPublishedOnly(block_list_autopublished)
            # Ensure that the vertical and its children are published in the exported OLX.
            self.assertOLXIsPublishedOnly(block_list_published)
            # Ensure that the other vertical and children are not published.
            self.assertOLXIsDraftOnly(block_list_untouched)


@ddt.ddt
class ElementalUnpublishingTests(DraftPublishedOpBaseTestSetup):
    """
    Tests for the unpublish() operation.
    """
    @ddt.data(SPLIT_MODULESTORE_SETUP)
    def test_unpublish_draft_unit(self, modulestore_builder):
        with self._setup_test(modulestore_builder):

            block_list_to_unpublish = (
                ('html', 'html08'),
            )
            # The unit is a draft.
            self.assertOLXIsDraftOnly(block_list_to_unpublish)
            # Since there's no published version, attempting an unpublish throws an exception.
            with pytest.raises(ItemNotFoundError):
                self.unpublish(block_list_to_unpublish)

    @ddt.data(SPLIT_MODULESTORE_SETUP)
    def test_unpublish_published_units(self, modulestore_builder):
        with self._setup_test(modulestore_builder):

            block_list_to_unpublish = (
                ('html', 'html08'),
                ('html', 'html09'),
            )
            block_list_parent = (
                ('vertical', 'vertical04'),
            )
            # The units are drafts.
            self.assertOLXIsDraftOnly(block_list_to_unpublish)
            self.assertOLXIsDraftOnly(block_list_parent)
            # Publish the *parent* of the units, which also publishes the units.
            self.publish(block_list_parent)
            # The units are now published.
            self.assertOLXIsPublishedOnly(block_list_parent)
            self.assertOLXIsPublishedOnly(block_list_to_unpublish)
            # Unpublish the child units.
            self.unpublish(block_list_to_unpublish)
            # The units are now drafts again.
            self.assertOLXIsDraftOnly(block_list_to_unpublish)
            # MODULESTORE_DIFFERENCE:
            if self.is_split_modulestore:
                # Split:
                # The parent now has a draft *and* published item.
                self.assertOLXIsDraftAndPublished(block_list_parent)
            else:
                raise Exception("Must test either Old Mongo or Split modulestore!")

    @ddt.data(SPLIT_MODULESTORE_SETUP)
    def test_unpublish_draft_vertical(self, modulestore_builder):
        with self._setup_test(modulestore_builder):

            block_list_to_unpublish = (
                ('vertical', 'vertical02'),
            )
            # The vertical is a draft.
            self.assertOLXIsDraftOnly(block_list_to_unpublish)
            # Since there's no published version, attempting an unpublish throws an exception.
            with pytest.raises(ItemNotFoundError):
                self.unpublish(block_list_to_unpublish)

    @ddt.data(SPLIT_MODULESTORE_SETUP)
    def test_unpublish_published_vertical(self, modulestore_builder):
        with self._setup_test(modulestore_builder):

            block_list_to_unpublish = (
                ('vertical', 'vertical02'),
            )
            block_list_unpublished_children = (
                ('html', 'html04'),
                ('html', 'html05'),
            )
            block_list_untouched = (
                ('vertical', 'vertical04'),
                ('vertical', 'vertical05'),
                ('vertical', 'vertical06'),
                ('vertical', 'vertical07'),
                ('html', 'html08'),
                ('html', 'html09'),
                ('html', 'html10'),
                ('html', 'html11'),
                ('html', 'html12'),
                ('html', 'html13'),
                ('html', 'html14'),
                ('html', 'html15'),
            )
            # At first, no vertical or unit is published.
            self.assertOLXIsDraftOnly(block_list_to_unpublish)
            self.assertOLXIsDraftOnly(block_list_unpublished_children)
            self.assertOLXIsDraftOnly(block_list_untouched)
            # Then publish a vertical.
            self.publish(block_list_to_unpublish)
            # The published vertical and its children will be published.
            self.assertOLXIsPublishedOnly(block_list_to_unpublish)
            self.assertOLXIsPublishedOnly(block_list_unpublished_children)
            self.assertOLXIsDraftOnly(block_list_untouched)
            # Now, unpublish the same vertical.
            self.unpublish(block_list_to_unpublish)
            # The unpublished vertical and its children will now be a draft.
            self.assertOLXIsDraftOnly(block_list_to_unpublish)
            self.assertOLXIsDraftOnly(block_list_unpublished_children)
            self.assertOLXIsDraftOnly(block_list_untouched)

    @ddt.data(SPLIT_MODULESTORE_SETUP)
    def test_unpublish_draft_sequential(self, modulestore_builder):
        with self._setup_test(modulestore_builder):

            block_list_to_unpublish = (
                ('sequential', 'sequential03'),
            )
            with pytest.raises(InvalidVersionError):
                self.unpublish(block_list_to_unpublish)


@ddt.ddt
class ElementalDeleteItemTests(DraftPublishedOpBaseTestSetup):
    """
    Tests for the delete_item() operation.
    """
    def _check_for_item_deletion(self, block_list, expected_result):
        """
        Based on the expected result, verify that OLX for the listed blocks is correct.
        """
        assert_method = getattr(self, expected_result)
        assert_method(block_list)

    @ddt.data(*itertools.product(
        (SPLIT_MODULESTORE_SETUP,),
        (
            (ModuleStoreEnum.RevisionOption.published_only, 'assertOLXIsDraftOnly'),
            (ModuleStoreEnum.RevisionOption.all, 'assertOLXIsDeleted'),
            (None, 'assertOLXIsDeleted'),
        )
    ))
    @ddt.unpack
    def test_delete_draft_unit(self, modulestore_builder, revision_and_result):
        with self._setup_test(modulestore_builder):

            block_list_to_delete = (
                ('html', 'html08'),
            )
            (revision, result) = revision_and_result
            # The unit is a draft.
            self.assertOLXIsDraftOnly(block_list_to_delete)
            # MODULESTORE_DIFFERENCE:
            if self.is_split_modulestore:
                self.delete_item(block_list_to_delete, revision=revision)
                self._check_for_item_deletion(block_list_to_delete, result)
            else:
                raise Exception("Must test either Old Mongo or Split modulestore!")

    @ddt.data(*itertools.product(
        (SPLIT_MODULESTORE_SETUP,),
        (
            # MODULESTORE_DIFFERENCE: This first line is different between old Mongo and Split for verticals.
            # Split does not delete the draft vertical when a published_only revision is specified.
            (ModuleStoreEnum.RevisionOption.published_only, 'assertOLXIsDraftOnly'),
            (ModuleStoreEnum.RevisionOption.all, 'assertOLXIsDeleted'),
            (None, 'assertOLXIsDeleted'),
        )
    ))
    @ddt.unpack
    def test_split_delete_draft_vertical(self, modulestore_builder, revision_and_result):
        with self._setup_test(modulestore_builder):

            block_list_to_delete = (
                ('vertical', 'vertical03'),
            )
            block_list_children = (
                ('html', 'html06'),
                ('html', 'html07'),
            )
            (revision, result) = revision_and_result
            # The vertical is a draft.
            self.assertOLXIsDraftOnly(block_list_to_delete)
            if revision in (ModuleStoreEnum.RevisionOption.published_only, ModuleStoreEnum.RevisionOption.all):
                self.delete_item(block_list_to_delete, revision=revision)
                self._check_for_item_deletion(block_list_to_delete, result)
            else:
                self.delete_item(block_list_to_delete, revision=revision)
                self._check_for_item_deletion(block_list_to_delete, result)
                self.assertOLXIsDeleted(block_list_children)

    @ddt.data(*itertools.product(
        (SPLIT_MODULESTORE_SETUP,),
        (
            (ModuleStoreEnum.RevisionOption.published_only, 'assertOLXIsDeleted'),
            (ModuleStoreEnum.RevisionOption.all, 'assertOLXIsDeleted'),
            (None, 'assertOLXIsDeleted'),
        )
    ))
    @ddt.unpack
    def test_delete_sequential(self, modulestore_builder, revision_and_result):
        with self._setup_test(modulestore_builder):

            block_list_to_delete = (
                ('sequential', 'sequential03'),
            )
            block_list_children = (
                ('vertical', 'vertical06'),
                ('vertical', 'vertical07'),
                ('html', 'html12'),
                ('html', 'html13'),
                ('html', 'html14'),
                ('html', 'html15'),
            )
            (revision, result) = revision_and_result
            # Sequentials are auto-published.
            self.assertOLXIsPublishedOnly(block_list_to_delete)
            self.delete_item(block_list_to_delete, revision=revision)
            self._check_for_item_deletion(block_list_to_delete, result)
            self.assertOLXIsDeleted(block_list_children)

    @ddt.data(*itertools.product(
        (SPLIT_MODULESTORE_SETUP,),
        (
            (ModuleStoreEnum.RevisionOption.published_only, 'assertOLXIsDeleted'),
            (ModuleStoreEnum.RevisionOption.all, 'assertOLXIsDeleted'),
            (None, 'assertOLXIsDeleted'),
        )
    ))
    @ddt.unpack
    def test_delete_chapter(self, modulestore_builder, revision_and_result):
        with self._setup_test(modulestore_builder):

            block_list_to_delete = (
                ('chapter', 'chapter01'),
            )
            autopublished_children = (
                ('sequential', 'sequential02'),
                ('sequential', 'sequential03'),
            )
            block_list_draft_children = (
                ('vertical', 'vertical04'),
                ('vertical', 'vertical05'),
                ('vertical', 'vertical06'),
                ('vertical', 'vertical07'),
                ('html', 'html08'),
                ('html', 'html09'),
                ('html', 'html10'),
                ('html', 'html11'),
                ('html', 'html12'),
                ('html', 'html13'),
                ('html', 'html14'),
                ('html', 'html15'),
            )
            (revision, result) = revision_and_result
            # Chapters are auto-published.
            self.assertOLXIsPublishedOnly(block_list_to_delete)
            self.delete_item(block_list_to_delete, revision=revision)
            self._check_for_item_deletion(block_list_to_delete, result)
            self.assertOLXIsDeleted(autopublished_children)
            self.assertOLXIsDeleted(block_list_draft_children)


@ddt.ddt
class ElementalConvertToDraftTests(DraftPublishedOpBaseTestSetup):
    """
    Tests for the convert_to_draft() operation.
    """
    @ddt.data(SPLIT_MODULESTORE_SETUP)
    def test_convert_to_draft_published_vertical(self, modulestore_builder):
        with self._setup_test(modulestore_builder):

            block_list_to_convert = (
                ('vertical', 'vertical02'),
            )
            # At first, no vertical is published.
            self.assertOLXIsDraftOnly(block_list_to_convert)
            # Then publish a vertical.
            self.publish(block_list_to_convert)
            # The vertical will be published.
            self.assertOLXIsPublishedOnly(block_list_to_convert)
            # Now, convert the same vertical to draft.
            self.convert_to_draft(block_list_to_convert)
            # MODULESTORE_DIFFERENCE:
            if self.is_split_modulestore:
                # Split:
                # This operation is a no-op is Split since there's always a draft version maintained.
                self.assertOLXIsPublishedOnly(block_list_to_convert)
            else:
                raise Exception("Must test either Old Mongo or Split modulestore!")

    @ddt.data(SPLIT_MODULESTORE_SETUP)
    def test_convert_to_draft_autopublished_sequential(self, modulestore_builder):
        with self._setup_test(modulestore_builder):

            block_list_to_convert = (
                ('sequential', 'sequential03'),
            )
            # Sequentials are auto-published.
            self.assertOLXIsPublishedOnly(block_list_to_convert)
            # MODULESTORE_DIFFERENCE:
            if self.is_split_modulestore:
                # Split:
                # Now, convert the same sequential to draft.
                self.convert_to_draft(block_list_to_convert)
                # This operation is a no-op is Split since there's always a draft version maintained.
                self.assertOLXIsPublishedOnly(block_list_to_convert)
            else:
                raise Exception("Must test either Old Mongo or Split modulestore!")


@ddt.ddt
class ElementalRevertToPublishedTests(DraftPublishedOpBaseTestSetup):
    """
    Tests for the revert_to_published() operation.
    """
    @ddt.data(SPLIT_MODULESTORE_SETUP)
    def test_revert_to_published_unpublished_vertical(self, modulestore_builder):
        with self._setup_test(modulestore_builder):

            block_list_to_revert = (
                ('vertical', 'vertical02'),
            )
            # At first, no vertical is published.
            self.assertOLXIsDraftOnly(block_list_to_revert)
            # Now, without publishing anything first, revert the same vertical to published.
            # Since no published version exists, an exception is raised.
            with pytest.raises(InvalidVersionError):
                self.revert_to_published(block_list_to_revert)

    @ddt.data(SPLIT_MODULESTORE_SETUP)
    def test_revert_to_published_published_vertical(self, modulestore_builder):
        with self._setup_test(modulestore_builder):

            block_list_to_revert = (
                ('vertical', 'vertical02'),
            )
            # At first, no vertical is published.
            self.assertOLXIsDraftOnly(block_list_to_revert)
            # Then publish a vertical.
            self.publish(block_list_to_revert)
            # The vertical will be published.
            self.assertOLXIsPublishedOnly(block_list_to_revert)
            # Now, revert the same vertical to published.
            self.revert_to_published(block_list_to_revert)
            # Basically a no-op - there was no draft version to revert.
            self.assertOLXIsPublishedOnly(block_list_to_revert)

    @ddt.data(SPLIT_MODULESTORE_SETUP)
    def test_revert_to_published_vertical(self, modulestore_builder):
        with self._setup_test(modulestore_builder):

            block_list_to_revert = (
                ('vertical', 'vertical02'),
            )
            # At first, no vertical is published.
            self.assertOLXIsDraftOnly(block_list_to_revert)
            # Then publish a vertical.
            self.publish(block_list_to_revert)
            # The vertical will be published.
            self.assertOLXIsPublishedOnly(block_list_to_revert)

            # Change something in the draft item and update it.
            with self.store.branch_setting(ModuleStoreEnum.Branch.draft_preferred):
                item = self.store.get_item(
                    self.course.id.make_usage_key(block_type='vertical', block_id='vertical02')
                )
                item.display_name = 'SNAFU'
                self.store.update_item(item, self.user_id)
            self.export_dir = self._make_new_export_dir_name()

            # The vertical now has a draft -and- published version.
            self.assertOLXIsDraftAndPublished(block_list_to_revert)
            # Now, revert the same vertical to published.
            self.revert_to_published(block_list_to_revert)
            # The draft version is now gone.
            self.assertOLXIsPublishedOnly(block_list_to_revert)
