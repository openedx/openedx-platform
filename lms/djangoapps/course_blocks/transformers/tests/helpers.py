"""
Test helpers for testing course block transformers.
"""


from unittest.mock import patch

from common.djangoapps.course_modes.models import CourseMode
from common.djangoapps.student.tests.factories import CourseEnrollmentFactory, UserFactory
from lms.djangoapps.courseware.access import has_access
from openedx.core.djangoapps.content.block_structure.tests.helpers import clear_registered_transformers_cache
from openedx.core.djangoapps.content.block_structure.transformers import BlockStructureTransformers
from xmodule.modulestore import ModuleStoreEnum
from xmodule.modulestore.django import modulestore
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase
from xmodule.modulestore.tests.factories import BlockFactory, CourseFactory

from ...api import get_course_blocks


class TransformerRegistryTestMixin:
    """
    Mixin that overrides the TransformerRegistry so that it returns
    TRANSFORMER_CLASS_TO_TEST as a registered transformer.
    """
    def setUp(self):
        super().setUp()
        self.patcher = patch(
            'openedx.core.djangoapps.content.block_structure.transformer_registry.'
            'TransformerRegistry.get_registered_transformers'
        )
        mock_registry = self.patcher.start()
        mock_registry.return_value = {self.TRANSFORMER_CLASS_TO_TEST}
        self.transformers = BlockStructureTransformers([self.TRANSFORMER_CLASS_TO_TEST()])

    def tearDown(self):
        self.patcher.stop()
        clear_registered_transformers_cache()


