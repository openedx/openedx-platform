"""
Unit tests for forms in the accounts API
"""

from unittest.mock import Mock, patch

from django.core.exceptions import ObjectDoesNotExist
from django.test import TestCase

from common.djangoapps.student.tests.factories import UserFactory
from openedx.core.djangoapps.user_api.accounts.forms import (
    extract_extended_profile_fields_data,
    get_extended_profile_form,
    validate_and_get_extended_profile_form,
)


class TestExtractExtendedProfileFieldsData(TestCase):
    """
    Tests for extract_extended_profile_fields_data function
    """

    def test_extract_valid_extended_profile_data(self):
        """
        Test extraction of valid extended profile data
        """
        extended_profile = [
            {"field_name": "department", "field_value": "Engineering"},
            {"field_name": "title", "field_value": "Software Engineer"},
        ]

        extracted_data, errors = extract_extended_profile_fields_data(extended_profile)

        self.assertEqual(errors, {})
        self.assertEqual(extracted_data, {"department": "Engineering", "title": "Software Engineer"})

    def test_extract_extended_profile_with_none_value(self):
        """
        Test that None values are skipped
        """
        extended_profile = [
            {"field_name": "department", "field_value": "Engineering"},
            {"field_name": "title", "field_value": None},
        ]

        extracted_data, errors = extract_extended_profile_fields_data(extended_profile)

        self.assertEqual(errors, {})
        self.assertEqual(extracted_data, {"department": "Engineering"})

    def test_extract_extended_profile_with_empty_string(self):
        """
        Test that empty strings are included
        """
        extended_profile = [
            {"field_name": "department", "field_value": ""},
            {"field_name": "title", "field_value": "Engineer"},
        ]

        extracted_data, errors = extract_extended_profile_fields_data(extended_profile)

        self.assertEqual(errors, {})
        self.assertEqual(extracted_data, {"department": "", "title": "Engineer"})

    def test_extract_extended_profile_not_a_list(self):
        """
        Test error when extended_profile is not a list
        """
        extended_profile = "not a list"

        extracted_data, errors = extract_extended_profile_fields_data(extended_profile)

        self.assertEqual(extracted_data, {})
        self.assertIn("extended_profile", errors)
        self.assertEqual(errors["extended_profile"]["developer_message"], "extended_profile must be a list")

    def test_extract_extended_profile_with_invalid_field_data(self):
        """
        Test that invalid field data entries are skipped (logged but not errored)
        """
        extended_profile = [
            {"field_name": "department", "field_value": "Engineering"},
            "invalid entry",  # Not a dict
            {"field_name": "title", "field_value": "Engineer"},
        ]

        extracted_data, errors = extract_extended_profile_fields_data(extended_profile)

        # Invalid entry should be skipped, but valid ones should be extracted
        self.assertEqual(errors, {})
        self.assertEqual(extracted_data, {"department": "Engineering", "title": "Engineer"})

    def test_extract_extended_profile_missing_field_name(self):
        """
        Test that entries without field_name are skipped
        """
        extended_profile = [
            {"field_name": "department", "field_value": "Engineering"},
            {"field_value": "Engineer"},  # Missing field_name
        ]

        extracted_data, errors = extract_extended_profile_fields_data(extended_profile)

        self.assertEqual(errors, {})
        self.assertEqual(extracted_data, {"department": "Engineering"})

    def test_extract_extended_profile_empty_list(self):
        """
        Test that an empty list returns empty data
        """
        extended_profile = []

        extracted_data, errors = extract_extended_profile_fields_data(extended_profile)

        self.assertEqual(errors, {})
        self.assertEqual(extracted_data, {})


