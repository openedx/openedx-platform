# pylint: disable=attribute-defined-outside-init, protected-access
"""
Tests for UserPartitionTransformer.
"""


import string
from collections import namedtuple
from datetime import datetime
from unittest.mock import patch

import ddt

from common.djangoapps.course_modes.tests.factories import CourseModeFactory
from common.djangoapps.student.tests.factories import CourseEnrollmentFactory
from openedx.core.djangoapps.course_groups.cohorts import add_user_to_cohort
from openedx.core.djangoapps.course_groups.partition_scheme import CohortPartitionScheme
from openedx.core.djangoapps.course_groups.tests.helpers import CohortFactory, config_course_cohorts
from openedx.core.djangoapps.course_groups.views import link_cohort_to_partition_group
from openedx.features.content_type_gating.models import ContentTypeGatingConfig
from openedx.features.content_type_gating.partitions import create_content_gating_partition
from xmodule.modulestore.tests.factories import CourseFactory  # pylint: disable=wrong-import-order
from xmodule.partitions.partitions import Group, UserPartition  # pylint: disable=wrong-import-order

from ...api import get_course_blocks
from ..user_partitions import UserPartitionTransformer, _MergedGroupAccess
from .helpers import CourseStructureTestCase, update_block


class UserPartitionTestMixin:
    """
    Helper Mixin for testing user partitions.
    """
    TRANSFORMER_CLASS_TO_TEST = UserPartitionTransformer

    def setup_groups_partitions(self, num_user_partitions=1, num_groups=4, active=True):
        """
        Sets up groups and user partitions for testing.
        """
        # Set up groups
        self.groups = []
        for group_num in range(1, num_groups + 1):
            self.groups.append(Group(group_num, 'Group ' + str(group_num)))

        # Set up user partitions
        self.user_partitions = []
        for user_partition_num in range(1, num_user_partitions + 1):
            user_partition = UserPartition(
                id=user_partition_num,
                name='Partition ' + str(user_partition_num),
                description='This is partition ' + str(user_partition_num),
                groups=self.groups,
                scheme=CohortPartitionScheme,
                active=active,
            )
            user_partition.scheme.name = "cohort"
            self.user_partitions.append(user_partition)

    def setup_cohorts(self, course):
        """
        Sets up a cohort for each previously created user partition.
        """
        config_course_cohorts(course, is_cohorted=True)
        self.partition_cohorts = []
        for user_partition in self.user_partitions:
            partition_cohorts = []
            for group in self.groups:
                cohort = CohortFactory(course_id=course.id)
                partition_cohorts.append(cohort)
                link_cohort_to_partition_group(
                    cohort,
                    user_partition.id,
                    group.id,
                )
            self.partition_cohorts.append(partition_cohorts)


