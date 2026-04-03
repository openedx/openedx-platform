# lint-amnesty, pylint: disable=django-not-configured
"""
Tests of the LMS XBlock Mixin
"""

import ddt
from xblock.validation import ValidationMessage

from lms.djangoapps.lms_xblock.mixin import (
    INVALID_USER_PARTITION_GROUP_VALIDATION_COMPONENT,
    INVALID_USER_PARTITION_GROUP_VALIDATION_UNIT,
    INVALID_USER_PARTITION_VALIDATION_COMPONENT,
    INVALID_USER_PARTITION_VALIDATION_UNIT,
    NONSENSICAL_ACCESS_RESTRICTION,
)
from xmodule.modulestore import ModuleStoreEnum  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.modulestore.tests.django_utils import (  # lint-amnesty, pylint: disable=wrong-import-order
    TEST_DATA_SPLIT_MODULESTORE,
    ModuleStoreTestCase,
)
from xmodule.modulestore.tests.factories import (  # lint-amnesty, pylint: disable=wrong-import-order
    BlockFactory,
    CourseFactory,
    ToyCourseFactory,
)
from xmodule.partitions.partitions import Group, UserPartition  # lint-amnesty, pylint: disable=wrong-import-order


class LmsXBlockMixinTestCase(ModuleStoreTestCase):
    """
    Base class for XBlock mixin tests cases. A simple course with a single user partition is created
    in setUp for all subclasses to use.
    """
    def build_course(self):
        """
        Build up a course tree with a UserPartition.
        """
        # pylint: disable=attribute-defined-outside-init
        self.user_partition = UserPartition(
            0,
            'first_partition',
            'First Partition',
            [
                Group(0, 'alpha'),
                Group(1, 'beta')
            ]
        )
        self.group1 = self.user_partition.groups[0]
        self.group2 = self.user_partition.groups[1]
        self.course = CourseFactory.create(user_partitions=[self.user_partition])
        section = BlockFactory.create(parent=self.course, category='chapter', display_name='Test Section')
        subsection = BlockFactory.create(parent=section, category='sequential', display_name='Test Subsection')
        vertical = BlockFactory.create(parent=subsection, category='vertical', display_name='Test Unit')
        video = BlockFactory.create(parent=vertical, category='video', display_name='Test Video 1')
        split_test = BlockFactory.create(parent=vertical, category='split_test', display_name='Test Content Experiment')
        child_vertical = BlockFactory.create(parent=split_test, category='vertical')
        child_html_block = BlockFactory.create(parent=child_vertical, category='html')
        self.section_location = section.location
        self.subsection_location = subsection.location
        self.vertical_location = vertical.location
        self.video_location = video.location
        self.split_test_location = split_test.location
        self.child_vertical_location = child_vertical.location
        self.child_html_block_location = child_html_block.location

    def set_group_access(self, block_location, access_dict):
        """
        Sets the group_access dict on the block referenced by block_location.
        """
        block = self.store.get_item(block_location)
        block.group_access = access_dict
        self.store.update_item(block, 1)


