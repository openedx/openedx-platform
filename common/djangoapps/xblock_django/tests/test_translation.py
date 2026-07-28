"""
Tests for the xblock_django.translation module.
"""

from done import DoneXBlock

from ..translation import get_non_xmodule_xblock_module_names, get_non_xmodule_xblocks


def test_get_non_xmodule_xblock_module_names():
    """
    Ensure xmodule isn't returned but other default xblocks are.
    """
    assert 'xmodule' not in get_non_xmodule_xblock_module_names()
    assert 'done' in get_non_xmodule_xblock_module_names()
    assert 'lti_consumer' in get_non_xmodule_xblock_module_names()


def test_get_non_xmodule_xblocks():
    """
    Ensures that default XBlocks are included.
    """
    assert ('done', DoneXBlock) in get_non_xmodule_xblocks()