class CourseStructureTestCase(TransformerRegistryTestMixin, ModuleStoreTestCase):
    """
    Helper for test cases that need to build course structures.
    """

    def setUp(self):
        """
        Create users.
        """
        super().setUp()
        # Set up users.
        self.password = self.TEST_PASSWORD
        self.user = UserFactory.create(password=self.password)
        self.staff = UserFactory.create(password=self.password, is_staff=True)

    def create_block_id(self, block_type, block_ref):
        """
        Returns the block id (display name) that is used in the test
        course structures for the given block type and block reference
        string.
        """
        return f'{block_type}_{block_ref}'

    def build_xblock(self, block_hierarchy, block_map, parent):
        """
        Build an XBlock, add it to block_map, and call build_xblock on
        the children defined in block_dict.

        Arguments:
            block_hierarchy (BlockStructureDict): Definition of
                hierarchy, from this block down.
            block_map (dict[str: XBlock]): Mapping from '#ref' values to
                their XBlocks.
            parent (XBlock): Parent block for this xBlock.
        """
        block_type = block_hierarchy['#type']
        block_ref = block_hierarchy['#ref']
        factory = (CourseFactory if block_type == 'course' else BlockFactory)
        kwargs = {key: value for key, value in block_hierarchy.items() if key[0] != '#'}

        if block_type != 'course':
            kwargs['category'] = block_type
            kwargs['publish_item'] = True
        if parent:
            kwargs['parent'] = parent

        xblock = factory.create(
            display_name=self.create_block_id(block_type, block_ref),
            **kwargs
        )
        block_map[block_ref] = xblock

        for child_hierarchy in block_hierarchy.get('#children', []):
            self.build_xblock(child_hierarchy, block_map, xblock)

    def add_parents(self, block_hierarchy, block_map):
        """
        Recursively traverse the block_hierarchy and add additional
        parents. This method is expected to be called only after all
        blocks have been created.

        The additional parents are obtained from the '#parents' field
        and is expected to be a list of '#ref' values of the parents.

        Note: if a '#parents' field is found, the block is removed from
        the course block since it is expected to not belong to the root.
        If the block is meant to be a direct child of the course as
        well, the course should be explicitly listed in '#parents'.

        Arguments:
            block_hierarchy (BlockStructureDict):
                Definition of block hierarchy.
            block_map (dict[str: XBlock]):
                Mapping from '#ref' values to their XBlocks.

        """
        store = modulestore()
        parents = block_hierarchy.get('#parents', [])
        if parents:
            block_key = block_map[block_hierarchy['#ref']].location

            # First remove the block from the course.
            # It would be re-added to the course if the course was
            # explicitly listed in parents.
            with store.branch_setting(ModuleStoreEnum.Branch.draft_preferred, block_map['course'].id):
                course = store.get_item(block_map['course'].location)
            if block_key in course.children:
                course.children.remove(block_key)
                block_map['course'] = update_block(course)

            # Add this to block to each listed parent.
            for parent_ref in parents:
                with store.branch_setting(ModuleStoreEnum.Branch.draft_preferred, course.id):
                    parent_block = store.get_item(block_map[parent_ref].location)
                parent_block.children.append(block_key)
                block_map[parent_ref] = update_block(parent_block)

        # recursively call the children
        for child_hierarchy in block_hierarchy.get('#children', []):
            self.add_parents(child_hierarchy, block_map)

    def build_course(self, course_hierarchy):
        """
        Build a hierarchy of XBlocks.

        Arguments:
            course_hierarchy (BlockStructureDict): Definition of course
                hierarchy.

            where a BlockStructureDict is a list of dicts in the form {
                'key1': 'value1',
                ...
                'keyN': 'valueN',
                '#type': block_type,
                '#ref': short_string_for_referencing_block,
                '#children': list[BlockStructureDict],
                '#parents': list['#ref' values]
            }

            Special keys start with '#'; the rest just get passed as
            kwargs to Factory.create.

            Note: the caller has a choice of whether to create
            (1) a nested block structure with children blocks embedded
                within their parents, or
            (2) a flat block structure with children blocks defined
                alongside their parents and attached via the #parents
                field, or
            (3) a combination of both #1 and #2 used for whichever
                blocks.

            Note 2: When the #parents field is used in addition to the
            nested pattern for a block, it specifies additional parents
            that aren't already implied by having the block exist within
            another block's #children field.

        Returns:
            dict[str: XBlock]:
                Mapping from '#ref' values to their XBlocks.
        """
        block_map = {}

        # build the course tree
        for block_hierarchy in course_hierarchy:
            self.build_xblock(block_hierarchy, block_map, parent=None)

        # add additional parents if the course is a DAG or built
        # linearly (without specifying '#children' values)
        for block_hierarchy in course_hierarchy:
            self.add_parents(block_hierarchy, block_map)

        publish_course(block_map['course'])

        return block_map

    def get_block_key_set(self, blocks, *refs):
        """
        Gets the set of usage keys that correspond to the list of
        #ref values as defined on blocks.

        Returns: set[UsageKey]
        """
        xblocks = (blocks[ref] for ref in refs)
        return {xblock.location for xblock in xblocks}  # lint-amnesty, pylint: disable=consider-using-set-comprehension


