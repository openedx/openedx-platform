""" Tests for Name Affirmation API utils """

from unittest import TestCase
from unittest.mock import patch

import ddt
from edx_django_utils.plugins import PluginError

from openedx.features.name_affirmation_api.utils import is_name_affirmation_installed


@ddt.ddt
class TestNameAffirmationAPIUtils(TestCase):
    """ Tests for Name Affirmation API utils """

    @patch('openedx.features.name_affirmation_api.utils.PluginManager')
    def test_name_affirmation_installed(self, mock_manager):
        mock_manager.get_plugin.return_value = 'mock plugin'
        self.assertTrue(is_name_affirmation_installed())  # noqa: PT009

    @patch('openedx.features.name_affirmation_api.utils.PluginManager')
    def test_name_affirmation_not_installed(self, mock_manager):
        mock_manager.side_effect = PluginError('No such plugin')
        with self.assertRaises(PluginError):  # noqa: PT027
            self.assertFalse(is_name_affirmation_installed())  # noqa: PT009
