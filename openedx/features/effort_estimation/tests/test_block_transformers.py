"""Tests for effort_estimation transformers."""

from datetime import timedelta

from crum import set_current_request
from django.test.client import RequestFactory
from edx_toggles.toggles.testutils import override_waffle_flag
from edxval.api import create_video, remove_video_for_course

from openedx.core.djangoapps.content.block_structure.factory import BlockStructureFactory
from xmodule.modulestore.tests.django_utils import (
    ModuleStoreTestCase,  # lint-amnesty, pylint: disable=wrong-import-order
)
from xmodule.modulestore.tests.factories import SampleCourseFactory  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.modulestore.tests.sample_courses import BlockInfo  # lint-amnesty, pylint: disable=wrong-import-order

from ..block_transformers import EffortEstimationTransformer
from ..toggles import EFFORT_ESTIMATION_DISABLED_FLAG

# Copied here, rather than used directly from class, just to catch any accidental changes
DISABLE_ESTIMATION = 'disable_estimation'
EFFORT_ACTIVITIES = 'effort_activities'
EFFORT_TIME = 'effort_time'
HTML_WORD_COUNT = 'html_word_count'
VIDEO_CLIP_DURATION = 'video_clip_duration'
VIDEO_DURATION = 'video_duration'


class TestEffortEstimationTransformer(ModuleStoreTestCase):
    """EffortEstimationTransformer tests"""
    def setUp(self):
        super().setUp()

        block_info_tree = [
            BlockInfo('Section', 'chapter', {}, [
                BlockInfo('Subsection', 'sequential', {}, [
                    BlockInfo('Vertical', 'vertical', {}, [
                        BlockInfo('Clip', 'video',
                                  {'edx_video_id': 'edxval1',
                                   'start_time': timedelta(seconds=20),
                                   'end_time': timedelta(seconds=60)},
                                  []),
                        BlockInfo('Video', 'video', {'edx_video_id': 'edxval2'}, []),
                        BlockInfo('Web', 'video', {'edx_video_id': 'edxval3', 'only_on_web': True}, []),
                        BlockInfo('HTML', 'html', {'data': 'Hello World'}, []),
                        BlockInfo('Problem1', 'problem', {}, []),
                        BlockInfo('Problem2', 'problem', {}, []),
                    ]),
                ]),
            ]),
        ]

        self.course_key = SampleCourseFactory.create(block_info_tree=block_info_tree).id
        self.course_usage_key = self.store.make_course_usage_key(self.course_key)
        self.block_structure = BlockStructureFactory.create_from_modulestore(self.course_usage_key, self.store)

        self.section_key = self.course_key.make_usage_key('chapter', 'Section')
        self.subsection_key = self.course_key.make_usage_key('sequential', 'Subsection')
        self.vertical_key = self.course_key.make_usage_key('vertical', 'Vertical')
        self.video_clip_key = self.course_key.make_usage_key('video', 'Clip')
        self.video_normal_key = self.course_key.make_usage_key('video', 'Video')
        self.video_web_key = self.course_key.make_usage_key('video', 'Web')
        self.html_key = self.course_key.make_usage_key('html', 'HTML')

        # Set edxval data
        create_video({
            'edx_video_id': 'edxval1',
            'status': 'uploaded',
            'client_video_id': 'Clip Video',
            'duration': 200,
            'encoded_videos': [],
            'courses': [str(self.course_key)],
        })
        create_video({
            'edx_video_id': 'edxval2',
            'status': 'uploaded',
            'client_video_id': 'Normal Video',
            'duration': 30,
            'encoded_videos': [],
            'courses': [str(self.course_key)],
        })
        create_video({
            'edx_video_id': 'edxval3',
            'status': 'uploaded',
            'client_video_id': 'Web Video',
            'duration': 50,
            'encoded_videos': [],
            'courses': [str(self.course_key)],
        })

    def collect(self):
        EffortEstimationTransformer.collect(self.block_structure)
        self.block_structure._collect_requested_xblock_fields()  # pylint: disable=protected-access

    def transform(self):
        EffortEstimationTransformer().transform(None, self.block_structure)

    def collect_and_transform(self):
        self.collect()
        self.transform()

    def set_mobile_request(self):
        request = RequestFactory().request()
        request.META['HTTP_USER_AGENT'] = 'edX/org.edx.mobile'
        self.addCleanup(set_current_request, None)
        set_current_request(request)

    def get_collection_field(self, key, name):
        return self.block_structure.get_transformer_block_field(key, EffortEstimationTransformer, name)

    def assert_collected(self):
        """Confirm we at least collected the data (but not necessarily that we injected that data into block tree)"""
        assert self.get_collection_field(self.video_clip_key, VIDEO_DURATION) == 200
        assert self.get_collection_field(self.video_clip_key, VIDEO_CLIP_DURATION) == 40
        assert self.get_collection_field(self.video_normal_key, VIDEO_DURATION) == 30
        assert self.get_collection_field(self.video_normal_key, VIDEO_CLIP_DURATION) is None
        assert self.get_collection_field(self.video_web_key, VIDEO_DURATION) == 50
        assert self.get_collection_field(self.video_web_key, VIDEO_CLIP_DURATION) is None
        assert self.get_collection_field(self.html_key, HTML_WORD_COUNT) == 2

        assert self.block_structure.get_transformer_data(EffortEstimationTransformer, DISABLE_ESTIMATION) is None

    def test_collection(self):
        self.collect()
        self.assert_collected()

    def test_incomplete_data_collection(self):
        """Ensure that missing video data prevents any estimates from being generated"""
        remove_video_for_course(str(self.course_key), 'edxval3')
        self.collect_and_transform()

        assert self.block_structure.get_transformer_data(EffortEstimationTransformer, DISABLE_ESTIMATION) is True

        assert self.block_structure.get_xblock_field(self.section_key, EFFORT_ACTIVITIES) is None
        assert self.block_structure.get_xblock_field(self.section_key, EFFORT_TIME) is None
        assert self.block_structure.get_xblock_field(self.subsection_key, EFFORT_ACTIVITIES) is None
        assert self.block_structure.get_xblock_field(self.subsection_key, EFFORT_TIME) is None

    @override_waffle_flag(EFFORT_ESTIMATION_DISABLED_FLAG, True)
    def test_disabled(self):
        self.collect_and_transform()
        self.assert_collected()
        assert self.block_structure.get_xblock_field(self.section_key, EFFORT_ACTIVITIES) is None
        assert self.block_structure.get_xblock_field(self.section_key, EFFORT_TIME) is None
        assert self.block_structure.get_xblock_field(self.subsection_key, EFFORT_ACTIVITIES) is None
        assert self.block_structure.get_xblock_field(self.subsection_key, EFFORT_TIME) is None

    def test_enabled(self):
        self.collect_and_transform()
        assert self.block_structure.get_xblock_field(self.section_key, EFFORT_ACTIVITIES) == 1
        assert self.block_structure.get_xblock_field(self.section_key, EFFORT_TIME) == 121
        assert self.block_structure.get_xblock_field(self.subsection_key, EFFORT_ACTIVITIES) == 1
        assert self.block_structure.get_xblock_field(self.subsection_key, EFFORT_TIME) == 121

    def test_mobile_video_support(self):
        """Clips values are ignored and web only videos should be excluded"""
        self.set_mobile_request()
        self.collect_and_transform()
        assert self.block_structure.get_xblock_field(self.section_key, EFFORT_TIME) == 231
