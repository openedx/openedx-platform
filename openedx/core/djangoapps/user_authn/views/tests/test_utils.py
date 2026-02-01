"""
Tests for user utils functionality.
"""
from datetime import datetime
from typing import Optional
from unittest.mock import Mock, patch

import ddt
from django.db.models import Model
from django.test import TestCase
from django.test.utils import override_settings

from openedx.core.djangoapps.user_authn.views.registration_form import get_extended_profile_model
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


@ddt.ddt
class TestGetExtendedProfileModel(TestCase):
    """
    Tests for get_extended_profile_model function
    """

    @ddt.data(None, "")
    def test_get_extended_profile_model_no_setting_or_empty_string(self, setting_value: Optional[str]):
        """
        Test when REGISTRATION_EXTENSION_FORM setting is not configured
        """
        with override_settings(REGISTRATION_EXTENSION_FORM=setting_value):
            result = get_extended_profile_model()

        self.assertIsNone(result)

    @override_settings(REGISTRATION_EXTENSION_FORM="invalid.module.path")
    @patch("openedx.core.djangoapps.user_authn.views.registration_form.logger")
    def test_get_extended_profile_model_invalid_module(self, mock_logger: Mock):
        """
        Test when the module path is invalid
        """
        result = get_extended_profile_model()

        self.assertIsNone(result)
        mock_logger.warning.assert_called_once()
        self.assertIn("Could not load extended profile model", str(mock_logger.warning.call_args))

    @override_settings(REGISTRATION_EXTENSION_FORM="django.forms.Form")
    def test_get_extended_profile_model_no_meta_class(self):
        """
        Test when the form class doesn't have a Meta class
        """
        result = get_extended_profile_model()

        # Form doesn't have Meta.model, should return None
        self.assertIsNone(result)

    @override_settings(REGISTRATION_EXTENSION_FORM="invalid_module_path")
    @patch("openedx.core.djangoapps.user_authn.views.registration_form.logger")
    def test_get_extended_profile_model_malformed_path(self, mock_logger: Mock):
        """
        Test when the setting value doesn't have a dot separator
        """
        result = get_extended_profile_model()

        self.assertIsNone(result)
        mock_logger.warning.assert_called_once()

    @override_settings(REGISTRATION_EXTENSION_FORM="myapp.forms.CustomExtendedProfileForm")
    @patch("openedx.core.djangoapps.user_authn.views.registration_form.import_module")
    def test_get_extended_profile_model_custom_form(self, mock_import_module: Mock):
        """
        Test loading model from a custom extended profile form
        """
        # Create a mock model
        mock_model = Mock(spec=Model)
        # Create a mock form class with Meta.model
        mock_form_class = Mock()
        mock_form_class.Meta = Mock()
        mock_form_class.Meta.model = mock_model
        # Create a mock module with the custom form class
        mock_module = Mock()
        mock_module.CustomExtendedProfileForm = mock_form_class
        mock_import_module.return_value = mock_module

        result = get_extended_profile_model()

        self.assertEqual(result, mock_model)
        mock_import_module.assert_called_once_with("myapp.forms")

    @override_settings(REGISTRATION_EXTENSION_FORM="myapp.forms.FormWithoutModel")
    @patch("openedx.core.djangoapps.user_authn.views.registration_form.import_module")
    def test_get_extended_profile_model_form_without_model(self, mock_import_module: Mock):
        """
        Test when form has Meta but no model attribute
        """
        # Create a mock form class with Meta but no model
        mock_form_class = Mock()
        mock_form_class.Meta = Mock(spec=[])  # Meta exists but has no model attribute
        # Create a mock module with the form class
        mock_module = Mock()
        mock_module.FormWithoutModel = mock_form_class
        mock_import_module.return_value = mock_module

        result = get_extended_profile_model()

        self.assertIsNone(result)

    @ddt.data(
        (ImportError, "Module not found"),
        (ModuleNotFoundError, "No module named 'myapp'"),
    )
    @ddt.unpack
    @override_settings(REGISTRATION_EXTENSION_FORM="myapp.forms.ExtendedProfileForm")
    @patch("openedx.core.djangoapps.user_authn.views.registration_form.import_module")
    @patch("openedx.core.djangoapps.user_authn.views.registration_form.logger")
    def test_get_extended_profile_model_import_errors(
        self, exception_class: type, error_message: str, mock_logger: Mock, mock_import_module: Mock
    ):
        """
        Test when import_module raises ImportError or ModuleNotFoundError
        """
        mock_import_module.side_effect = exception_class(error_message)

        result = get_extended_profile_model()

        self.assertIsNone(result)
        mock_logger.warning.assert_called_once()
        self.assertIn("Could not load extended profile model", str(mock_logger.warning.call_args))

    @override_settings(REGISTRATION_EXTENSION_FORM="myapp.forms.NonExistentForm")
    @patch("openedx.core.djangoapps.user_authn.views.registration_form.import_module")
    @patch("openedx.core.djangoapps.user_authn.views.registration_form.logger")
    def test_get_extended_profile_model_attribute_error(self, mock_logger: Mock, mock_import_module: Mock):
        """
        Test when the form class doesn't exist in the module
        """
        mock_module = Mock(spec=[])
        mock_import_module.return_value = mock_module

        result = get_extended_profile_model()

        self.assertIsNone(result)
        mock_logger.warning.assert_called_once()