class BlockParentsMapTestCase(TransformerRegistryTestMixin, ModuleStoreTestCase):
    """
    Test helper class for creating a test course of
    a graph of vertical blocks based on a parents_map.
    """

    # Tree formed by parent_map:
    #        0
    #     /     \
    #    1       2
    #   / \     / \
    #  3   4   /   5
    #       \ /
    #        6
    # Note the parents must always have lower indices than their
    # children.
    parents_map = [[], [0], [0], [1], [1], [2], [2, 4]]

    def setUp(self):
        super().setUp()

        # create the course
        self.course = CourseFactory.create()

        # an ordered list of block locations, where the index
        # corresponds to the block's index in the parents_map.
        self.xblock_keys = [self.course.location]

        # create all other blocks in the course
        for i, parents_index in enumerate(self.parents_map):
            if i == 0:
                continue  # course already created

            self.xblock_keys.append(
                BlockFactory.create(
                    parent=self.get_block(parents_index[0]),
                    category="vertical",
                ).location
            )

            # add additional parents
            if len(parents_index) > 1:
                for index in range(1, len(parents_index)):
                    parent_index = parents_index[index]
                    parent_block = self.get_block(parent_index)
                    parent_block.children.append(self.xblock_keys[i])
                    update_block(parent_block)

        self.password = self.TEST_PASSWORD
        self.student = UserFactory.create(is_staff=False, username='test_student', password=self.password)
        self.staff = UserFactory.create(is_staff=True, username='test_staff', password=self.password)
        CourseEnrollmentFactory.create(
            is_active=True,
            mode=CourseMode.DEFAULT_MODE_SLUG,
            user=self.student,
            course_id=self.course.id
        )

    def assert_transform_results(
            self,
            test_user,
            expected_user_accessible_blocks,
            blocks_with_differing_access=None,
            transformers=None,
    ):
        """
        Verifies the results of transforming the blocks in the course.

        Arguments:
            test_user (User): The non-staff user that is being tested.
                For example, self.student.

            expected_user_accessible_blocks (set(int)): Set of blocks
                (indices) that a student user is expected to have access
                to after the transformers are executed.

            blocks_with_differing_access (set(int)): Set of
                blocks (indices) whose access will differ from the
                transformers result and the current implementation of
                has_access.  If not provided, does not compare with
                has_access results.

            transformers (BlockStructureTransformers): An optional collection
                of transformers that are to be executed.  If not
                provided, the default value used by get_course_blocks
                is used.
        """
        publish_course(self.course)

        # verify given test user has access to expected blocks
        self._check_results(
            test_user,
            expected_user_accessible_blocks,
            blocks_with_differing_access,
            transformers,
        )

        # verify staff has access to all blocks
        self._check_results(self.staff, set(range(len(self.parents_map))), {}, transformers)

    def get_block(self, block_index):
        """
        Helper method to retrieve the requested block (index) from the
        modulestore
        """
        store = modulestore()
        with store.branch_setting(ModuleStoreEnum.Branch.draft_preferred, self.course.id):
            return store.get_item(self.xblock_keys[block_index])

    def _check_results(self, user, expected_accessible_blocks, blocks_with_differing_access, transformers):
        """
        Verifies the results of transforming the blocks in the
        course for the given user.
        """

        self.client.login(username=user.username, password=self.password)
        block_structure = get_course_blocks(user, self.course.location, transformers)

        for i, xblock_key in enumerate(self.xblock_keys):

            # compute access results of the block
            block_structure_result = xblock_key in block_structure

            # compare with expected value
            assert block_structure_result == (i in expected_accessible_blocks), \
                f'block_structure return value {block_structure_result} not equal to expected value for block' \
                f' {i} for user {user.username}'

            if blocks_with_differing_access:
                # compare with has_access_result
                has_access_result = bool(has_access(user, 'load', self.get_block(i), course_key=self.course.id))
                if i in blocks_with_differing_access:
                    assert block_structure_result != has_access_result, \
                        f'block structure ({block_structure_result}) & has_access ({has_access_result})' \
                        f' results are equal for block {i} for user {user.username}'
                else:
                    assert block_structure_result == has_access_result, \
                        f'block structure ({block_structure_result}) & has_access ({has_access_result})' \
                        f' results not equal for block {i} for user {user.username}'

        self.client.logout()


def update_block(block):
    """
    Helper method to update the block in the modulestore
    """
    store = modulestore()
    with store.branch_setting(ModuleStoreEnum.Branch.draft_preferred, block.course_id):
        return store.update_item(block, ModuleStoreEnum.UserID.test)


def publish_course(course):
    """
    Helper method to publish the course (from draft to publish branch)
    """
    store = modulestore()
    with store.branch_setting(ModuleStoreEnum.Branch.draft_preferred, course.id):
        store.publish(course.location, ModuleStoreEnum.UserID.test)


def create_location(org, course, run, block_type, block_id):
    """
    Returns the usage key for the given key parameters using the
    default modulestore
    """
    return modulestore().make_course_key(org, course, run).make_usage_key(block_type, block_id)
