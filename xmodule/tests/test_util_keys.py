"""
Tests for xmodule/util/keys.py
"""
from unittest import TestCase
from unittest.mock import Mock

import ddt
import pytest
from opaque_keys.edx.keys import CourseKey
from opaque_keys.edx.locator import BlockUsageLocator

from xmodule.util.keys import BlockKey, derive_key

mock_block = Mock()
mock_block.id = CourseKey.from_string('course-v1:Beeper+B33P+BOOP')

derived_key_scenarios = [
    {
        'source': BlockUsageLocator.from_string(
            'block-v1:edX+DemoX+Demo_Course+type@chapter+block@interactive_demonstrations'
        ),
        'parent': mock_block,
        'expected': BlockKey('chapter', '5793ec64e25ed870a7dd'),
    },
    {
        'source': BlockUsageLocator.from_string(
            'block-v1:edX+DemoX+Demo_Course+type@chapter+block@interactive_demonstrations'
        ),
        'parent': BlockKey('chapter', 'thingy'),
        'expected': BlockKey('chapter', '599792a5622d85aa41e6'),
    }
]


@ddt.ddt
class TestDeriveKey(TestCase):
    """
    Test reproducible block ID generation.
    """
    @ddt.data(*derived_key_scenarios)
    @ddt.unpack
    def test_derive_key(self, source, parent, expected):
        """
        Test that derive_key returns the expected value.
        """
        assert derive_key(source, parent) == expected


@ddt.ddt
class TestBlockKeyParsing(TestCase):
    """
    Tests for parsing BlockKeys.
    """

    @ddt.data(['chapter:some-id', 'chapter', 'some-id'], ['section:one-more-id', 'section', 'one-more-id'])
    @ddt.unpack
    def test_block_key_from_string(self, block_key_str, blockType, blockId):
        block_key = BlockKey.from_string(block_key_str)
        assert block_key.type == blockType
        assert block_key.id == blockId

    @ddt.data('chapter:invalid:some-id', 'sectionone-more-id')
    def test_block_key_from_string_error(self, block_key_str):
        with pytest.raises(ValueError):
            BlockKey.from_string(block_key_str)

    @ddt.data(
        [BlockKey('chapter', 'some-id'), 'chapter:some-id'], [BlockKey('section', 'one-more-id'), 'section:one-more-id']
    )
    @ddt.unpack
    def test_block_key_to_string(self, block_key, block_key_str):
        assert str(block_key) == block_key_str

    @ddt.data(
        [BlockKey('chapter', 'some-id'), BlockUsageLocator(
            mock_block.id,
            'chapter',
            'some-id'
        )],
        [BlockKey('section', 'one-more-id'), BlockUsageLocator(
            mock_block.id,
            'section',
            'one-more-id'
        )]
    )
    @ddt.unpack
    def test_block_key_to_usage_key(self, block_key: BlockKey, block_key_str):
        assert block_key.to_usage_key(mock_block.id) == block_key_str
