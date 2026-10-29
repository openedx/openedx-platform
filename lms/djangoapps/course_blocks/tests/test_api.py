# pylint: disable=attribute-defined-outside-init
"""
Tests for course_blocks API
"""

from unittest.mock import Mock, patch

import ddt
from django.http.request import HttpRequest
from django.test import override_settings
from openedx_filters import PipelineStep

from common.djangoapps.student.tests.factories import UserFactory
from lms.djangoapps.course_blocks.api import get_course_blocks
from lms.djangoapps.course_blocks.transformers.load_override_data import OverrideDataTransformer
from lms.djangoapps.course_blocks.transformers.tests.helpers import CourseStructureTestCase
from lms.djangoapps.course_blocks.transformers.tests.test_user_partitions import UserPartitionTestMixin
from lms.djangoapps.courseware.block_render import make_track_function, prepare_runtime_for_user
from openedx.core.djangoapps.content.block_structure.api import get_block_structure_manager
from openedx.core.djangoapps.content.block_structure.transformers import BlockStructureTransformers
from openedx.core.djangoapps.course_groups.cohorts import add_user_to_cohort
from xmodule.modulestore.django import modulestore

FILTERED_USERNAME = 'filtered_user'


class TestOverrideDisplayNameStep(PipelineStep):
    """
    Pipeline step that overrides the course display name for a single user.
    """

    def run_filter(self, block_structure, user, starting_block_usage_key):  # pylint: disable=arguments-differ
        if user.username == FILTERED_USERNAME:
            block_structure.override_xblock_field(starting_block_usage_key, 'display_name', 'Changed')
        return {'block_structure': block_structure}


def get_block_side_effect(block_locator, user_known):
    """
    Side effect for `SplitModuleStoreRuntime.get_block`
    """
    store = modulestore()
    course = store.get_course(block_locator.course_key)
    block = store.get_item(block_locator)
    runtime = block.runtime
    user = UserFactory.create()
    user.known = user_known

    prepare_runtime_for_user(
        user=user,
        student_data=Mock(),
        runtime=runtime,
        course_id=block_locator.course_key,
        track_function=make_track_function(HttpRequest()),
        request_token=Mock(),
        course=course,
    )
    return block.runtime.get_block_for_descriptor(block)


def get_block_side_effect_for_known_user(self, *args, **kwargs):
    """
    Side effect for known user test.
    """
    return get_block_side_effect(self, True)


def get_block_side_effect_for_unknown_user(self, *args, **kwargs):
    """
    Side effect for unknown user test.
    """
    return get_block_side_effect(self, False)


@ddt.ddt
class TestGetCourseBlocks(UserPartitionTestMixin, CourseStructureTestCase):
    """
    Tests `get_course_blocks` API
    """

    def setup_partitions_and_course(self):
        """
        Setup course structure.
        """
        # Set up user partitions and groups.
        self.setup_groups_partitions(active=True, num_groups=1)
        self.user_partition = self.user_partitions[0]

        # Build course.
        self.course_hierarchy = self.get_course_hierarchy()
        self.blocks = self.build_course(self.course_hierarchy)
        self.course = self.blocks['course']

        # Set up cohorts.
        self.setup_cohorts(self.course)

    def get_course_hierarchy(self):
        """
        Returns a course hierarchy to test with.
        """
        #                      course
        #                    /          \
        #                   /            \
        #                 A[0]            B
        #                                 |
        #                                 |
        #                                 O

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
                        'metadata': {'group_access': {self.user_partition.id: [0]}},
                    },
                    {'#type': 'vertical', '#ref': 'B'},
                ],
            },
            {
                '#type': 'vertical',
                '#ref': 'O',
                '#parents': ['B'],
            },
        ]

    @ddt.data(
        (1, ('course', 'B', 'O'), True),
        (1, ('course', 'A', 'B', 'O'), False),
        (None, ('course', 'B', 'O'), True),
        (None, ('course', 'A', 'B', 'O'), False),
    )
    @ddt.unpack
    def test_get_course_blocks(self, group_id, expected_blocks, user_known):
        """
        Tests that `get_course_blocks` returns blocks without access checks for unknown users.

        Access checks are done through the transformers and through Runtime get_block_for_descriptor. Due
        to the runtime limitations during the tests, the Runtime access checks are not performed as
        get_block_for_descriptor is never called and Block is returned by SplitModuleStoreRuntime.get_block.
        In this test, we mock the SplitModuleStoreRuntime.get_block and check block access for known and unknown users.
        For known users, it performs the Runtime access checks through get_block_for_descriptor. For unknown, it
        skips the access checks.
        """
        self.setup_partitions_and_course()
        if group_id:
            cohort = self.partition_cohorts[self.user_partition.id - 1][group_id - 1]
            add_user_to_cohort(cohort, self.user.username)

        side_effect = get_block_side_effect_for_known_user if user_known else get_block_side_effect_for_unknown_user
        with patch('xmodule.modulestore.split_mongo.split.SplitModuleStoreRuntime.get_block', side_effect=side_effect):
            block_structure = get_course_blocks(
                self.user,
                self.course.location,
                BlockStructureTransformers([]),
            )
        self.assertSetEqual(  # noqa: PT009
            set(block_structure.get_block_keys()),
            self.get_block_key_set(self.blocks, *expected_blocks)
        )

    @override_settings(
        OPEN_EDX_FILTERS_CONFIG={
            'org.openedx.learning.course_blocks.transform.completed.v1': {
                'pipeline': ['lms.djangoapps.course_blocks.tests.test_api.TestOverrideDisplayNameStep'],
                'fail_silently': False,
            },
        },
    )
    # Register a transformer that collects display_name so the collected structure carries the authored value.
    @patch(
        'openedx.core.djangoapps.content.block_structure.transformer_registry.'
        'TransformerRegistry.get_registered_transformers',
        Mock(return_value={OverrideDataTransformer}),
    )
    def test_filter_overrides_do_not_leak_into_collected_structure(self):
        """
        Tests that changes made by the transform completed filter are returned on cold and warm caches
        without modifying the collected block structure or the blocks returned to other users.
        """
        self.setup_partitions_and_course()
        root_key = self.course.location
        authored_name = self.course.display_name
        filtered_user = UserFactory.create(username=FILTERED_USERNAME)

        for _ in range(2):  # the first call collects the structure, the second reads it from the cache
            block_structure = get_course_blocks(filtered_user, root_key, BlockStructureTransformers([]))
            assert block_structure.get_xblock_field(root_key, 'display_name') == 'Changed'

        collected = get_block_structure_manager(self.course.id).get_collected()
        assert collected.get_xblock_field(root_key, 'display_name') == authored_name
        block_structure = get_course_blocks(self.user, root_key, BlockStructureTransformers([]))
        assert block_structure.get_xblock_field(root_key, 'display_name') == authored_name
