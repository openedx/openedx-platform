"""
Tests for third_party_auth/models.py.
"""
import unittest

from django.test import TestCase, override_settings

from ..models import SAMLProviderConfig, clean_username
from .factories import SAMLProviderConfigFactory


class TestSamlProviderConfigModel(TestCase, unittest.TestCase):
    """
    Test model operations for the saml provider config model.
    """

    def setUp(self):
        super().setUp()
        self.saml_provider_config = SAMLProviderConfigFactory()

    def test_unique_entity_id_enforcement_for_non_current_configs(self):
        """
        Test that the unique entity ID enforcement does not apply to noncurrent configs
        """
        with self.assertLogs() as ctx:
            assert len(SAMLProviderConfig.objects.all()) == 1
            old_entity_id = self.saml_provider_config.entity_id
            self.saml_provider_config.entity_id = f'{self.saml_provider_config.entity_id}-ayylmao'
            self.saml_provider_config.save()

            # check that we now have two records, one non-current
            assert len(SAMLProviderConfig.objects.all()) == 2
            assert len(SAMLProviderConfig.objects.current_set()) == 1

            # Make sure we can use that old entity id
            SAMLProviderConfigFactory(entity_id=old_entity_id)

            # 7/21/22 : Disabling the exception on duplicate entity ID's because of existing data.
            # with pytest.raises(IntegrityError):
            bad_config = SAMLProviderConfig(entity_id=self.saml_provider_config.entity_id)
            bad_config.save()
        assert ctx.records[0].msg == f'Entity ID: {self.saml_provider_config.entity_id} already in use'

    @override_settings(FEATURES={'ENABLE_UNICODE_USERNAME': False})
    def test_clean_username_unicode_disabled(self):
        """
        Test the username cleaner function with unicode disabled
        """
        assert clean_username('ItJüstWòrks™') == 'ItJ_stW_rks'

    @override_settings(FEATURES={'ENABLE_UNICODE_USERNAME': True})
    def test_clean_username_unicode_enabled(self):
        """
        Test the username cleaner function with unicode enabled
        """
        assert clean_username('ItJüstWòrks™') == 'ItJüstWòrks'
