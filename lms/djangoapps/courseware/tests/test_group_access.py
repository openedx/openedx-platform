"""
This module defines tests for courseware.access that are specific to group
access control rules.
"""


import ddt
from stevedore.extension import Extension, ExtensionManager

import lms.djangoapps.courseware.access as access
from common.djangoapps.student.tests.factories import StaffFactory, UserFactory
from xmodule.modulestore.django import modulestore
from xmodule.modulestore.tests.django_utils import TEST_DATA_SPLIT_MODULESTORE, ModuleStoreTestCase
from xmodule.modulestore.tests.factories import BlockFactory, CourseFactory
from xmodule.partitions.partitions import USER_PARTITION_SCHEME_NAMESPACE, Group, UserPartition


class MemoryUserPartitionScheme:
    """
    In-memory partition scheme for testing.
    """
    name = "memory"

    def __init__(self):
        self.current_group = {}

    def set_group_for_user(self, user, user_partition, group):
        """
        Link this user to this group in this partition, in memory.
        """
        self.current_group.setdefault(user.id, {})[user_partition.id] = group

    def get_group_for_user(self, course_id, user, user_partition):  # pylint: disable=unused-argument
        """
        Fetch the group to which this user is linked in this partition, or None.
        """
        return self.current_group.get(user.id, {}).get(user_partition.id)


def resolve_attrs(test_method):
    """
    Helper function used with ddt.  It allows passing strings to test methods
    via @ddt.data, which are the names of instance attributes on `self`, and
    replaces them with the resolved values of those attributes in the method
    call.
    """
    def _wrapper(self, *args):
        new_args = [getattr(self, arg) for arg in args]
        return test_method(self, *new_args)
    return _wrapper


