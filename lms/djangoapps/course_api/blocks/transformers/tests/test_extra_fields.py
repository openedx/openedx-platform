"""
Tests for ExtraFieldsTransformer.
"""
from django.test import override_settings

# pylint: disable=protected-access
from openedx.core.djangoapps.content.block_structure.factory import BlockStructureFactory
from xmodule.modulestore.tests.django_utils import (
    ModuleStoreTestCase,  # lint-amnesty, pylint: disable=wrong-import-order
)
from xmodule.modulestore.tests.factories import SampleCourseFactory  # lint-amnesty, pylint: disable=wrong-import-order

from ..extra_fields import ExtraFieldsTransformer


@override_settings(COURSE_BLOCKS_API_EXTRA_FIELDS=[('course', 'other_course_settings')])
class TestExtraFieldsTransformer(ModuleStoreTestCase):
    """
    Test proper behavior for ExtraFieldsTransformer
    """
    shard = 4

    OTHER_COURSE_SETTINGS_DEFAULT = {
        'test key': 'test value',
        'jackson 5': [
            ['a', 'b', 'c'],
            'it\'s easy as',
            [1, 2, 3],
            'as simple as',
            ['do', 're', 'mi']
        ]
    }

    def setUp(self):
        super().setUp()

        self.course = SampleCourseFactory.create(
            other_course_settings=self.OTHER_COURSE_SETTINGS_DEFAULT
        )
        self.course_key = self.course.id

        self.course_usage_key = self.store.make_course_usage_key(self.course_key)
        self.block_structure = BlockStructureFactory.create_from_modulestore(self.course_usage_key, self.store)

    def test_transform(self):
        # collect phase
        ExtraFieldsTransformer.collect(self.block_structure)
        self.block_structure._collect_requested_xblock_fields()

        # transform phase
        ExtraFieldsTransformer().transform(
            usage_info=None,
            block_structure=self.block_structure,
        )

        block_data = self.block_structure.get_transformer_block_data(
            self.course_usage_key, ExtraFieldsTransformer,
        )

        assert block_data.other_course_settings == self.OTHER_COURSE_SETTINGS_DEFAULT
