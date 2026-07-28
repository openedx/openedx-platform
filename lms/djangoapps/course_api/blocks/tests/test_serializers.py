"""
Tests for Course Blocks serializers
"""


from unittest.mock import MagicMock

from common.djangoapps.student.roles import CourseStaffRole
from common.djangoapps.student.tests.factories import UserFactory
from lms.djangoapps.course_blocks.api import get_course_block_access_transformers, get_course_blocks
from openedx.core.djangoapps.content.block_structure.transformers import BlockStructureTransformers
from xmodule.modulestore import ModuleStoreEnum  # pylint: disable=wrong-import-order
from xmodule.modulestore.tests.django_utils import (
    SharedModuleStoreTestCase,  # pylint: disable=wrong-import-order
)
from xmodule.modulestore.tests.factories import ToyCourseFactory  # pylint: disable=wrong-import-order

from ..serializers import BlockDictSerializer, BlockSerializer
from ..transformers.blocks_api import BlocksAPITransformer
from .helpers import deserialize_usage_key


class TestBlockSerializerBase(SharedModuleStoreTestCase):
    """
    Base class for testing BlockSerializer and BlockDictSerializer
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        cls.course = ToyCourseFactory.create()

        # Hide the html block
        key = cls.course.id.make_usage_key('html', 'secret:toylab')
        cls.html_block = cls.store.get_item(key)
        cls.html_block.visible_to_staff_only = True
        cls.store.update_item(cls.html_block, ModuleStoreEnum.UserID.test)

    def setUp(self):
        super().setUp()

        self.user = UserFactory.create()

        blocks_api_transformer = BlocksAPITransformer(
            block_types_to_count=['video'],
            requested_student_view_data=['video'],
        )
        self.transformers = BlockStructureTransformers(
            get_course_block_access_transformers(self.user) + [blocks_api_transformer]
        )
        self.block_structure = get_course_blocks(
            self.user,
            self.course.location,
            self.transformers,
        )
        self.serializer_context = {
            'request': MagicMock(),
            'block_structure': self.block_structure,
            'requested_fields': ['type'],
        }

    def assert_basic_block(self, block_key_string, serialized_block):
        """
        Verifies the given serialized_block when basic fields are requested.
        """
        block_key = deserialize_usage_key(block_key_string, self.course.id)
        assert self.block_structure.get_xblock_field(block_key, 'category') == serialized_block['type']
        assert set(serialized_block.keys()) == {
            'id', 'block_id', 'type', 'lms_web_url', 'legacy_web_url', 'student_view_url'
        }

    def add_additional_requested_fields(self, context=None):
        """
        Adds additional fields to the requested_fields context for the serializer.
        """
        if context is None:
            context = self.serializer_context
        context['requested_fields'].extend([
            'children',
            'display_name',
            'graded',
            'format',
            'block_counts',
            'student_view_data',
            'student_view_multi_device',
            'lti_url',
            'visible_to_staff_only',
            'hide_after_due'
        ])

    def assert_extended_block(self, serialized_block):
        """
        Verifies the given serialized_block when additional fields are requested.
        """
        assert {
            'id',
            'type',
            'lms_web_url',
            'legacy_web_url',
            'student_view_url',
            'display_name',
            'graded',
            'student_view_multi_device',
            'lti_url',
            'visible_to_staff_only',
            'hide_after_due'
        } <= set(serialized_block.keys())

        # video blocks should have student_view_data
        if serialized_block['type'] == 'video':
            assert 'student_view_data' in serialized_block

        # html blocks should have student_view_multi_device set to True
        if serialized_block['type'] == 'html':
            assert 'student_view_multi_device' in serialized_block
            assert serialized_block['student_view_multi_device']

        # chapters with video should have block_counts
        if serialized_block['type'] == 'chapter':
            if serialized_block['display_name'] not in ('poll_test', 'handout_container'):
                assert 'block_counts' in serialized_block
            else:
                assert 'block_counts' not in serialized_block

    def create_staff_context(self):
        """
        Create staff user and course blocks accessible by that user
        """
        # Create a staff user to be able to test visible_to_staff_only
        staff_user = UserFactory.create()
        CourseStaffRole(self.course.location.course_key).add_users(staff_user)

        block_structure = get_course_blocks(
            staff_user,
            self.course.location,
            self.transformers,
        )
        return {
            'request': MagicMock(),
            'block_structure': block_structure,
            'requested_fields': ['type'],
        }

    def assert_staff_fields(self, serialized_block):
        """
        Test fields accessed by a staff user
        """
        if serialized_block['id'] == str(self.html_block.location):
            assert serialized_block['visible_to_staff_only']
        else:
            assert not serialized_block['visible_to_staff_only']


class TestBlockSerializer(TestBlockSerializerBase):
    """
    Tests the BlockSerializer class, which returns a list of blocks.
    """

    def create_serializer(self, context=None):
        """
        creates a BlockSerializer
        """
        if context is None:
            context = self.serializer_context
        return BlockSerializer(
            context['block_structure'], many=True, context=context,
        )

    def test_basic(self):
        serializer = self.create_serializer()
        for serialized_block in serializer.data:
            self.assert_basic_block(serialized_block['id'], serialized_block)
        assert len(serializer.data) == 28

    def test_additional_requested_fields(self):
        self.add_additional_requested_fields()
        serializer = self.create_serializer()
        for serialized_block in serializer.data:
            self.assert_extended_block(serialized_block)
        assert len(serializer.data) == 28

    def test_staff_fields(self):
        """
        Test fields accessed by a staff user
        """
        context = self.create_staff_context()
        self.add_additional_requested_fields(context)
        serializer = self.create_serializer(context)
        for serialized_block in serializer.data:
            self.assert_extended_block(serialized_block)
            self.assert_staff_fields(serialized_block)
        assert len(serializer.data) == 29


class TestBlockDictSerializer(TestBlockSerializerBase):
    """
    Tests the BlockDictSerializer class, which returns a dict of blocks key-ed by its block_key.
    """

    def create_serializer(self, context=None):
        """
        creates a BlockDictSerializer
        """
        if context is None:
            context = self.serializer_context
        return BlockDictSerializer(
            context['block_structure'], many=False, context=context,
        )

    def test_basic(self):
        serializer = self.create_serializer()

        # verify root
        assert serializer.data['root'] == str(self.block_structure.root_block_usage_key)

        # verify blocks
        for block_key_string, serialized_block in serializer.data['blocks'].items():
            assert serialized_block['id'] == block_key_string
            self.assert_basic_block(block_key_string, serialized_block)
        assert len(serializer.data['blocks']) == 28

    def test_additional_requested_fields(self):
        self.add_additional_requested_fields()
        serializer = self.create_serializer()
        for serialized_block in serializer.data['blocks'].values():
            self.assert_extended_block(serialized_block)
        assert len(serializer.data['blocks']) == 28

    def test_staff_fields(self):
        """
        Test fields accessed by a staff user
        """
        context = self.create_staff_context()
        self.add_additional_requested_fields(context)
        serializer = self.create_serializer(context)
        for serialized_block in serializer.data['blocks'].values():
            self.assert_extended_block(serialized_block)
            self.assert_staff_fields(serialized_block)
        assert len(serializer.data['blocks']) == 29
