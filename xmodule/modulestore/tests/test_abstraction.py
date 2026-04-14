"""
Simple test to ensure that modulestore base classes remain abstract
"""


from unittest import TestCase

import pytest

from xmodule.modulestore import ModuleStoreRead, ModuleStoreWrite


class AbstractionTest(TestCase):
    """
    Tests that the ModuleStore objects are properly abstracted
    """

    def test_cant_instantiate_abstract_class(self):
        pytest.raises(TypeError, ModuleStoreRead)
        # Cannot be instantiated due to explicit abstraction
        pytest.raises(TypeError, ModuleStoreWrite)
