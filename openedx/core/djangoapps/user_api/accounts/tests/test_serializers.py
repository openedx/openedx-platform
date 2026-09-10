"""
Test cases to cover Accounts-related serializers of the User API application
"""

import logging
from unittest.mock import Mock, patch

from django.core.exceptions import ObjectDoesNotExist
from django.test import TestCase
from django.test.client import RequestFactory
from testfixtures import LogCapture

from common.djangoapps.student.models import UserProfile
from common.djangoapps.student.tests.factories import UserFactory
from openedx.core.djangoapps.user_api.accounts.serializers import UserReadOnlySerializer, get_extended_profile

LOGGER_NAME = "openedx.core.djangoapps.user_api.accounts.serializers"


class UserReadOnlySerializerTest(TestCase):  # pylint: disable=missing-class-docstring
    def setUp(self):
        super().setUp()
        request_factory = RequestFactory()
        self.request = request_factory.get('/api/user/v1/accounts/')
        self.user = UserFactory.build(username='test_user', email='test_user@test.com')
        self.user.save()
        self.config = {
            "default_visibility": "public",
            "public_fields": [
                'email', 'name', 'username'
            ],
        }

    def test_serializer_data(self):
        """
        Test serializer return data properly.
        """
        UserProfile.objects.create(user=self.user, name='test name')
        data = UserReadOnlySerializer(self.user, configuration=self.config, context={'request': self.request}).data
        assert data['username'] == self.user.username
        assert data['name'] == 'test name'
        assert data['email'] == self.user.email

    def test_user_no_profile(self):
        """
        Test serializer return data properly when user does not have profile.
        """
        with LogCapture(LOGGER_NAME, level=logging.DEBUG) as logger:
            data = UserReadOnlySerializer(self.user, configuration=self.config, context={'request': self.request}).data
            logger.check(
                (LOGGER_NAME, 'WARNING', 'user profile for the user [test_user] does not exist')
            )

        assert data['username'] == self.user.username
        assert data['name'] is None