@ddt.ddt
class UserPartitionTransformerTestCase(UserPartitionTestMixin, CourseStructureTestCase):
    """
    UserPartitionTransformer Test
    """

    def setup_partitions_and_course(self, active=True):
        """
        Setup course structure and create user for user partition
        transformer test.
        Args:
            active: boolean representing if the user partitions are
            active or not
        """
        # Set up user partitions and groups.
        self.setup_groups_partitions(active=active)
        self.user_partition = self.user_partitions[0]

        # Build course.
        self.course_hierarchy = self.get_course_hierarchy()
        self.blocks = self.build_course(self.course_hierarchy)
        self.course = self.blocks['course']

        # Enroll user in course.
        CourseEnrollmentFactory.create(
            user=self.user, course_id=self.course.id, is_active=True
        )

        # Set up cohorts.
        self.setup_cohorts(self.course)

    def get_course_hierarchy(self):
        """
        Returns a course hierarchy to test with.
        """
        #                                     course
        #                                  /          \
        #                                 /            \
        #                      A[1, 2, 3]                B
        #                     /  |      \                |
        #                   /    |       \               |
        #                 /      |        \              |
        #           C[1, 2]    D[2, 3]     E            /
        #         / |     \      |        / \          /
        #        /  |     \      |       /   \        /
        #       /   |     \      |      /     \      /
        #     F   G[1]   H[2]    I     J     K[4]   /
        #        /   \    /                  /  \  /
        #       /    \   /                  /   \ /
        #      /     \  /                  /    \/
        #  L[1, 2]  M[1, 2, 3]            N     O
        #
        return [
            {
                'org': 'UserPartitionTransformer',
                'course': 'UP101F',
                'run': 'test_run',
                'user_partitions': [self.user_partition],
                '#type': 'course',
                '#ref': 'course',
                '#children': [
                    {
                        '#type': 'vertical',
                        '#ref': 'A',
                        'metadata': {'group_access': {self.user_partition.id: [0, 1, 2, 3]}},
                    },
                    {'#type': 'vertical', '#ref': 'B'},
                ],
            },
            {
                '#type': 'vertical',
                '#ref': 'C',
                '#parents': ['A'],
                'metadata': {'group_access': {self.user_partition.id: [1, 2]}},
                '#children': [
                    {'#type': 'vertical', '#ref': 'F'},
                    {
                        '#type': 'vertical',
                        '#ref': 'G',
                        'metadata': {'group_access': {self.user_partition.id: [1]}},
                    },
                    {
                        '#type': 'vertical',
                        '#ref': 'H',
                        'metadata': {'group_access': {self.user_partition.id: [2]}},
                    },
                ],
            },
            {
                '#type': 'vertical',
                '#ref': 'D',
                '#parents': ['A'],
                'metadata': {'group_access': {self.user_partition.id: [2, 3]}},
                '#children': [{'#type': 'vertical', '#ref': 'I'}],
            },
            {
                '#type': 'vertical',
                '#ref': 'E',
                '#parents': ['A'],
                '#children': [{'#type': 'vertical', '#ref': 'J'}],
            },
            {
                '#type': 'vertical',
                '#ref': 'K',
                '#parents': ['E'],
                'metadata': {'group_access': {self.user_partition.id: [4, 51]}},
                '#children': [{'#type': 'vertical', '#ref': 'N'}],
            },
            {
                '#type': 'vertical',
                '#ref': 'L',
                '#parents': ['G'],
                'metadata': {'group_access': {self.user_partition.id: [1, 2]}},
            },
            {
                '#type': 'vertical',
                '#ref': 'M',
                '#parents': ['G', 'H'],
                'metadata': {'group_access': {self.user_partition.id: [1, 2, 3]}},
            },
            {
                '#type': 'vertical',
                '#ref': 'O',
                '#parents': ['K', 'B'],
            },
        ]

    @ddt.data(
        (None, ('course', 'B', 'O')),
        (1, ('course', 'A', 'B', 'C', 'E', 'F', 'G', 'J', 'L', 'M', 'O')),
        (2, ('course', 'A', 'B', 'C', 'D', 'E', 'F', 'H', 'I', 'J', 'M', 'O')),
        (3, ('course', 'A', 'B', 'D', 'E', 'I', 'J', 'O')),
        (4, ('course', 'B', 'O')),
    )
    @ddt.unpack
    def test_transform(self, group_id, expected_blocks):
        self.setup_partitions_and_course()
        if group_id:
            cohort = self.partition_cohorts[self.user_partition.id - 1][group_id - 1]
            add_user_to_cohort(cohort, self.user.username)

        trans_block_structure = get_course_blocks(
            self.user,
            self.course.location,
            self.transformers,
        )
        self.assertSetEqual(  # noqa: PT009
            set(trans_block_structure.get_block_keys()),
            self.get_block_key_set(self.blocks, *expected_blocks)
        )

    def test_transform_with_content_gating_partition(self):
        self.setup_partitions_and_course()
        CourseModeFactory.create(course_id=self.course.id, mode_slug='audit')
        CourseModeFactory.create(course_id=self.course.id, mode_slug='verified')
        ContentTypeGatingConfig.objects.create(enabled=True, enabled_as_of=datetime(2018, 1, 1))
        partition = create_content_gating_partition(self.course)
        self.user_partitions.append(partition)
        cohort = self.partition_cohorts[0][1]
        add_user_to_cohort(cohort, self.user.username)

        with patch(
            'lms.djangoapps.course_blocks.transformers.user_partitions.get_partition_from_id',
            return_value=partition
        ), patch(
            'lms.djangoapps.course_blocks.transformers.user_partitions._MergedGroupAccess.get_allowed_groups',
            return_value={51: set()}
        ):
            trans_block_structure = get_course_blocks(
                self.user,
                self.course.location,
                self.transformers,
            )
            xblocks_denial_reason = [trans_block_structure.get_xblock_field(b, 'authorization_denial_reason')
                                     for b in trans_block_structure.get_block_keys()]
            self.assertSetEqual(set(xblocks_denial_reason), {'Feature-based Enrollments'})  # noqa: PT009

    def test_transform_on_inactive_partition(self):
        """
        Tests UserPartitionTransformer for inactive UserPartition.
        """
        self.setup_partitions_and_course(active=False)

        # we expect to find all blocks because the UserPartitions are all
        # inactive
        expected_blocks = ('course',) + tuple(string.ascii_uppercase[:15])

        trans_block_structure = get_course_blocks(
            self.user,
            self.course.location,
            self.transformers,
        )

        self.assertSetEqual(  # noqa: PT009
            set(trans_block_structure.get_block_keys()),
            self.get_block_key_set(self.blocks, *expected_blocks)
        )


