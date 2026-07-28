"""
Tests for user utils functionality.
"""

from datetime import datetime
from unittest.mock import Mock, patch

import ddt
from django.db.models import Model
from django.test import TestCase
from django.test.utils import override_settings

from openedx.core.djangoapps.user_authn.views.registration_form import (
    get_extended_profile_model,
    get_registration_extension_form,
)
from openedx.core.djangoapps.user_authn.views.utils import _get_username_prefix, get_auto_generated_username


@ddt.ddt
class TestGenerateUsername(TestCase):
    """
    Test case for the get_auto_generated_username function.
    """

    @ddt.data(
        ({"first_name": "John", "last_name": "Doe"}, "JD"),
        ({"name": "Jane Smith"}, "JS"),
        ({"name": "Jane"}, "J"),
        ({"name": "John Doe Smith"}, "JD"),
    )
    @ddt.unpack
    def test_generate_username_from_data(self, data, expected_initials):
        """
        Test get_auto_generated_username function.
        """
        random_string = "XYZA"
        current_year_month = f"_{datetime.now().year % 100}{datetime.now().month:02d}_"

        with patch("openedx.core.djangoapps.user_authn.views.utils.random.choices") as mock_choices:
            mock_choices.return_value = ["X", "Y", "Z", "A"]

            username = get_auto_generated_username(data)

        expected_username = expected_initials + current_year_month + random_string
        self.assertEqual(username, expected_username)  # noqa: PT009

    @ddt.data(
        ({"first_name": "John", "last_name": "Doe"}, "JD"),
        ({"name": "Jane Smith"}, "JS"),
        ({"name": "Jane"}, "J"),
        ({"name": "John Doe Smith"}, "JD"),
        ({"first_name": "John Doe", "last_name": "Smith"}, "JD"),
        ({}, None),
        ({"first_name": "", "last_name": ""}, None),
        ({"name": ""}, None),
        ({"name": "="}, None),
        ({"name": "@"}, None),
        ({"first_name": "阿提亚", "last_name": "阿提亚"}, "AT"),
        ({"first_name": "أحمد", "last_name": "محمد"}, "HM"),
        ({"name": "أحمد محمد"}, "HM"),
    )
    @ddt.unpack
    def test_get_username_prefix(self, data, expected_initials):
        """
        Test _get_username_prefix function.
        """
        username_prefix = _get_username_prefix(data)
        self.assertEqual(username_prefix, expected_initials)  # noqa: PT009

    @patch("openedx.core.djangoapps.user_authn.views.utils._get_username_prefix")
    @patch("openedx.core.djangoapps.user_authn.views.utils.random.choices")
    @patch("openedx.core.djangoapps.user_authn.views.utils.datetime")
    def test_get_auto_generated_username_no_prefix(self, mock_datetime, mock_choices, mock_get_username_prefix):
        """
        Test get_auto_generated_username function when no name data is provided.
        """
        mock_datetime.now.return_value.strftime.return_value = f"{datetime.now().year % 100} {datetime.now().month:02d}"
        mock_choices.return_value = ["X", "Y", "Z", "A"]  # Fixed random string for testing

        mock_get_username_prefix.return_value = None

        current_year_month = f"{datetime.now().year % 100}{datetime.now().month:02d}_"
        random_string = "XYZA"
        expected_username = current_year_month + random_string

        username = get_auto_generated_username({})
        self.assertEqual(username, expected_username)  # noqa: PT009