class GetExtendedProfileTest(TestCase):
    """
    Tests for get_extended_profile function
    """

    def setUp(self):
        super().setUp()
        self.user = UserFactory.create()
        self.user_profile = UserProfile.objects.get(user=self.user)

    @patch("openedx.core.djangoapps.user_api.accounts.serializers.configuration_helpers")
    @patch("openedx.core.djangoapps.user_api.accounts.serializers.get_profile_extension_model")
    def test_get_extended_profile_from_model(self, mock_get_model: Mock, mock_config_helpers: Mock):
        """
        Test getting extended profile from a custom model
        """
        mock_config_helpers.get_value.return_value = ["department", "title", "company"]
        mock_model = Mock()
        mock_instance = Mock()
        mock_instance.department = "Engineering"
        mock_instance.title = "Software Engineer"
        mock_instance.company = "EdX"
        mock_instance.user = self.user
        mock_model.objects.get.return_value = mock_instance
        mock_get_model.return_value = mock_model

        with patch("openedx.core.djangoapps.user_api.accounts.serializers.model_to_dict") as mock_model_to_dict:
            mock_model_to_dict.return_value = {
                "department": "Engineering",
                "title": "Software Engineer",
                "company": "EdX",
                "user": self.user.id,
            }

            result = get_extended_profile(self.user_profile)

        self.assertEqual(len(result), 3)  # noqa: PT009
        self.assertIn({"field_name": "department", "field_value": "Engineering"}, result)  # noqa: PT009
        self.assertIn({"field_name": "title", "field_value": "Software Engineer"}, result)  # noqa: PT009
        self.assertIn({"field_name": "company", "field_value": "EdX"}, result)  # noqa: PT009

    @patch("openedx.core.djangoapps.user_api.accounts.serializers.configuration_helpers")
    def test_get_extended_profile_no_model_configured_uses_meta(self, mock_config_helpers: Mock):
        """
        Test fallback to meta field when no PROFILE_EXTENSION_FORM is configured.
        """
        mock_config_helpers.get_value.return_value = ["department", "title"]
        self.user_profile.set_meta({"department": "Sales", "title": "Manager"})
        self.user_profile.save()

        result = get_extended_profile(self.user_profile)

        self.assertEqual(len(result), 2)  # noqa: PT009
        self.assertIn({"field_name": "department", "field_value": "Sales"}, result)  # noqa: PT009
        self.assertIn({"field_name": "title", "field_value": "Manager"}, result)  # noqa: PT009

    @patch("openedx.core.djangoapps.user_api.accounts.serializers.configuration_helpers")
    @patch("openedx.core.djangoapps.user_api.accounts.serializers.get_profile_extension_model")
    def test_get_profile_extension_model_instance_missing_falls_back_to_meta(
        self, mock_get_model: Mock, mock_config_helpers: Mock
    ):
        """
        Test that when a model is configured but the user has no record in it,
        the function falls back to user_profile.meta instead of returning empty.
        """
        mock_config_helpers.get_value.return_value = ["department", "title"]
        mock_model = Mock()
        mock_model.objects.get.side_effect = ObjectDoesNotExist
        mock_get_model.return_value = mock_model

        self.user_profile.set_meta({"department": "Sales", "title": "Manager"})
        self.user_profile.save()

        result = get_extended_profile(self.user_profile)

        self.assertEqual(len(result), 2)  # noqa: PT009
        self.assertIn({"field_name": "department", "field_value": "Sales"}, result)  # noqa: PT009
        self.assertIn({"field_name": "title", "field_value": "Manager"}, result)  # noqa: PT009

    @patch("openedx.core.djangoapps.user_api.accounts.serializers.configuration_helpers")
    @patch("openedx.core.djangoapps.user_api.accounts.serializers.get_profile_extension_model")
    def test_get_extended_profile_field_absent_from_model_falls_back_to_meta(
        self, mock_get_model: Mock, mock_config_helpers: Mock
    ):
        """
        Test that a configured field not present in the model is read from meta.
        Model has 'department', but 'title' is only in meta and must be returned from there.
        """
        mock_config_helpers.get_value.return_value = ["department", "title"]
        mock_model = Mock()
        mock_get_model.return_value = mock_model

        self.user_profile.set_meta({"department": "Meta-Dept", "title": "Meta-Title"})
        self.user_profile.save()

        with patch("openedx.core.djangoapps.user_api.accounts.serializers.model_to_dict") as mock_model_to_dict:
            # Model only knows about 'department', but 'title' is absent.
            mock_model_to_dict.return_value = {"department": "Model-Dept"}

            result = get_extended_profile(self.user_profile)

        self.assertEqual(len(result), 2)  # noqa: PT009
        self.assertIn({"field_name": "department", "field_value": "Model-Dept"}, result)  # noqa: PT009
        self.assertIn({"field_name": "title", "field_value": "Meta-Title"}, result)  # noqa: PT009

    @patch("openedx.core.djangoapps.user_api.accounts.serializers.configuration_helpers")
    @patch("openedx.core.djangoapps.user_api.accounts.serializers.get_profile_extension_model")
    def test_get_profile_extension_model_takes_precedence_over_meta(
        self, mock_get_model: Mock, mock_config_helpers: Mock
    ):
        """
        Test that when both the model and meta have a value for the same field,
        the model value takes precedence.
        """
        mock_config_helpers.get_value.return_value = ["department"]
        mock_model = Mock()
        mock_get_model.return_value = mock_model

        self.user_profile.set_meta({"department": "Meta-Dept"})
        self.user_profile.save()

        with patch("openedx.core.djangoapps.user_api.accounts.serializers.model_to_dict") as mock_model_to_dict:
            mock_model_to_dict.return_value = {"department": "Model-Dept"}

            result = get_extended_profile(self.user_profile)

        self.assertEqual(len(result), 1)  # noqa: PT009
        self.assertIn({"field_name": "department", "field_value": "Model-Dept"}, result)  # noqa: PT009

    @patch("openedx.core.djangoapps.user_api.accounts.serializers.configuration_helpers")
    @patch("openedx.core.djangoapps.user_api.accounts.serializers.get_profile_extension_model")
    def test_get_extended_profile_no_model_configured(self, mock_get_model: Mock, mock_config_helpers: Mock):
        """
        Test fallback to meta field when no model is configured
        """
        mock_config_helpers.get_value.return_value = ["department", "title"]
        mock_get_model.return_value = None
        meta_data = {"department": "Marketing", "title": "Director"}
        self.user_profile.set_meta(meta_data)
        self.user_profile.save()

        result = get_extended_profile(self.user_profile)

        self.assertEqual(len(result), 2)  # noqa: PT009
        self.assertIn({"field_name": "department", "field_value": "Marketing"}, result)  # noqa: PT009
        self.assertIn({"field_name": "title", "field_value": "Director"}, result)  # noqa: PT009

    @patch("openedx.core.djangoapps.user_api.accounts.serializers.configuration_helpers")
    @patch("openedx.core.djangoapps.user_api.accounts.serializers.get_profile_extension_model")
    def test_get_extended_profile_empty_meta(self, mock_get_model: Mock, mock_config_helpers: Mock):
        """
        Test getting extended profile with empty meta field
        """
        mock_config_helpers.get_value.return_value = ["department", "title"]
        mock_get_model.return_value = None
        self.user_profile.meta = ""
        self.user_profile.save()

        result = get_extended_profile(self.user_profile)

        self.assertEqual(len(result), 2)  # noqa: PT009
        self.assertIn({"field_name": "department", "field_value": ""}, result)  # noqa: PT009
        self.assertIn({"field_name": "title", "field_value": ""}, result)  # noqa: PT009

    @patch("openedx.core.djangoapps.user_api.accounts.serializers.configuration_helpers")
    @patch("openedx.core.djangoapps.user_api.accounts.serializers.get_profile_extension_model")
    def test_get_extended_profile_invalid_json_in_meta(self, mock_get_model: Mock, mock_config_helpers: Mock):
        """
        Test getting extended profile with invalid JSON in meta field
        """
        mock_config_helpers.get_value.return_value = ["department", "title"]
        mock_get_model.return_value = None
        self.user_profile.meta = "invalid json {"
        self.user_profile.save()

        result = get_extended_profile(self.user_profile)

        self.assertEqual(len(result), 2)  # noqa: PT009
        self.assertIn({"field_name": "department", "field_value": ""}, result)  # noqa: PT009
        self.assertIn({"field_name": "title", "field_value": ""}, result)  # noqa: PT009

    @patch("openedx.core.djangoapps.user_api.accounts.serializers.configuration_helpers")
    @patch("openedx.core.djangoapps.user_api.accounts.serializers.get_profile_extension_model")
    def test_get_extended_profile_missing_fields(self, mock_get_model: Mock, mock_config_helpers: Mock):
        """
        Test getting extended profile when some configured fields are missing
        """
        mock_config_helpers.get_value.return_value = ["department", "title", "location"]
        mock_get_model.return_value = None
        meta_data = {"department": "HR", "title": "Recruiter"}
        self.user_profile.set_meta(meta_data)
        self.user_profile.save()

        result = get_extended_profile(self.user_profile)

        self.assertEqual(len(result), 3)  # noqa: PT009
        self.assertIn({"field_name": "department", "field_value": "HR"}, result)  # noqa: PT009
        self.assertIn({"field_name": "title", "field_value": "Recruiter"}, result)  # noqa: PT009
        self.assertIn({"field_name": "location", "field_value": ""}, result)  # noqa: PT009

    @patch("openedx.core.djangoapps.user_api.accounts.serializers.configuration_helpers")
    @patch("openedx.core.djangoapps.user_api.accounts.serializers.get_profile_extension_model")
    def test_get_extended_profile_no_configured_fields(self, mock_get_model: Mock, mock_config_helpers: Mock):
        """
        Test getting extended profile when no fields are configured
        """
        mock_config_helpers.get_value.return_value = []
        mock_get_model.return_value = None
        meta_data = {"department": "Finance", "title": "Analyst"}
        self.user_profile.set_meta(meta_data)
        self.user_profile.save()

        result = get_extended_profile(self.user_profile)

        self.assertEqual(len(result), 0)  # noqa: PT009