class XBlockValidationTest(LmsXBlockMixinTestCase):
    """
    Unit tests for XBlock validation
    """
    def setUp(self):
        super().setUp()
        self.build_course()

    def verify_validation_message(self, message, expected_message, expected_message_type):
        """
        Verify that the validation message has the expected validation message and type.
        """
        assert message.text == expected_message
        assert message.type == expected_message_type

    def test_validate_full_group_access(self):
        """
        Test the validation messages produced for an xblock with full group access.
        """
        validation = self.store.get_item(self.video_location).validate()
        assert len(validation.messages) == 0

    def test_validate_restricted_group_access(self):
        """
        Test the validation messages produced for an xblock with a valid group access restriction
        """
        self.set_group_access(self.video_location, {self.user_partition.id: [self.group1.id, self.group2.id]})
        validation = self.store.get_item(self.video_location).validate()
        assert len(validation.messages) == 0

    def test_validate_invalid_user_partitions(self):
        """
        Test the validation messages produced for a component referring to non-existent user partitions.
        """
        self.set_group_access(self.video_location, {999: [self.group1.id]})
        validation = self.store.get_item(self.video_location).validate()
        assert len(validation.messages) == 1
        self.verify_validation_message(
            validation.messages[0],
            INVALID_USER_PARTITION_VALIDATION_COMPONENT,
            ValidationMessage.ERROR,
        )

        # Now add a second invalid user partition and validate again.
        # Note that even though there are two invalid configurations,
        # only a single error message will be returned.
        self.set_group_access(self.video_location, {998: [self.group2.id]})
        validation = self.store.get_item(self.video_location).validate()
        assert len(validation.messages) == 1
        self.verify_validation_message(
            validation.messages[0],
            INVALID_USER_PARTITION_VALIDATION_COMPONENT,
            ValidationMessage.ERROR,
        )

    def test_validate_invalid_user_partitions_unit(self):
        """
        Test the validation messages produced for a unit referring to non-existent user partitions.
        """
        self.set_group_access(self.vertical_location, {999: [self.group1.id]})
        validation = self.store.get_item(self.vertical_location).validate()
        assert len(validation.messages) == 1
        self.verify_validation_message(
            validation.messages[0],
            INVALID_USER_PARTITION_VALIDATION_UNIT,
            ValidationMessage.ERROR,
        )

        # Now add a second invalid user partition and validate again.
        # Note that even though there are two invalid configurations,
        # only a single error message will be returned.
        self.set_group_access(self.vertical_location, {998: [self.group2.id]})
        validation = self.store.get_item(self.vertical_location).validate()
        assert len(validation.messages) == 1
        self.verify_validation_message(
            validation.messages[0],
            INVALID_USER_PARTITION_VALIDATION_UNIT,
            ValidationMessage.ERROR,
        )

    def test_validate_invalid_groups(self):
        """
        Test the validation messages produced for an xblock referring to non-existent groups.
        """
        self.set_group_access(self.video_location, {self.user_partition.id: [self.group1.id, 999]})
        validation = self.store.get_item(self.video_location).validate()
        assert len(validation.messages) == 1
        self.verify_validation_message(
            validation.messages[0],
            INVALID_USER_PARTITION_GROUP_VALIDATION_COMPONENT,
            ValidationMessage.ERROR,
        )

        # Now try again with two invalid group ids
        self.set_group_access(self.video_location, {self.user_partition.id: [self.group1.id, 998, 999]})
        validation = self.store.get_item(self.video_location).validate()
        assert len(validation.messages) == 1
        self.verify_validation_message(
            validation.messages[0],
            INVALID_USER_PARTITION_GROUP_VALIDATION_COMPONENT,
            ValidationMessage.ERROR,
        )

    def test_validate_nonsensical_access_for_split_test_children(self):
        """
        Test the validation messages produced for components within
        a content group experiment (also known as a split_test).
        Ensures that children of split_test xblocks only validate
        their access settings off the parent, rather than any
        grandparent.
        """
        # Test that no validation message is displayed on split_test child when child agrees with parent
        self.set_group_access(self.vertical_location, {self.user_partition.id: [self.group1.id]})
        self.set_group_access(self.split_test_location, {self.user_partition.id: [self.group2.id]})
        self.set_group_access(self.child_vertical_location, {self.user_partition.id: [self.group2.id]})
        self.set_group_access(self.child_html_block_location, {self.user_partition.id: [self.group2.id]})
        validation = self.store.get_item(self.child_html_block_location).validate()
        assert len(validation.messages) == 0

        # Test that a validation message is displayed on split_test child when the child contradicts the parent,
        # even though the child agrees with the grandparent unit.
        self.set_group_access(self.child_html_block_location, {self.user_partition.id: [self.group1.id]})
        validation = self.store.get_item(self.child_html_block_location).validate()
        assert len(validation.messages) == 1
        self.verify_validation_message(
            validation.messages[0],
            NONSENSICAL_ACCESS_RESTRICTION,
            ValidationMessage.ERROR,
        )

    def test_validate_invalid_groups_for_unit(self):
        """
        Test the validation messages produced for a unit-level xblock referring to non-existent groups.
        """
        self.set_group_access(self.vertical_location, {self.user_partition.id: [self.group1.id, 999]})
        validation = self.store.get_item(self.vertical_location).validate()
        assert len(validation.messages) == 1
        self.verify_validation_message(
            validation.messages[0],
            INVALID_USER_PARTITION_GROUP_VALIDATION_UNIT,
            ValidationMessage.ERROR,
        )

    def test_validate_nonsensical_access_restriction(self):
        """
        Test the validation messages produced for a component whose
        access settings contradict the unit level access.
        """
        # Test that there is no validation message for non-contradicting access restrictions
        self.set_group_access(self.vertical_location, {self.user_partition.id: [self.group1.id]})
        self.set_group_access(self.video_location, {self.user_partition.id: [self.group1.id]})
        validation = self.store.get_item(self.video_location).validate()
        assert len(validation.messages) == 0

        # Now try again with opposing access restrictions
        self.set_group_access(self.vertical_location, {self.user_partition.id: [self.group1.id]})
        self.set_group_access(self.video_location, {self.user_partition.id: [self.group2.id]})
        validation = self.store.get_item(self.video_location).validate()
        assert len(validation.messages) == 1
        self.verify_validation_message(
            validation.messages[0],
            NONSENSICAL_ACCESS_RESTRICTION,
            ValidationMessage.ERROR,
        )

        # Now try again when the component restricts access to additional groups that the unit does not
        self.set_group_access(self.vertical_location, {self.user_partition.id: [self.group1.id]})
        self.set_group_access(self.video_location, {self.user_partition.id: [self.group1.id, self.group2.id]})
        validation = self.store.get_item(self.video_location).validate()
        assert len(validation.messages) == 1
        self.verify_validation_message(
            validation.messages[0],
            NONSENSICAL_ACCESS_RESTRICTION,
            ValidationMessage.ERROR,
        )

        # Now try again when the component tries to allow access to all learners and staff
        self.set_group_access(self.vertical_location, {self.user_partition.id: [self.group1.id]})
        self.set_group_access(self.video_location, {})
        validation = self.store.get_item(self.video_location).validate()
        assert len(validation.messages) == 1
        self.verify_validation_message(
            validation.messages[0],
            NONSENSICAL_ACCESS_RESTRICTION,
            ValidationMessage.ERROR,
        )

    def test_nonsensical_access_restriction_does_not_override(self):
        """
        Test that the validation message produced for a component
        whose access settings contradict the unit level access don't
        override other messages but add on to them.
        """
        self.set_group_access(self.vertical_location, {self.user_partition.id: [self.group1.id]})
        self.set_group_access(self.video_location, {self.user_partition.id: [self.group2.id, 999]})
        validation = self.store.get_item(self.video_location).validate()
        assert len(validation.messages) == 2
        self.verify_validation_message(
            validation.messages[0],
            INVALID_USER_PARTITION_GROUP_VALIDATION_COMPONENT,
            ValidationMessage.ERROR,
        )
        self.verify_validation_message(
            validation.messages[1],
            NONSENSICAL_ACCESS_RESTRICTION,
            ValidationMessage.ERROR,
        )