class TestGetExtendedProfileForm(TestCase):
    """
    Tests for get_extended_profile_form function
    """

    def setUp(self):
        super().setUp()
        self.user = UserFactory.create()

    @patch("openedx.core.djangoapps.user_api.accounts.forms.get_extended_profile_model")
    def test_get_extended_profile_form_no_model_configured(self, mock_get_model: Mock):
        """
        Test when no extended profile model is configured
        """
        mock_get_model.side_effect = ImportError("No model configured")
        extended_profile_fields_data = {"department": "Engineering"}

        form, errors = get_extended_profile_form(extended_profile_fields_data, self.user)

        self.assertIsNone(form)
        self.assertEqual(errors, {})

    @patch("openedx.core.djangoapps.user_api.accounts.forms.get_extended_profile_model")
    def test_get_extended_profile_form_model_has_no_objects(self, mock_get_model: Mock):
        """
        Test when model doesn't have objects attribute (AttributeError)
        """
        mock_get_model.return_value = None
        extended_profile_fields_data = {"department": "Engineering"}

        form, errors = get_extended_profile_form(extended_profile_fields_data, self.user)

        self.assertIsNone(form)
        self.assertEqual(errors, {})

    @patch("openedx.core.djangoapps.user_api.accounts.forms.get_registration_extension_form")
    @patch("openedx.core.djangoapps.user_api.accounts.forms.get_extended_profile_model")
    def test_get_extended_profile_form_with_existing_instance(self, mock_get_model: Mock, mock_get_form: Mock):
        """
        Test form creation with an existing profile instance
        """
        mock_model = Mock()
        mock_instance = Mock()
        mock_model.objects.get.return_value = mock_instance
        mock_get_model.return_value = mock_model
        mock_form_instance = Mock()
        mock_form_instance.is_valid.return_value = True
        mock_get_form.return_value = mock_form_instance
        extended_profile_fields_data = {"department": "Engineering"}

        form, errors = get_extended_profile_form(extended_profile_fields_data, self.user)

        self.assertEqual(form, mock_form_instance)
        self.assertEqual(errors, {})
        mock_model.objects.get.assert_called_once_with(user=self.user)
        mock_get_form.assert_called_once_with(data=extended_profile_fields_data, instance=mock_instance)

    @patch("openedx.core.djangoapps.user_api.accounts.forms.get_registration_extension_form")
    @patch("openedx.core.djangoapps.user_api.accounts.forms.get_extended_profile_model")
    def test_get_extended_profile_form_without_existing_instance(self, mock_get_model: Mock, mock_get_form: Mock):
        """
        Test form creation for a new profile (no existing instance)
        """
        mock_model = Mock()
        mock_model.DoesNotExist = ObjectDoesNotExist
        mock_model.objects.get.side_effect = ObjectDoesNotExist("Profile not found")
        mock_get_model.return_value = mock_model
        mock_form_instance = Mock()
        mock_form_instance.is_valid.return_value = True
        mock_get_form.return_value = mock_form_instance
        extended_profile_fields_data = {"department": "Engineering"}

        form, errors = get_extended_profile_form(extended_profile_fields_data, self.user)

        self.assertEqual(form, mock_form_instance)
        self.assertEqual(errors, {})
        mock_model.objects.get.assert_called_once_with(user=self.user)
        mock_get_form.assert_called_once_with(data=extended_profile_fields_data)

    @patch("openedx.core.djangoapps.user_api.accounts.forms.get_registration_extension_form")
    @patch("openedx.core.djangoapps.user_api.accounts.forms.get_extended_profile_model")
    def test_get_extended_profile_form_validation_errors(self, _: Mock, mock_get_form: Mock):
        """
        Test when form validation fails
        """
        mock_form_instance = Mock()
        mock_form_instance.is_valid.return_value = False
        mock_form_instance.errors = {"department": ["This field is required"], "title": ["Invalid value"]}
        mock_get_form.return_value = mock_form_instance
        extended_profile_fields_data = {}

        form, errors = get_extended_profile_form(extended_profile_fields_data, self.user)

        self.assertEqual(form, mock_form_instance)
        self.assertIn("department", errors)
        self.assertIn("title", errors)
        self.assertEqual(errors["department"]["user_message"], "This field is required")
        self.assertEqual(errors["title"]["user_message"], "Invalid value")

    @patch("openedx.core.djangoapps.user_api.accounts.forms.get_registration_extension_form")
    def test_get_extended_profile_form_returns_none(self, mock_get_form: Mock):
        """
        Test when get_registration_extension_form returns None
        """
        mock_get_form.return_value = None
        extended_profile_fields_data = {"department": "Engineering"}

        with patch("openedx.core.djangoapps.user_api.accounts.forms.get_extended_profile_model"):
            form, errors = get_extended_profile_form(extended_profile_fields_data, self.user)

        self.assertIsNone(form)
        self.assertEqual(errors, {})

    @patch("openedx.core.djangoapps.user_api.accounts.forms.get_registration_extension_form")
    def test_get_extended_profile_form_exception_during_creation(self, mock_get_form: Mock):
        """
        Test when an unexpected exception occurs during form creation
        """
        mock_get_form.side_effect = Exception("Unexpected error")
        extended_profile_fields_data = {"department": "Engineering"}

        with patch("openedx.core.djangoapps.user_api.accounts.forms.get_extended_profile_model"):
            form, errors = get_extended_profile_form(extended_profile_fields_data, self.user)

        self.assertIsNone(form)
        self.assertIn("extended_profile", errors)
        self.assertIn("Error creating custom form", errors["extended_profile"]["developer_message"])