@ddt.ddt
class MergedGroupAccessTestData(UserPartitionTestMixin, CourseStructureTestCase):
    """
    _MergedGroupAccess Test
    """

    def setUp(self):
        """
        Setup course structure and create user for user partition
        transformer test.
        """
        super().setUp()

        # Set up multiple user partitions and groups.
        self.setup_groups_partitions(num_user_partitions=3)

        self.course = CourseFactory.create(user_partitions=self.user_partitions)
        CourseEnrollmentFactory.create(user=self.user, course_id=self.course.id, is_active=True)

        # Set up cohorts.
        self.setup_cohorts(self.course)

    def get_course_hierarchy(self):
        """
        Returns a course hierarchy to test with.
        """
        # The block tree is as follows, with the numbers in the brackets
        # specifying the group_id for each of the 3 partitions.
        #               A
        #        /      |    \
        #       /       |     \
        #     B         C      D
        # [1][3][]  [2][2][]  [3][1][]
        #      \    /
        #       \  /
        #         E
        #
        return [
            {
                'org': 'MergedGroupAccess',
                'course': 'MGA101F',
                'run': 'test_run',
                'user_partitions': self.user_partitions,
                '#type': 'course',
                '#ref': 'A',
            },
            {
                '#type': 'vertical',
                '#ref': 'B',
                '#parents': ['A'],
                'metadata': {'group_access': {1: [1], 2:[3], 3:[]}},
            },
            {
                '#type': 'vertical',
                '#ref': 'C',
                '#parents': ['A'],
                'metadata': {'group_access': {1: [2], 2:[2], 3:[]}},
            },
            {
                '#type': 'vertical',
                '#ref': 'D',
                '#parents': ['A'],
                'metadata': {'group_access': {1: [3], 2:[1], 3:[]}},
            },
            {
                '#type': 'vertical',
                '#ref': 'E',
                '#parents': ['B', 'C'],
            },
        ]

    AccessTestData = namedtuple(
        'AccessTestData',
        ['partition_groups', 'xblock_access', 'merged_parents_list', 'expected_access'],
    )
    AccessTestData.__new__.__defaults__ = ({}, None, [], False)

    @ddt.data(
        # universal access throughout
        AccessTestData(expected_access=True),
        AccessTestData(xblock_access={1: None}, expected_access=True),
        AccessTestData(xblock_access={1: []}, expected_access=True),

        # partition 1 requiring membership in group 1
        AccessTestData(xblock_access={1: [1]}),
        AccessTestData(partition_groups={2: 1, 3: 1}, xblock_access={1: [1]}),
        AccessTestData(partition_groups={1: 1, 2: 1, 3: 1}, xblock_access={1: [1]}, expected_access=True),
        AccessTestData(partition_groups={1: 1, 2: 1}, xblock_access={1: [1], 2: [], 3: []}, expected_access=True),

        # partitions 1 and 2 requiring membership in group 1
        AccessTestData(xblock_access={1: [1], 2: [1]}),
        AccessTestData(partition_groups={2: 1, 3: 1}, xblock_access={1: [1], 2: [1]}),
        AccessTestData(partition_groups={1: 1, 2: 1}, xblock_access={1: [1], 2: [1]}, expected_access=True),

        # partitions 1 and 2 requiring membership in different groups
        AccessTestData(xblock_access={1: [1], 2: [2]}),
        AccessTestData(partition_groups={2: 1, 3: 1}, xblock_access={1: [1], 2: [2]}),
        AccessTestData(partition_groups={1: 1, 2: 1, 3: 1}, xblock_access={1: [1], 2: [2]}),

        AccessTestData(partition_groups={1: 1, 2: 2}, xblock_access={1: [1], 2: [2]}, expected_access=True),

        # partitions 1 and 2 requiring membership in list of groups
        AccessTestData(partition_groups={1: 3, 2: 3}, xblock_access={1: [1, 2], 2: [1, 2]}),

        AccessTestData(partition_groups={1: 1, 2: 1}, xblock_access={1: [1, 2], 2: [1, 2]}, expected_access=True),
        AccessTestData(partition_groups={1: 1, 2: 2}, xblock_access={1: [1, 2], 2: [1, 2]}, expected_access=True),
        AccessTestData(partition_groups={1: 2, 2: 1}, xblock_access={1: [1, 2], 2: [1, 2]}, expected_access=True),
        AccessTestData(partition_groups={1: 2, 2: 2}, xblock_access={1: [1, 2], 2: [1, 2]}, expected_access=True),

        # parent inheritance
        #   1 parent allows
        AccessTestData(partition_groups={1: 1, 2: 2}, merged_parents_list=[{1: {1}}], expected_access=True),

        #   2 parents allow
        AccessTestData(partition_groups={1: 1, 2: 2}, merged_parents_list=[{1: {1}}, {1: {1}}], expected_access=True),
        AccessTestData(partition_groups={1: 1, 2: 2}, merged_parents_list=[{1: {2}}, {1: {1}}], expected_access=True),
        AccessTestData(
            partition_groups={1: 1, 2: 2},
            merged_parents_list=[{1: {2}, 2: {2}}, {1: {1}, 2: {1}}],
            expected_access=True,
        ),

        #   1 parent denies
        AccessTestData(partition_groups={1: 1, 2: 2}, merged_parents_list=[{1: {}}]),
        AccessTestData(partition_groups={1: 1, 2: 2}, merged_parents_list=[{1: {3}}]),

        #   1 parent denies, 1 parent allows all
        AccessTestData(partition_groups={1: 1, 2: 2}, merged_parents_list=[{1: {}}, {}], expected_access=True),
        AccessTestData(partition_groups={1: 1, 2: 2}, merged_parents_list=[{1: {}}, {1: {}}, {}], expected_access=True),
        AccessTestData(partition_groups={1: 1, 2: 2}, merged_parents_list=[{1: {}}, {}, {1: {}}], expected_access=True),

        #   1 parent denies, 1 parent allows
        AccessTestData(partition_groups={1: 1, 2: 2}, merged_parents_list=[{1: {3}}, {1: {1}}], expected_access=True),

        #   2 parents deny
        AccessTestData(partition_groups={1: 1, 2: 2}, merged_parents_list=[{1: {}}, {1: {}}]),
        AccessTestData(partition_groups={1: 1, 2: 2}, merged_parents_list=[{1: {3}}, {1: {3}, 2: {2}}]),

        # intersect with parent
        #   child denies, 1 parent allows
        AccessTestData(partition_groups={1: 1, 2: 2}, xblock_access={1: [3]}, merged_parents_list=[{1: {1}}]),
        AccessTestData(partition_groups={1: 1, 2: 2}, xblock_access={1: [2]}, merged_parents_list=[{1: {1}}]),

        #  child denies, 2 parents allow
        AccessTestData(partition_groups={1: 1, 2: 2}, xblock_access={1: [3]}, merged_parents_list=[{1: {1}}, {2: {2}}]),
        AccessTestData(partition_groups={1: 1, 2: 2}, xblock_access={2: [3]}, merged_parents_list=[{1: {1}}, {2: {2}}]),

        #   child allows, 1 parent denies
        AccessTestData(partition_groups={1: 1, 2: 2}, xblock_access={2: [2]}, merged_parents_list=[{1: {}}]),
        AccessTestData(partition_groups={1: 1, 2: 2}, xblock_access={1: [1]}, merged_parents_list=[{1: {2}}]),
        AccessTestData(partition_groups={1: 1, 2: 2}, xblock_access={2: [2]}, merged_parents_list=[{1: {2}}]),

        #   child allows, 1 parent allows
        AccessTestData(
            partition_groups={1: 1, 2: 2},
            xblock_access={1: [1]},
            merged_parents_list=[{}],
            expected_access=True,
        ),
        AccessTestData(
            partition_groups={1: 1, 2: 2}, xblock_access={2: [2]}, merged_parents_list=[{1: {1}}], expected_access=True
        ),
        AccessTestData(
            partition_groups={1: 1, 2: 2},
            xblock_access={1: [1, 3], 2: [2, 3]},
            merged_parents_list=[{1: {1, 2, 3}}, {2: {1, 2, 3}}],
            expected_access=True,
        ),

        #   child allows, 1 parent allows, 1 parent denies
        AccessTestData(
            partition_groups={1: 1, 2: 2},
            xblock_access={1: [1]},
            merged_parents_list=[{1: {3}}, {1: {1}}],
            expected_access=True,
        ),
    )
    @ddt.unpack
    def test_merged_group_access(self, user_partition_groups, xblock_access, merged_parents_list, expected_access):
        # use the course as the block to test
        block = self.course

        # update block access
        if xblock_access is not None:
            block.group_access = xblock_access
            update_block(self.course)

        # convert merged_parents_list to _MergedGroupAccess objects
        for ind, merged_parent in enumerate(merged_parents_list):
            converted_object = _MergedGroupAccess([], block, [])
            converted_object._access = merged_parent
            merged_parents_list[ind] = converted_object

        merged_group_access = _MergedGroupAccess(self.user_partitions, block, merged_parents_list)

        # convert group_id to groups in user_partition_groups parameter
        for partition_id, group_id in user_partition_groups.items():
            user_partition_groups[partition_id] = self.groups[group_id - 1]

        assert merged_group_access.check_group_access(user_partition_groups) == expected_access

    @ddt.data(
        ([None], None),
        ([{1}, None], {1}),
        ([None, {1}], {1}),
        ([None, {1}, {1, 2}], {1}),
        ([None, {1, 2}, {1, 2}], {1, 2}),
        ([{1, 2, 3}, {1, 2}, None], {1, 2}),
        ([{1, 2}, {1, 2, 3, 4}, None], {1, 2}),
        ([{1}, {2}, None], set()),
        ([None, {1}, {2}, None], set()),
    )
    @ddt.unpack
    def test_intersection_method(self, input_value, expected_result):
        assert _MergedGroupAccess._intersection(*input_value) == expected_result