class OpenAssessmentBlockMixinTestCase(ModuleStoreTestCase):
    """
    Tests for OpenAssessmentBlock mixin.
    """

    def setUp(self):
        super().setUp()
        self.course = CourseFactory.create()
        self.section = BlockFactory.create(parent=self.course, category='chapter', display_name='Test Section')
        self.open_assessment = BlockFactory.create(
            parent=self.section,
            category="openassessment",
            display_name="untitled",
        )

    def test_has_score(self):
        """
        Test has_score is true for ora2 problems.
        """
        assert self.open_assessment.has_score


class XBlockGetParentTest(LmsXBlockMixinTestCase):
    """
    Test that XBlock.get_parent returns correct results with each modulestore
    backend.
    """
    MODULESTORE = TEST_DATA_SPLIT_MODULESTORE

    def test_parents(self):
        with self.store.default_store(ModuleStoreEnum.Type.split):

            # setting up our own local course tree here, since it needs to be
            # created with the correct modulestore type.

            course_key = ToyCourseFactory.create().id
            course = self.store.get_course(course_key)
            assert course.get_parent() is None

            def recurse(parent):
                """
                Descend the course tree and ensure the result of get_parent()
                is the expected one.
                """
                visited = []
                for child in parent.get_children():
                    assert parent.location == child.get_parent().location
                    visited.append(child)
                    visited += recurse(child)
                return visited

            visited = recurse(course)
            assert len(visited) == 28

    def test_parents_draft_content(self):
        # move the video to the new vertical
        with self.store.default_store(ModuleStoreEnum.Type.split):
            self.build_course()
            subsection = self.store.get_item(self.subsection_location)
            new_vertical = BlockFactory.create(parent=subsection, category='vertical', display_name='New Test Unit')
            child_to_move_location = self.video_location.for_branch(None)
            new_parent_location = new_vertical.location.for_branch(None)
            old_parent_location = self.vertical_location.for_branch(None)

            with self.store.branch_setting(ModuleStoreEnum.Branch.draft_preferred):
                assert self.course.get_parent() is None

                with self.store.bulk_operations(self.course.id):
                    user_id = ModuleStoreEnum.UserID.test

                    old_parent = self.store.get_item(old_parent_location)
                    old_parent.children.remove(child_to_move_location)
                    self.store.update_item(old_parent, user_id)

                    new_parent = self.store.get_item(new_parent_location)
                    new_parent.children.append(child_to_move_location)
                    self.store.update_item(new_parent, user_id)

                    # re-fetch video from draft store
                    video = self.store.get_item(child_to_move_location)

                    assert new_parent_location == video.get_parent().location
            with self.store.branch_setting(ModuleStoreEnum.Branch.published_only):
                # re-fetch video from published store
                video = self.store.get_item(child_to_move_location)
                assert old_parent_location == video.get_parent().location.for_branch(None)