@ddt.ddt
class GroupAccessTestCase(ModuleStoreTestCase):
    """
    Tests to ensure that has_access() correctly enforces the visibility
    restrictions specified in the `group_access` field of XBlocks.
    """
    MODULESTORE = TEST_DATA_SPLIT_MODULESTORE

    def set_user_group(self, user, partition, group):
        """
        Internal DRY / shorthand.
        """
        partition.scheme.set_group_for_user(user, partition, group)

    def set_group_access(self, block_location, access_dict):
        """
        Set group_access on block specified by location.
        """
        block = modulestore().get_item(block_location)
        block.group_access = access_dict
        modulestore().update_item(block, 1)

    def set_user_partitions(self, block_location, partitions):
        """
        Sets the user_partitions on block specified by location.
        """
        block = modulestore().get_item(block_location)
        block.user_partitions = partitions
        modulestore().update_item(block, 1)

    def setUp(self):
        super().setUp()

        UserPartition.scheme_extensions = ExtensionManager.make_test_instance(
            [
                Extension(
                    "memory",
                    USER_PARTITION_SCHEME_NAMESPACE,
                    MemoryUserPartitionScheme(),
                    None
                ),
                Extension(
                    "random",
                    USER_PARTITION_SCHEME_NAMESPACE,
                    MemoryUserPartitionScheme(),
                    None
                )
            ],
            namespace=USER_PARTITION_SCHEME_NAMESPACE
        )

        self.cat_group = Group(10, 'cats')
        self.dog_group = Group(20, 'dogs')
        self.worm_group = Group(30, 'worms')
        self.animal_partition = UserPartition(
            0,
            'Pet Partition',
            'which animal are you?',
            [self.cat_group, self.dog_group, self.worm_group],
            scheme=UserPartition.get_scheme("memory"),
        )

        self.red_group = Group(1000, 'red')
        self.blue_group = Group(2000, 'blue')
        self.gray_group = Group(3000, 'gray')
        self.color_partition = UserPartition(
            100,
            'Color Partition',
            'what color are you?',
            [self.red_group, self.blue_group, self.gray_group],
            scheme=UserPartition.get_scheme("memory"),
        )

        self.course = CourseFactory.create(
            user_partitions=[self.animal_partition, self.color_partition],
        )
        with self.store.bulk_operations(self.course.id, emit_signals=False):
            chapter = BlockFactory.create(category='chapter', parent=self.course)
            section = BlockFactory.create(category='sequential', parent=chapter)
            vertical = BlockFactory.create(category='vertical', parent=section)
            component = BlockFactory.create(category='problem', parent=vertical)

            self.chapter_location = chapter.location
            self.section_location = section.location
            self.vertical_location = vertical.location
            self.component_location = component.location

        self.red_cat = UserFactory()  # student in red and cat groups
        self.set_user_group(self.red_cat, self.animal_partition, self.cat_group)
        self.set_user_group(self.red_cat, self.color_partition, self.red_group)

        self.blue_dog = UserFactory()  # student in blue and dog groups
        self.set_user_group(self.blue_dog, self.animal_partition, self.dog_group)
        self.set_user_group(self.blue_dog, self.color_partition, self.blue_group)

        self.white_mouse = UserFactory()  # student in no group

        self.gray_worm = UserFactory()  # student in deleted group
        self.set_user_group(self.gray_worm, self.animal_partition, self.worm_group)
        self.set_user_group(self.gray_worm, self.color_partition, self.gray_group)
        # delete the gray/worm groups from the partitions now so we can test scenarios
        # for user whose group is missing.
        self.animal_partition.groups.pop()
        self.color_partition.groups.pop()

        # add a staff user, whose access will be unconditional in spite of group access.
        self.staff = StaffFactory.create(course_key=self.course.id)

    # avoid repeatedly declaring the same sequence for ddt in all the test cases.
    PARENT_CHILD_PAIRS = (
        ('chapter_location', 'chapter_location'),
        ('chapter_location', 'section_location'),
        ('chapter_location', 'vertical_location'),
        ('chapter_location', 'component_location'),
        ('section_location', 'section_location'),
        ('section_location', 'vertical_location'),
        ('section_location', 'component_location'),
        ('vertical_location', 'vertical_location'),
        ('vertical_location', 'component_location'),
    )

    def tearDown(self):
        """
        Clear out the stevedore extension points on UserPartition to avoid
        side-effects in other tests.
        """
        UserPartition.scheme_extensions = None
        super().tearDown()

    def check_access(self, user, block_location, is_accessible):
        """
        DRY helper.
        """
        assert bool(access.has_access(user, 'load', modulestore().get_item(block_location), self.course.id))\
               is is_accessible

    def ensure_staff_access(self, block_location):
        """
        Another DRY helper.
        """
        block = modulestore().get_item(block_location)
        assert access.has_access(self.staff, 'load', block, self.course.id)

    # NOTE: in all the tests that follow, `block_specified` and
    # `block_accessed` designate the place where group_access rules are
    # specified, and where access is being checked in the test, respectively.

    @ddt.data(*PARENT_CHILD_PAIRS)
    @ddt.unpack
    @resolve_attrs
    def test_has_access_single_partition_single_group(self, block_specified, block_accessed):
        """
        Access checks are correctly enforced on the block when a single group
        is specified for a single partition.
        """
        self.set_group_access(
            block_specified,
            {self.animal_partition.id: [self.cat_group.id]},
        )
        self.check_access(self.red_cat, block_accessed, True)
        self.check_access(self.blue_dog, block_accessed, False)
        self.check_access(self.white_mouse, block_accessed, False)
        self.check_access(self.gray_worm, block_accessed, False)
        self.ensure_staff_access(block_accessed)

    @ddt.data(*PARENT_CHILD_PAIRS)
    @ddt.unpack
    @resolve_attrs
    def test_has_access_single_partition_two_groups(self, block_specified, block_accessed):
        """
        Access checks are correctly enforced on the block when multiple groups
        are specified for a single partition.
        """
        self.set_group_access(
            block_specified,
            {self.animal_partition.id: [self.cat_group.id, self.dog_group.id]},
        )
        self.check_access(self.red_cat, block_accessed, True)
        self.check_access(self.blue_dog, block_accessed, True)
        self.check_access(self.white_mouse, block_accessed, False)
        self.check_access(self.gray_worm, block_accessed, False)
        self.ensure_staff_access(block_accessed)

    @ddt.data(*PARENT_CHILD_PAIRS)
    @ddt.unpack
    @resolve_attrs
    def test_has_access_single_partition_disjoint_groups(self, block_specified, block_accessed):
        """
        When the parent's and child's group specifications do not intersect,
        access is denied to the child regardless of the user's groups.
        """
        if block_specified == block_accessed:
            # this test isn't valid unless block_accessed is a descendant of
            # block_specified.
            return

        self.set_group_access(
            block_specified,
            {self.animal_partition.id: [self.dog_group.id]},
        )
        self.set_group_access(
            block_accessed,
            {self.animal_partition.id: [self.cat_group.id]},
        )
        self.check_access(self.red_cat, block_accessed, False)
        self.check_access(self.blue_dog, block_accessed, False)
        self.check_access(self.white_mouse, block_accessed, False)
        self.check_access(self.gray_worm, block_accessed, False)
        self.ensure_staff_access(block_accessed)

    @ddt.data(*PARENT_CHILD_PAIRS)
    @ddt.unpack
    @resolve_attrs
    def test_has_access_single_empty_partition(self, block_specified, block_accessed):
        """
        No group access checks are enforced on the block when group_access
        declares a partition but does not specify any groups.
        """
        self.set_group_access(block_specified, {self.animal_partition.id: []})
        self.check_access(self.red_cat, block_accessed, True)
        self.check_access(self.blue_dog, block_accessed, True)
        self.check_access(self.white_mouse, block_accessed, True)
        self.check_access(self.gray_worm, block_accessed, True)
        self.ensure_staff_access(block_accessed)

    @ddt.data(*PARENT_CHILD_PAIRS)
    @ddt.unpack
    @resolve_attrs
    def test_has_access_empty_dict(self, block_specified, block_accessed):
        """
        No group access checks are enforced on the block when group_access is an
        empty dictionary.
        """
        self.set_group_access(block_specified, {})
        self.check_access(self.red_cat, block_accessed, True)
        self.check_access(self.blue_dog, block_accessed, True)
        self.check_access(self.white_mouse, block_accessed, True)
        self.check_access(self.gray_worm, block_accessed, True)
        self.ensure_staff_access(block_accessed)

    @ddt.data(*PARENT_CHILD_PAIRS)
    @ddt.unpack
    @resolve_attrs
    def test_has_access_none(self, block_specified, block_accessed):
        """
        No group access checks are enforced on the block when group_access is None.
        """
        self.set_group_access(block_specified, None)
        self.check_access(self.red_cat, block_accessed, True)
        self.check_access(self.blue_dog, block_accessed, True)
        self.check_access(self.white_mouse, block_accessed, True)
        self.check_access(self.gray_worm, block_accessed, True)
        self.ensure_staff_access(block_accessed)

    @ddt.data(*PARENT_CHILD_PAIRS)
    @ddt.unpack
    @resolve_attrs
    def test_has_access_single_partition_group_none(self, block_specified, block_accessed):
        """
        No group access checks are enforced on the block when group_access
        specifies a partition but its value is None.
        """
        self.set_group_access(block_specified, {self.animal_partition.id: None})
        self.check_access(self.red_cat, block_accessed, True)
        self.check_access(self.blue_dog, block_accessed, True)
        self.check_access(self.white_mouse, block_accessed, True)
        self.check_access(self.gray_worm, block_accessed, True)
        self.ensure_staff_access(block_accessed)

    @ddt.data(*PARENT_CHILD_PAIRS)
    @ddt.unpack
    @resolve_attrs
    def test_has_access_single_partition_group_empty_list(self, block_specified, block_accessed):
        """
        No group access checks are enforced on the block when group_access
        specifies a partition but its value is an empty list.
        """
        self.set_group_access(block_specified, {self.animal_partition.id: []})
        self.check_access(self.red_cat, block_accessed, True)
        self.check_access(self.blue_dog, block_accessed, True)
        self.check_access(self.white_mouse, block_accessed, True)
        self.check_access(self.gray_worm, block_accessed, True)
        self.ensure_staff_access(block_accessed)

    @ddt.data(*PARENT_CHILD_PAIRS)
    @ddt.unpack
    @resolve_attrs
    def test_has_access_nonexistent_nonempty_partition(self, block_specified, block_accessed):
        """
        Access will be denied to the block when group_access specifies a
        nonempty partition that does not exist in course.user_partitions.
        """
        self.set_group_access(block_specified, {9: [99]})
        self.check_access(self.red_cat, block_accessed, False)
        self.check_access(self.blue_dog, block_accessed, False)
        self.check_access(self.white_mouse, block_accessed, False)
        self.check_access(self.gray_worm, block_accessed, False)
        self.ensure_staff_access(block_accessed)

    @ddt.data(*PARENT_CHILD_PAIRS)
    @ddt.unpack
    @resolve_attrs
    def test_has_access_nonexistent_group(self, block_specified, block_accessed):
        """
        Access will be denied to the block when group_access contains a group
        id that does not exist in its referenced partition.
        """
        self.set_group_access(block_specified, {self.animal_partition.id: [99]})
        self.check_access(self.red_cat, block_accessed, False)
        self.check_access(self.blue_dog, block_accessed, False)
        self.check_access(self.white_mouse, block_accessed, False)
        self.check_access(self.gray_worm, block_accessed, False)
        self.ensure_staff_access(block_accessed)

    @ddt.data(*PARENT_CHILD_PAIRS)
    @ddt.unpack
    @resolve_attrs
    def test_multiple_partitions(self, block_specified, block_accessed):
        """
        Group access restrictions are correctly enforced when multiple partition
        / group rules are defined.
        """
        self.set_group_access(
            block_specified,
            {
                self.animal_partition.id: [self.cat_group.id],
                self.color_partition.id: [self.red_group.id],
            },
        )
        self.check_access(self.red_cat, block_accessed, True)
        self.check_access(self.blue_dog, block_accessed, False)
        self.check_access(self.white_mouse, block_accessed, False)
        self.check_access(self.gray_worm, block_accessed, False)
        self.ensure_staff_access(block_accessed)

    @ddt.data(*PARENT_CHILD_PAIRS)
    @ddt.unpack
    @resolve_attrs
    def test_multiple_partitions_deny_access(self, block_specified, block_accessed):
        """
        Group access restrictions correctly deny access even when some (but not
        all) group_access rules are satisfied.
        """
        self.set_group_access(
            block_specified,
            {
                self.animal_partition.id: [self.cat_group.id],
                self.color_partition.id: [self.blue_group.id],
            },
        )
        self.check_access(self.red_cat, block_accessed, False)
        self.check_access(self.blue_dog, block_accessed, False)
        self.check_access(self.gray_worm, block_accessed, False)
        self.ensure_staff_access(block_accessed)
