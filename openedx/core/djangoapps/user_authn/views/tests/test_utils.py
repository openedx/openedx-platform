"""
Tests for user utils functionality.
"""
from datetime import datetime
from unittest.mock import patch

import ddt
from django.test import TestCase

from openedx.core.djangoapps.user_authn.views.utils import _get_username_prefix, get_auto_generated_username


@ddt.ddt
class TestGenerateUsername(TestCase):
    """
    Test case for the get_auto_generated_username function.
    """

    @ddt.data(
        ({'first_name': 'John', 'last_name': 'Doe'}, "JD"),
        ({'name': 'Jane Smith'}, "JS"),
        ({'name': 'Jane'}, "J"),
        ({'name': 'John Doe Smith'}, "JD")
    )
    @ddt.unpack
    def test_generate_username_from_data(self, data, expected_initials):
        """
        Test get_auto_generated_username function.
        """
        random_string = 'XYZA'
        current_year_month = f"_{datetime.now().year % 100}{datetime.now().month:02d}_"

        with patch('openedx.core.djangoapps.user_authn.views.utils.random.choices') as mock_choices:
            mock_choices.return_value = ['X', 'Y', 'Z', 'A']

            username = get_auto_generated_username(data)

        expected_username = expected_initials + current_year_month + random_string
        self.assertEqual(username, expected_username)

    @ddt.data(
        ({'first_name': 'John', 'last_name': 'Doe'}, "JD"),
        ({'name': 'Jane Smith'}, "JS"),
        ({'name': 'Jane'}, "J"),
        ({'name': 'John Doe Smith'}, "JD"),
        ({'first_name': 'John Doe', 'last_name': 'Smith'}, "JD"),
        ({}, None),
        ({'first_name': '', 'last_name': ''}, None),
        ({'name': ''}, None),
        ({'name': '='}, None),
        ({'name': '@'}, None),
        ({'first_name': '阿提亚', 'last_name': '阿提亚'}, "AT"),
        ({'first_name': 'أحمد', 'last_name': 'محمد'}, "HM"),
        ({'name': 'أحمد محمد'}, "HM"),
    )
    @ddt.unpack
    def test_get_username_prefix(self, data, expected_initials):
        """
        Test _get_username_prefix function.
        """
        username_prefix = _get_username_prefix(data)
        self.assertEqual(username_prefix, expected_initials)

    @patch('openedx.core.djangoapps.user_authn.views.utils._get_username_prefix')
    @patch('openedx.core.djangoapps.user_authn.views.utils.random.choices')
    @patch('openedx.core.djangoapps.user_authn.views.utils.datetime')
    def test_get_auto_generated_username_no_prefix(self, mock_datetime, mock_choices, mock_get_username_prefix):
        """
        Test get_auto_generated_username function when no name data is provided.
        """
        mock_datetime.now.return_value.strftime.return_value = f"{datetime.now().year % 100} {datetime.now().month:02d}"
        mock_choices.return_value = ['X', 'Y', 'Z', 'A']  # Fixed random string for testing

        mock_get_username_prefix.return_value = None

        current_year_month = f"{datetime.now().year % 100}{datetime.now().month:02d}_"
        random_string = 'XYZA'
        expected_username = current_year_month + random_string

        username = get_auto_generated_username({})
        self.assertEqual(username, expected_username)