@ddt.ddt
class TestGetExtendedProfileModel(TestCase):
    """
    Tests for `get_extended_profile_model function
    """

    @ddt.data(None, "")
    def test_get_extended_profile_model_no_setting_or_empty_string(self, setting_value: str | None):
        """
        Test when `PROFILE_EXTENSION_FORM` setting is not configured
        """
        with override_settings(PROFILE_EXTENSION_FORM=setting_value):
            result = get_extended_profile_model()

        self.assertIsNone(result)  # noqa: PT009

    @override_settings(PROFILE_EXTENSION_FORM="invalid.module.path")
    @patch("openedx.core.djangoapps.user_authn.views.registration_form.log")
    def test_get_extended_profile_model_invalid_module(self, mock_logger: Mock):
        """
        Test when the module path is invalid
        """
        result = get_extended_profile_model()

        self.assertIsNone(result)  # noqa: PT009
        mock_logger.warning.assert_called_once()
        self.assertIn("Could not load extended profile model", str(mock_logger.warning.call_args))  # noqa: PT009

    @override_settings(PROFILE_EXTENSION_FORM="django.forms.Form")
    def test_get_extended_profile_model_no_meta_class(self):
        """
        Test when the form class doesn't have a Meta class
        """
        result = get_extended_profile_model()

        self.assertIsNone(result)  # noqa: PT009

    @override_settings(PROFILE_EXTENSION_FORM="invalid_module_path")
    @patch("openedx.core.djangoapps.user_authn.views.registration_form.log")
    def test_get_extended_profile_model_malformed_path(self, mock_logger: Mock):
        """
        Test when the setting value doesn't have a dot separator
        """
        result = get_extended_profile_model()

        self.assertIsNone(result)  # noqa: PT009
        mock_logger.warning.assert_called_once()
        self.assertIn("Could not load extended profile model", str(mock_logger.warning.call_args))  # noqa: PT009

    @override_settings(PROFILE_EXTENSION_FORM="myapp.forms.CustomExtendedProfileForm")
    @patch("openedx.core.djangoapps.user_authn.views.registration_form.import_module")
    def test_get_extended_profile_model_custom_form(self, mock_import_module: Mock):
        """
        Test loading model from a custom extended profile form
        """
        mock_model = Mock(spec=Model)
        mock_form_class = Mock()
        mock_form_class.Meta = Mock()
        mock_form_class.Meta.model = mock_model
        mock_module = Mock()
        mock_module.CustomExtendedProfileForm = mock_form_class
        mock_import_module.return_value = mock_module

        result = get_extended_profile_model()

        self.assertEqual(result, mock_model)  # noqa: PT009
        mock_import_module.assert_called_once_with("myapp.forms")

    @override_settings(PROFILE_EXTENSION_FORM="myapp.forms.FormWithoutModel")
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

        self.assertIsNone(result)  # noqa: PT009

    @ddt.data((ImportError, "Module not found"))
    @ddt.unpack
    @override_settings(PROFILE_EXTENSION_FORM="myapp.forms.ExtendedProfileForm")
    @patch("openedx.core.djangoapps.user_authn.views.registration_form.import_module")
    @patch("openedx.core.djangoapps.user_authn.views.registration_form.log")
    def test_get_extended_profile_model_import_errors(
        self, exception_class: type, error_message: str, mock_logger: Mock, mock_import_module: Mock
    ):
        """
        Test when import_module raises ImportError or ModuleNotFoundError
        """
        mock_import_module.side_effect = exception_class(error_message)

        result = get_extended_profile_model()

        self.assertIsNone(result)  # noqa: PT009
        mock_logger.warning.assert_called_once()
        self.assertIn("Could not load extended profile model", str(mock_logger.warning.call_args))  # noqa: PT009

    @override_settings(PROFILE_EXTENSION_FORM="myapp.forms.NonExistentForm")
    @patch("openedx.core.djangoapps.user_authn.views.registration_form.import_module")
    @patch("openedx.core.djangoapps.user_authn.views.registration_form.log")
    def test_get_extended_profile_model_attribute_error(self, mock_logger: Mock, mock_import_module: Mock):
        """
        Test when the form class doesn't exist in the module
        """
        mock_module = Mock(spec=[])
        mock_import_module.return_value = mock_module

        result = get_extended_profile_model()

        self.assertIsNone(result)  # noqa: PT009
        mock_logger.warning.assert_called_once()
        self.assertIn("Could not load extended profile model", str(mock_logger.warning.call_args))  # noqa: PT009

    @override_settings(PROFILE_EXTENSION_FORM=None, REGISTRATION_EXTENSION_FORM="myapp.forms.LegacyForm")
    def test_get_extended_profile_model_with_deprecated_setting_returns_none(self):
        """
        Test that using REGISTRATION_EXTENSION_FORM returns None (maintains old behavior).

        This ensures backward compatibility: sites using REGISTRATION_EXTENSION_FORM
        will NOT get the new model-based profile capabilities. They continue using
        the old UserProfile.meta field approach.
        """
        result = get_extended_profile_model()

        self.assertIsNone(result)  # noqa: PT009