class RenamedTuple(tuple):
    """
    This class is only used to allow overriding __name__ on the tuples passed
    through ddt, in order to have the generated test names make sense.
    """
    pass  # lint-amnesty, pylint: disable=unnecessary-pass


def ddt_named(parent, child):
    """
    Helper to get more readable dynamically-generated test names from ddt.
    """
    args = RenamedTuple([parent, child])
    args.__name__ = f'parent_{parent}_child_{child}'      # pylint: disable=attribute-defined-outside-init
    return args


@ddt.ddt
class XBlockMergedGroupAccessTest(LmsXBlockMixinTestCase):
    """
    Test that XBlock.merged_group_access is computed correctly according to
    our access control rules.
    """

    PARTITION_1 = 1
    PARTITION_1_GROUP_1 = 11
    PARTITION_1_GROUP_2 = 12

    PARTITION_2 = 2
    PARTITION_2_GROUP_1 = 21
    PARTITION_2_GROUP_2 = 22

    PARENT_CHILD_PAIRS = (
        ddt_named('section_location', 'subsection_location'),
        ddt_named('section_location', 'vertical_location'),
        ddt_named('section_location', 'video_location'),
        ddt_named('subsection_location', 'vertical_location'),
        ddt_named('subsection_location', 'video_location'),
    )

    def setUp(self):
        super().setUp()
        self.build_course()

    def verify_group_access(self, block_location, expected_dict):
        """
        Verify the expected value for the block's group_access.
        """
        block = self.store.get_item(block_location)
        assert block.merged_group_access == expected_dict

    @ddt.data(*PARENT_CHILD_PAIRS)
    @ddt.unpack
    def test_intersecting_groups(self, parent, child):
        """
        When merging group_access on a block, the resulting group IDs for each
        partition is the intersection of the group IDs defined for that
        partition across all ancestor blocks (including this one).
        """
        parent_block = getattr(self, parent)
        child_block = getattr(self, child)

        self.set_group_access(parent_block, {self.PARTITION_1: [self.PARTITION_1_GROUP_1, self.PARTITION_1_GROUP_2]})
        self.set_group_access(child_block, {self.PARTITION_1: [self.PARTITION_1_GROUP_2]})

        self.verify_group_access(parent_block, {self.PARTITION_1: [self.PARTITION_1_GROUP_1, self.PARTITION_1_GROUP_2]})
        self.verify_group_access(child_block, {self.PARTITION_1: [self.PARTITION_1_GROUP_2]})

    @ddt.data(*PARENT_CHILD_PAIRS)
    @ddt.unpack
    def test_disjoint_groups(self, parent, child):
        """
        When merging group_access on a block, if the intersection of group IDs
        for a partition is empty, the merged value for that partition is False.
        """
        parent_block = getattr(self, parent)
        child_block = getattr(self, child)

        self.set_group_access(parent_block, {self.PARTITION_1: [self.PARTITION_1_GROUP_1]})
        self.set_group_access(child_block, {self.PARTITION_1: [self.PARTITION_1_GROUP_2]})

        self.verify_group_access(parent_block, {self.PARTITION_1: [self.PARTITION_1_GROUP_1]})
        self.verify_group_access(child_block, {self.PARTITION_1: False})

    def test_disjoint_groups_no_override(self):
        """
        Special case of the above test - ensures that `False` propagates down
        to the block being queried even if blocks further down in the hierarchy
        try to override it.
        """
        self.set_group_access(self.section_location, {self.PARTITION_1: [self.PARTITION_1_GROUP_1]})
        self.set_group_access(self.subsection_location, {self.PARTITION_1: [self.PARTITION_1_GROUP_2]})
        self.set_group_access(
            self.vertical_location, {self.PARTITION_1: [self.PARTITION_1_GROUP_1, self.PARTITION_1_GROUP_2]}
        )

        self.verify_group_access(self.vertical_location, {self.PARTITION_1: False})
        self.verify_group_access(self.video_location, {self.PARTITION_1: False})

    @ddt.data(*PARENT_CHILD_PAIRS)
    @ddt.unpack
    def test_union_partitions(self, parent, child):
        """
        When merging group_access on a block, the result's keys (partitions)
        are the union of all partitions specified across all ancestor blocks
        (including this one).
        """
        parent_block = getattr(self, parent)
        child_block = getattr(self, child)

        self.set_group_access(parent_block, {self.PARTITION_1: [self.PARTITION_1_GROUP_1]})
        self.set_group_access(child_block, {self.PARTITION_2: [self.PARTITION_1_GROUP_2]})

        self.verify_group_access(parent_block, {self.PARTITION_1: [self.PARTITION_1_GROUP_1]})
        self.verify_group_access(
            child_block, {self.PARTITION_1: [self.PARTITION_1_GROUP_1], self.PARTITION_2: [self.PARTITION_1_GROUP_2]}
        )
