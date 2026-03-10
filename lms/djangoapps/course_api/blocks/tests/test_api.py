"""
Tests for Blocks api.py
"""


from unittest.mock import patch

import ddt
from django.test.client import RequestFactory

from common.djangoapps.student.tests.factories import UserFactory
from openedx.core.djangoapps.content.block_structure.api import clear_course_from_cache
from xmodule.modulestore import ModuleStoreEnum  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.modulestore.tests.django_utils import SharedModuleStoreTestCase  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.modulestore.tests.factories import SampleCourseFactory, check_mongo_calls  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.modulestore.tests.sample_courses import BlockInfo  # lint-amnesty, pylint: disable=wrong-import-order

from ..api import get_blocks


class TestGetBlocks(SharedModuleStoreTestCase):
    """
    Tests for the get_blocks function
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        with cls.store.default_store(ModuleStoreEnum.Type.split):
            cls.course = SampleCourseFactory.create()

        # hide the html block
        cls.html_block = cls.store.get_item(cls.course.id.make_usage_key('html', 'html_x1a_1'))
        cls.html_block.visible_to_staff_only = True
        cls.store.update_item(cls.html_block, ModuleStoreEnum.UserID.test)

    def setUp(self):
        super().setUp()
        self.user = UserFactory.create()
        self.request = RequestFactory().get("/dummy")
        self.request.user = self.user

    def test_basic(self):
        blocks = get_blocks(self.request, self.course.location, self.user)
        assert blocks['root'] == str(self.course.location)

        # subtract for (1) the orphaned course About block and (2) the hidden Html block
        assert len(blocks['blocks']) == (len(self.store.get_items(self.course.id)) - 2)
        assert str(self.html_block.location) not in blocks['blocks']

    def test_no_user(self):
        blocks = get_blocks(self.request, self.course.location)
        assert str(self.html_block.location) not in blocks['blocks']
        vertical_block = self.store.get_item(self.course.id.make_usage_key('vertical', 'vertical_x1a'))
        assert str(vertical_block.location) in blocks['blocks']

    def test_access_before_api_transformer_order(self):
        """
        Tests the order of transformers: access checks are made before the api
        transformer is applied.
        """
        blocks = get_blocks(self.request, self.course.location, self.user, nav_depth=5, requested_fields=['nav_depth'])
        vertical_block = self.store.get_item(self.course.id.make_usage_key('vertical', 'vertical_x1a'))
        problem_block = self.store.get_item(self.course.id.make_usage_key('problem', 'problem_x1a_1'))

        vertical_descendants = blocks['blocks'][str(vertical_block.location)]['descendants']

        assert str(problem_block.location) in vertical_descendants
        assert str(self.html_block.location) not in vertical_descendants

    def test_sub_structure(self):
        sequential_block = self.store.get_item(self.course.id.make_usage_key('sequential', 'sequential_y1'))

        blocks = get_blocks(self.request, sequential_block.location, self.user)
        assert blocks['root'] == str(sequential_block.location)
        assert len(blocks['blocks']) == 5

        for block_type, block_name, is_inside_of_structure in (
                ('vertical', 'vertical_y1a', True),
                ('problem', 'problem_y1a_1', True),
                ('chapter', 'chapter_y', False),
                ('sequential', 'sequential_x1', False),
        ):
            block = self.store.get_item(self.course.id.make_usage_key(block_type, block_name))
            if is_inside_of_structure:
                assert str(block.location) in blocks['blocks']
            else:
                assert str(block.location) not in blocks['blocks']

    def test_filtering_by_block_types(self):
        sequential_block = self.store.get_item(self.course.id.make_usage_key('sequential', 'sequential_y1'))

        # not filtered blocks
        blocks = get_blocks(self.request, sequential_block.location, self.user, requested_fields=['type'])
        assert len(blocks['blocks']) == 5
        found_not_problem = False
        for block in blocks['blocks'].values():
            if block['type'] != 'problem':
                found_not_problem = True
        assert found_not_problem

        # filtered blocks
        blocks = get_blocks(self.request, sequential_block.location, self.user,
                            block_types_filter=['problem'], requested_fields=['type'])
        assert len(blocks['blocks']) == 3
        for block in blocks['blocks'].values():
            assert block['type'] == 'problem'


class TestGetBlocksVideoUrls(SharedModuleStoreTestCase):
    """
    Tests the video blocks returned have their URL re-written for
    encoded videos.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        with cls.store.default_store(ModuleStoreEnum.Type.split):
            cls.course = SampleCourseFactory.create(
                block_info_tree=[
                    BlockInfo('empty_chapter', 'chapter', {}, [
                        BlockInfo('empty_sequential', 'sequential', {}, [
                            BlockInfo('empty_vertical', 'vertical', {}, []),
                        ]),
                    ]),
                    BlockInfo('full_chapter', 'chapter', {}, [
                        BlockInfo('full_sequential', 'sequential', {}, [
                            BlockInfo('full_vertical', 'vertical', {}, [
                                BlockInfo('html', 'html', {}, []),
                                BlockInfo('sample_video', 'video', {}, [])
                            ]),
                        ]),
                    ])
                ]
            )

    def setUp(self):
        super().setUp()
        self.user = UserFactory.create()
        self.request = RequestFactory().get("/dummy")
        self.request.user = self.user

    @patch('xmodule.video_block.VideoBlock.student_view_data')
    def test_video_urls_rewrite(self, video_data_patch):
        """
        Verify the video blocks returned have their URL re-written for
        encoded videos.
        """
        video_data_patch.return_value = {
            'encoded_videos': {
                'hls': {
                    'url': 'https://xyz123.cloudfront.net/XYZ123ABC.mp4',
                    'file_size': 0
                },
                'mobile_low': {
                    'url': 'https://1234abcd.cloudfront.net/ABCD1234abcd.mp4',
                    'file_size': 0
                }
            }
        }
        blocks = get_blocks(
            self.request, self.course.location, requested_fields=['student_view_data'], student_view_data=['video']
        )
        video_block_key = str(self.course.id.make_usage_key('video', 'sample_video'))
        video_block_data = blocks['blocks'][video_block_key]
        for video_data in video_block_data['student_view_data']['encoded_videos'].values():
            assert 'cloudfront' not in video_data['url']


