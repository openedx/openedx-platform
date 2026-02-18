"""
Tests for SAML provider configuration of optional email checkboxes in registration form.
"""

import json
import logging
from django.test import TestCase, override_settings
from django.test.client import RequestFactory
from django.urls import reverse

from common.djangoapps.third_party_auth.tests.factories import SAMLProviderConfigFactory
from common.djangoapps.third_party_auth.tests.testutil import simulate_running_pipeline
from openedx.core.djangoapps.user_authn.views.registration_form import RegistrationFormFactory

log = logging.getLogger(__name__)


class SAMLProviderOptionalCheckboxTest(TestCase):
    """
    Tests for SAML provider configuration options to make marketing and research
    email checkboxes optional during registration.
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

    @override_settings(
        ENABLE_THIRD_PARTY_AUTH=True,
        REGISTRATION_EXTRA_FIELDS={
            "marketing_emails_opt_in": "required"
        },
        REGISTRATION_FIELD_ORDER=[]
    )
    def test_marketing_checkbox_optional_with_saml_config(self):
        """
        Test that marketing checkbox becomes optional when SAML provider config
        has marketing_emails_opt_in_optional=True, overriding global settings.
        """
        # Create a SAML provider config with optional marketing emails
        saml_config = SAMLProviderConfigFactory(
            marketing_emails_opt_in_optional=True
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
            # When SAML provider config sets marketing_emails_opt_in_optional=True,
            # the field should not be required
            self.assertFalse(marketing_field.get('required', False))

    @override_settings(
        ENABLE_THIRD_PARTY_AUTH=True,
        REGISTRATION_EXTRA_FIELDS={
            "marketing_emails_opt_in": "required"
        },
        REGISTRATION_FIELD_ORDER=[]
    )
    def test_marketing_checkbox_still_optional_when_config_false(self):
        """
        Test that when SAML provider config has marketing_emails_opt_in_optional=False,
        the global REGISTRATION_EXTRA_FIELDS setting is used (required in this case).
        """
        # Create a SAML provider config with required marketing emails (default behavior)
        saml_config = SAMLProviderConfigFactory(
            marketing_emails_opt_in_optional=False
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
            # When SAML provider config sets marketing_emails_opt_in_optional=False,
            # it should use the global setting (required in this test)
            self.assertTrue(marketing_field.get('required', False))

    @override_settings(
        ENABLE_THIRD_PARTY_AUTH=True,
        REGISTRATION_EXTRA_FIELDS={
            "marketing_emails_opt_in": "required"
        },
        REGISTRATION_FIELD_ORDER=[]
    )
    def test_marketing_checkbox_default_false_with_saml_config(self):
        """
        Test that when marketing checkbox is made optional via SAML provider config,
        the default value is set to False instead of True.
        """
        # Create a SAML provider config with optional marketing emails
        saml_config = SAMLProviderConfigFactory(
            marketing_emails_opt_in_optional=True
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
            # When SAML provider config sets marketing_emails_opt_in_optional=True,
            # the default value should be False
            log.info("Marketing field: %s", json.dumps(marketing_field, default=str))
            log.info("SAML config slug: %s, marketing_emails_opt_in_optional: %s",
                     saml_config.slug, saml_config.marketing_emails_opt_in_optional)
            self.assertFalse(
                marketing_field.get('defaultValue', True),
                "Marketing checkbox default should be False when optional via SAML config"
            )
