"""
Tests for SAML provider configuration to skip optional checkboxes in registration form.
"""

import logging
from unittest import mock

from django.test import TestCase, override_settings
from django.test.client import RequestFactory

from common.djangoapps.third_party_auth.tests.factories import SAMLProviderConfigFactory
from common.djangoapps.third_party_auth.tests.testutil import simulate_running_pipeline
from openedx.core.djangoapps.user_authn.views.registration_form import RegistrationFormFactory

log = logging.getLogger(__name__)


class SAMLProviderOptionalCheckboxTest(TestCase):
    """
    Tests for SAML provider configuration options to skip optional checkboxes
    (marketing emails, etc.) during registration.
    """

    def setUp(self):
        """Set up test fixtures."""
        super().setUp()
        self.factory = RequestFactory()

    def _create_request(self):
        """Create a test request with session support."""
        from importlib import import_module
        from django.conf import settings

        request = self.factory.get('/register')
        engine = import_module(settings.SESSION_ENGINE)
        session_key = None
        request.session = engine.SessionStore(session_key)
        return request

    @override_settings(
        MARKETING_EMAILS_OPT_IN=True,
        REGISTRATION_EXTRA_FIELDS={},
        REGISTRATION_FIELD_ORDER=[]
    )
    @mock.patch(
        'openedx.core.djangoapps.user_authn.views.registration_form.third_party_auth.is_enabled',
        return_value=True,
    )
    def test_marketing_checkbox_hidden_with_marketing_opt_in_setting(self, mock_is_enabled):
        """
        Test that marketing checkbox is hidden when SAML provider config
        has skip_registration_optional_checkboxes=True, even when the global
        MARKETING_EMAILS_OPT_IN setting is True (production scenario).
        """
        # Create a SAML provider config that skips optional checkboxes
        saml_config = SAMLProviderConfigFactory(
            skip_registration_optional_checkboxes=True
        )

        # Simulate running SAML authentication pipeline
        with simulate_running_pipeline(
            "common.djangoapps.third_party_auth.pipeline",
            "tpa-saml",
            idp_name=saml_config.slug,
            email="testuser@example.com",
            fullname="Test User",
            username="testuser"
        ):
            request = self._create_request()
            form_factory = RegistrationFormFactory()
            form_desc = form_factory.get_registration_form(request)

            # Find the marketing_emails_opt_in field
            marketing_field = None
            for field in form_desc.fields:
                if field['name'] == 'marketing_emails_opt_in':
                    marketing_field = field
                    break

            # Even though MARKETING_EMAILS_OPT_IN=True globally,
            # the field should not be present when skipped via SAML config
            self.assertIsNone(
                marketing_field,
                "marketing_emails_opt_in field should not be present when skipped via SAML config, "
                "even when MARKETING_EMAILS_OPT_IN=True"
            )

    @override_settings(
        MARKETING_EMAILS_OPT_IN=True,
        REGISTRATION_EXTRA_FIELDS={},
        REGISTRATION_FIELD_ORDER=[]
    )
    @mock.patch(
        'openedx.core.djangoapps.user_authn.views.registration_form.third_party_auth.is_enabled',
        return_value=True,
    )
    def test_marketing_checkbox_visible_with_marketing_opt_in_setting_no_skip(self, mock_is_enabled):
        """
        Test that marketing checkbox is visible when MARKETING_EMAILS_OPT_IN=True
        and SAML provider config does NOT have skip_registration_optional_checkboxes=True.
        """
        # Create a SAML provider config that doesn't skip checkboxes
        saml_config = SAMLProviderConfigFactory(
            skip_registration_optional_checkboxes=False
        )

        # Simulate running SAML authentication pipeline
        with simulate_running_pipeline(
            "common.djangoapps.third_party_auth.pipeline",
            "tpa-saml",
            idp_name=saml_config.slug,
            email="testuser@example.com",
            fullname="Test User",
            username="testuser"
        ):
            request = self._create_request()
            form_factory = RegistrationFormFactory()
            form_desc = form_factory.get_registration_form(request)

            # Find the marketing_emails_opt_in field
            marketing_field = None
            for field in form_desc.fields:
                if field['name'] == 'marketing_emails_opt_in':
                    marketing_field = field
                    break

            # When MARKETING_EMAILS_OPT_IN=True and SAML config doesn't skip,
            # the field should be present
            self.assertIsNotNone(
                marketing_field,
                "marketing_emails_opt_in field should be present when MARKETING_EMAILS_OPT_IN=True "
                "and SAML config does not skip checkboxes"
            )
            # The field should be visible (exposed)
            self.assertTrue(
                marketing_field.get('exposed', False),
                "Marketing checkbox should be visible when SAML config does not skip checkboxes"
            )
            # The field should be optional (not required) when MARKETING_EMAILS_OPT_IN=True
            self.assertFalse(
                marketing_field.get('required', False),
                "Marketing checkbox should be optional when MARKETING_EMAILS_OPT_IN=True"
            )

    @override_settings(
        REGISTRATION_EXTRA_FIELDS={
            "marketing_emails_opt_in": "optional"
        },
        REGISTRATION_FIELD_ORDER=[]
    )
    def test_marketing_checkbox_optional_without_saml_config(self):
        """
        Test that marketing checkbox is optional by default when REGISTRATION_EXTRA_FIELDS
        is set to optional, regardless of SAML config.
        """
        request = self._create_request()
        form_factory = RegistrationFormFactory()
        form_desc = form_factory.get_registration_form(request)

        # Find the marketing_emails_opt_in field
        marketing_field = None
        for field in form_desc.fields:
            if field['name'] == 'marketing_emails_opt_in':
                marketing_field = field
                break

        self.assertIsNotNone(marketing_field, "marketing_emails_opt_in field not found")
        # When REGISTRATION_EXTRA_FIELDS is optional, the field should not be required
        self.assertFalse(marketing_field.get('required', False))
        # The field should be visible (exposed=True) by default
        self.assertTrue(
            marketing_field.get('exposed', False),
            "Marketing checkbox should be visible when no SAML config skips it"
        )

    @override_settings(
        REGISTRATION_EXTRA_FIELDS={
            "marketing_emails_opt_in": "required"
        },
        REGISTRATION_FIELD_ORDER=[]
    )
    @mock.patch(
        'openedx.core.djangoapps.user_authn.views.registration_form.third_party_auth.is_enabled',
        return_value=True,
    )
    def test_marketing_checkbox_optional_with_saml_config(self, mock_is_enabled):
        """
        Test that marketing checkbox is hidden when SAML provider config
        has skip_registration_optional_checkboxes=True, overriding global settings.
        """
        # Create a SAML provider config that skips optional checkboxes
        saml_config = SAMLProviderConfigFactory(
            skip_registration_optional_checkboxes=True
        )

        # Simulate running SAML authentication pipeline
        with simulate_running_pipeline(
            "common.djangoapps.third_party_auth.pipeline",
            "tpa-saml",
            idp_name=saml_config.slug,
            email="testuser@example.com",
            fullname="Test User",
            username="testuser"
        ):
            request = self._create_request()
            form_factory = RegistrationFormFactory()
            form_desc = form_factory.get_registration_form(request)

            # Find the marketing_emails_opt_in field
            marketing_field = None
            for field in form_desc.fields:
                if field['name'] == 'marketing_emails_opt_in':
                    marketing_field = field
                    break

            # When SAML provider config sets skip_registration_optional_checkboxes=True,
            # the field should not be present in the form at all
            self.assertIsNone(
                marketing_field,
                "marketing_emails_opt_in field should not be present when skipped via SAML config"
            )

    @override_settings(
        REGISTRATION_EXTRA_FIELDS={
            "marketing_emails_opt_in": "required"
        },
        REGISTRATION_FIELD_ORDER=[]
    )
    @mock.patch(
        'openedx.core.djangoapps.user_authn.views.registration_form.third_party_auth.is_enabled',
        return_value=True,
    )
    def test_marketing_checkbox_still_optional_when_config_false(self, mock_is_enabled):
        """
        Test that when SAML provider config has skip_registration_optional_checkboxes=False,
        the global REGISTRATION_EXTRA_FIELDS setting is used (required in this case).
        """
        # Create a SAML provider config that doesn't skip checkboxes (default behavior)
        saml_config = SAMLProviderConfigFactory(
            skip_registration_optional_checkboxes=False
        )

        # Simulate running SAML authentication pipeline
        with simulate_running_pipeline(
            "common.djangoapps.third_party_auth.pipeline",
            "tpa-saml",
            idp_name=saml_config.slug,
            email="testuser@example.com",
            fullname="Test User",
            username="testuser"
        ):
            request = self._create_request()
            form_factory = RegistrationFormFactory()
            form_desc = form_factory.get_registration_form(request)

            # Find the marketing_emails_opt_in field
            marketing_field = None
            for field in form_desc.fields:
                if field['name'] == 'marketing_emails_opt_in':
                    marketing_field = field
                    break

            self.assertIsNotNone(marketing_field, "marketing_emails_opt_in field not found")
            # When SAML provider config sets skip_registration_optional_checkboxes=False,
            # it should use the global setting (required in this test)
            self.assertTrue(marketing_field.get('required', False))
            # The field should be visible (exposed=True) when config is False
            self.assertTrue(
                marketing_field.get('exposed', False),
                "Marketing checkbox should be visible when SAML config is False"
            )

    @override_settings(
        REGISTRATION_EXTRA_FIELDS={
            "marketing_emails_opt_in": "required"
        },
        REGISTRATION_FIELD_ORDER=[]
    )
    @mock.patch(
        'openedx.core.djangoapps.user_authn.views.registration_form.third_party_auth.is_enabled',
        return_value=True,
    )
    def test_marketing_checkbox_hidden_with_saml_config(self, mock_is_enabled):
        """
        Test that when marketing checkbox is skipped via SAML provider config,
        it is not present in the form at all (completely hidden).
        """
        # Create a SAML provider config that skips optional checkboxes
        saml_config = SAMLProviderConfigFactory(
            skip_registration_optional_checkboxes=True
        )

        # Simulate running SAML authentication pipeline
        with simulate_running_pipeline(
            "common.djangoapps.third_party_auth.pipeline",
            "tpa-saml",
            idp_name=saml_config.slug,
            email="testuser@example.com",
            fullname="Test User",
            username="testuser"
        ):
            request = self._create_request()
            form_factory = RegistrationFormFactory()
            form_desc = form_factory.get_registration_form(request)

            # Find the marketing_emails_opt_in field
            marketing_field = None
            for field in form_desc.fields:
                if field['name'] == 'marketing_emails_opt_in':
                    marketing_field = field
                    break

            # When SAML provider config sets skip_registration_optional_checkboxes=True,
            # the field should not be present in the form at all
            self.assertIsNone(
                marketing_field,
                "marketing_emails_opt_in field should not be present when skipped via SAML config"
            )