@ddt.ddt
class TestGetRegistrationExtensionForm(TestCase):
    """
    Tests for get_registration_extension_form function
    """

    @ddt.data(None, "")
    def test_get_registration_extension_form_no_setting(self, setting_value: str | None):
        """
        Test when neither PROFILE_EXTENSION_FORM nor REGISTRATION_EXTENSION_FORM is configured
        """
        with override_settings(PROFILE_EXTENSION_FORM=setting_value, REGISTRATION_EXTENSION_FORM=setting_value):
            result = get_registration_extension_form()

        self.assertIsNone(result)  # noqa: PT009

    @override_settings(PROFILE_EXTENSION_FORM="myapp.forms.CustomProfileForm")
    @patch("openedx.core.djangoapps.user_authn.views.registration_form.import_module")
    def test_get_registration_extension_form_with_new_setting(self, mock_import_module: Mock):
        """
        Test loading form from PROFILE_EXTENSION_FORM (new setting)
        """
        mock_form_instance = Mock()
        mock_form_class = Mock(return_value=mock_form_instance)
        mock_module = Mock()
        mock_module.CustomProfileForm = mock_form_class
        mock_import_module.return_value = mock_module

        result = get_registration_extension_form(data={"field": "value"})

        self.assertEqual(result, mock_form_instance)  # noqa: PT009
        mock_import_module.assert_called_once_with("myapp.forms")
        mock_form_class.assert_called_once_with(data={"field": "value"})

    @override_settings(PROFILE_EXTENSION_FORM="myapp.forms.NewForm", REGISTRATION_EXTENSION_FORM="myapp.forms.OldForm")
    @patch("openedx.core.djangoapps.user_authn.views.registration_form.import_module")
    def test_get_registration_extension_form_new_setting_precedence(self, mock_import_module: Mock):
        """
        Test that PROFILE_EXTENSION_FORM takes precedence over REGISTRATION_EXTENSION_FORM
        """
        mock_form_instance = Mock()
        mock_form_class = Mock(return_value=mock_form_instance)
        mock_module = Mock()
        mock_module.NewForm = mock_form_class
        mock_import_module.return_value = mock_module

        result = get_registration_extension_form()

        self.assertEqual(result, mock_form_instance)  # noqa: PT009
        mock_import_module.assert_called_once_with("myapp.forms")

    @override_settings(PROFILE_EXTENSION_FORM=None, REGISTRATION_EXTENSION_FORM="myapp.forms.LegacyForm")
    @patch("openedx.core.djangoapps.user_authn.views.registration_form.import_module")
    @patch("openedx.core.djangoapps.user_authn.views.registration_form.log")
    def test_get_registration_extension_form_deprecation_warning(self, mock_logger: Mock, mock_import_module: Mock):
        """
        Test that using REGISTRATION_EXTENSION_FORM logs a deprecation warning
        """
        mock_form_instance = Mock()
        mock_form_class = Mock(return_value=mock_form_instance)
        mock_module = Mock()
        mock_module.LegacyForm = mock_form_class
        mock_import_module.return_value = mock_module

        result = get_registration_extension_form()

        self.assertEqual(result, mock_form_instance)  # noqa: PT009
        deprecation_calls = [call for call in mock_logger.warning.call_args_list if "deprecated" in str(call).lower()]
        self.assertGreater(len(deprecation_calls), 0, "Expected a deprecation warning to be logged")  # noqa: PT009
        warning_message = str(deprecation_calls[0])
        self.assertIn("REGISTRATION_EXTENSION_FORM", warning_message)  # noqa: PT009
        self.assertIn("PROFILE_EXTENSION_FORM", warning_message)  # noqa: PT009

    @override_settings(PROFILE_EXTENSION_FORM="invalid.path")
    @patch("openedx.core.djangoapps.user_authn.views.registration_form.import_module")
    @patch("openedx.core.djangoapps.user_authn.views.registration_form.log")
    def test_get_registration_extension_form_import_error(self, mock_logger: Mock, mock_import_module: Mock):
        """
        Test when form import fails
        """
        mock_import_module.side_effect = ImportError("Module not found")

        result = get_registration_extension_form()

        self.assertIsNone(result)  # noqa: PT009
        error_calls = mock_logger.error.call_args_list
        self.assertGreater(len(error_calls), 0, "Expected an error to be logged")  # noqa: PT009

    @override_settings(PROFILE_EXTENSION_FORM="invalid_path_without_dot")
    @patch("openedx.core.djangoapps.user_authn.views.registration_form.log")
    def test_get_registration_extension_form_malformed_path(self, mock_logger: Mock):
        """
        Test when setting value doesn't have proper format (no dot separator)
        """
        result = get_registration_extension_form()

        self.assertIsNone(result)  # noqa: PT009

        error_calls = mock_logger.error.call_args_list
        self.assertGreater(len(error_calls), 0, "Expected an error to be logged")  # noqa: PT009