@ddt.ddt
class TestGetBlocksQueryCountsBase(SharedModuleStoreTestCase):
    """
    Base for the get_blocks tests.
    """

    ENABLED_SIGNALS = ['course_published']

    def setUp(self):
        super().setUp()

        self.user = UserFactory.create()
        self.request = RequestFactory().get("/dummy")
        self.request.user = self.user

    def _create_course(self, store_type):
        """
        Creates the sample course in the given store type.
        """
        with self.store.default_store(store_type):
            return SampleCourseFactory.create()

    def _get_blocks(self, course, expected_mongo_queries, expected_sql_queries):
        """
        Verifies the number of expected queries when calling
        get_blocks on the given course.
        """
        with check_mongo_calls(expected_mongo_queries):
            with self.assertNumQueries(expected_sql_queries):
                get_blocks(self.request, course.location, self.user)


@ddt.ddt
class TestGetBlocksQueryCounts(TestGetBlocksQueryCountsBase):
    """
    Tests query counts for the get_blocks function.
    """

    @ddt.data(ModuleStoreEnum.Type.split)
    def test_query_counts_cached(self, store_type):
        course = self._create_course(store_type)
        self._get_blocks(
            course,
            expected_mongo_queries=0,
            expected_sql_queries=14,
        )

    @ddt.data(
        (ModuleStoreEnum.Type.split, 2, 24),
    )
    @ddt.unpack
    def test_query_counts_uncached(self, store_type, expected_mongo_queries, num_sql_queries):
        course = self._create_course(store_type)
        clear_course_from_cache(course.id)

        self._get_blocks(
            course,
            expected_mongo_queries,
            expected_sql_queries=num_sql_queries,
        )