class TestValidateAndGetExtendedProfileForm(TestCase):
    """
    Tests for validate_and_get_extended_profile_form function
    """

    def setUp(self):
        super().setUp()
        self.user = UserFactory.create()

    @patch("openedx.core.djangoapps.user_api.accounts.forms.get_extended_profile_form")
    @patch("openedx.core.djangoapps.user_api.accounts.forms.extract_extended_profile_fields_data")
    def test_validate_with_valid_data(self, mock_extract: Mock, mock_get_form: Mock):
        """
        Test successful validation with valid data
        """
        extended_profile_data = [{"field_name": "department", "field_value": "Engineering"}]
        mock_extract.return_value = ({"department": "Engineering"}, {})
        mock_form = Mock()
        mock_get_form.return_value = (mock_form, {})

        form, errors = validate_and_get_extended_profile_form(extended_profile_data, self.user)

        self.assertEqual(form, mock_form)
        self.assertEqual(errors, {})
        mock_extract.assert_called_once_with(extended_profile_data)
        mock_get_form.assert_called_once_with({"department": "Engineering"}, self.user)

    @patch("openedx.core.djangoapps.user_api.accounts.forms.extract_extended_profile_fields_data")
    def test_validate_with_extraction_errors(self, mock_extract: Mock):
        """
        Test when extraction fails
        """
        extended_profile_data = "invalid data"
        mock_extract.return_value = ({}, {"extended_profile": {"developer_message": "Invalid format"}})

        form, errors = validate_and_get_extended_profile_form(extended_profile_data, self.user)

        self.assertIsNone(form)
        self.assertIn("extended_profile", errors)

    @patch("openedx.core.djangoapps.user_api.accounts.forms.extract_extended_profile_fields_data")
    def test_validate_with_empty_data(self, mock_extract: Mock):
        """
        Test when extracted data is empty
        """
        extended_profile_data = []
        mock_extract.return_value = ({}, {})

        form, errors = validate_and_get_extended_profile_form(extended_profile_data, self.user)

        self.assertIsNone(form)
        self.assertEqual(errors, {})

    @patch("openedx.core.djangoapps.user_api.accounts.forms.get_extended_profile_form")
    @patch("openedx.core.djangoapps.user_api.accounts.forms.extract_extended_profile_fields_data")
    def test_validate_with_form_errors(self, mock_extract: Mock, mock_get_form: Mock):
        """
        Test when form validation fails
        """
        extended_profile_data = [{"field_name": "department", "field_value": ""}]
        mock_extract.return_value = ({"department": ""}, {})
        mock_form = Mock()
        form_errors = {"department": {"developer_message": "Required field"}}
        mock_get_form.return_value = (mock_form, form_errors)

        form, errors = validate_and_get_extended_profile_form(extended_profile_data, self.user)

        self.assertEqual(form, mock_form)
        self.assertIn("department", errors)

    @patch("openedx.core.djangoapps.user_api.accounts.forms.get_extended_profile_form")
    @patch("openedx.core.djangoapps.user_api.accounts.forms.extract_extended_profile_fields_data")
    def test_validate_merges_errors(self, mock_extract: Mock, mock_get_form: Mock):
        """
        Test that extraction and form errors are merged
        """
        extended_profile_data = [{"field_name": "department", "field_value": "Engineering"}]
        mock_extract.return_value = ({"department": "Engineering"}, {})
        mock_form = Mock()
        form_errors = {"title": {"developer_message": "Required field"}}
        mock_get_form.return_value = (mock_form, form_errors)

        form, errors = validate_and_get_extended_profile_form(extended_profile_data, self.user)

        self.assertEqual(form, mock_form)
        self.assertIn("title", errors)
