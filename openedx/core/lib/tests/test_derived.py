"""
Tests for derived.py
"""


import sys
from unittest import TestCase

from openedx.core.lib.derived import Derived, derive_settings


class TestDerivedSettings(TestCase):
    """
    Test settings that are derived from other settings.
    """
    def setUp(self):
        super().setUp()
        self.module = sys.modules[__name__]
        self.module.SIMPLE_VALUE = 'paneer'
        self.module.DERIVED_VALUE = Derived(lambda settings: 'mutter ' + settings.SIMPLE_VALUE)
        self.module.ANOTHER_DERIVED_VALUE = Derived(lambda settings: settings.DERIVED_VALUE + ' with naan')
        self.module.UNREGISTERED_DERIVED_VALUE = lambda settings: settings.SIMPLE_VALUE + ' is cheese'
        self.module.DICT_VALUE = {}
        self.module.DICT_VALUE['test_key'] = Derived(lambda settings: settings.DERIVED_VALUE * 3)
        self.module.DICT_VALUE['list_key'] = ['not derived', Derived(lambda settings: settings.DERIVED_VALUE)]

    def test_derived_settings_are_derived(self):
        derive_settings(__name__)
        assert self.module.DERIVED_VALUE == 'mutter paneer'
        assert self.module.ANOTHER_DERIVED_VALUE == 'mutter paneer with naan'

    def test_unregistered_derived_settings(self):
        derive_settings(__name__)
        assert callable(self.module.UNREGISTERED_DERIVED_VALUE)

    def test_derived_settings_overridden(self):
        self.module.DERIVED_VALUE = 'aloo gobi'
        derive_settings(__name__)
        assert self.module.DERIVED_VALUE == 'aloo gobi'
        assert self.module.ANOTHER_DERIVED_VALUE == 'aloo gobi with naan'

    def test_derived_dict_settings(self):
        derive_settings(__name__)
        assert self.module.DICT_VALUE['test_key'] == 'mutter paneermutter paneermutter paneer'

    def test_derived_nested_settings(self):
        derive_settings(__name__)
        assert self.module.DICT_VALUE['list_key'][1] == 'mutter paneer'
