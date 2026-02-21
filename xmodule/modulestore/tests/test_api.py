"""
Tests for the modulestore and XBlock python APIs.
"""
from unittest.mock import Mock

from django.conf import settings

from lti_consumer.lti_xblock import LtiConsumerXBlock
from done import DoneXBlock
from xblock.field_data import DictFieldData

from xblock.test.tools import TestRuntime
from xblock.test.test_runtime import TestSimpleMixin
from xmodule.video_block import VideoBlock
from xmodule.modulestore.api import (
    get_javascript_i18n_file_name,
    get_javascript_i18n_file_path,
    get_python_locale_root,
    get_root_module_name,
    get_xblock_root_module_name,
)


def test_get_root_module_name():
    """
    Ensure the module name function works with different xblocks.
    """
    assert get_root_module_name(LtiConsumerXBlock) == 'lti_consumer'
    if settings.USE_EXTRACTED_VIDEO_BLOCK:
        assert get_root_module_name(VideoBlock) == 'xblocks_contrib'
    else:
        assert get_root_module_name(VideoBlock) == 'xmodule'
    assert get_root_module_name(DoneXBlock) == 'done'


def test_get_xblock_root_module_name():
    """
    Ensure the get_root_module_name works with mixed XBlocks.

    The XBlock uses a little-known Mixologist class which changes the final
    XBlock object class. See the XBlock.construct_xblock_from_class method
    for more information about this behavior.
    """
    field_data = DictFieldData({
        'field_a': 5,
        'field_x': [1, 2, 3],
    })
    runtime = TestRuntime(Mock(), mixins=[TestSimpleMixin], services={'field-data': field_data})

    mixed_done_xblock = runtime.construct_xblock_from_class(DoneXBlock, Mock())

    assert mixed_done_xblock.__module__ == 'xblock.core'
    assert mixed_done_xblock.unmixed_class == DoneXBlock, 'The unmixed_class property retains the original property.'

    assert get_xblock_root_module_name(mixed_done_xblock) == 'done'


def test_file_paths_api():
    """
    Test the `get_python_locale_root` returned path.
    """
    root = get_python_locale_root()
    assert root.endswith('/conf/plugins-locale/xblock.v1'), 'Needs to match Makefile and other code'


def test_get_javascript_i18n_file_name():
    """
    Test get_javascript_i18n_file_name relative path to `/static` URL.
    """
    assert get_javascript_i18n_file_name('lti_consumer', 'ar') == 'js/xblock.v1-i18n/lti_consumer/ar.js'


def test_get_javascript_i18n_file_path():
    """
    Test get_javascript_i18n_file_path absolute file path.
    """
    path = str(get_javascript_i18n_file_path('done', 'eo'))
    assert path.endswith(f'{settings.PROJECT_ROOT}/static/js/xblock.v1-i18n/done/eo.js')
