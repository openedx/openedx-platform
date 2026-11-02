"""
Unit tests for the IdentityServer3 OAuth2 Backend
"""
import json
import ddt
from common.djangoapps.third_party_auth.identityserver3 import IdentityServer3
from common.djangoapps.third_party_auth.tests import testutil
from common.djangoapps.third_party_auth.tests.utils import skip_unless_thirdpartyauth
from common.test.utils import assert_dict_contains_subset


@skip_unless_thirdpartyauth()
@ddt.ddt
class IdentityServer3Test(testutil.TestCase):
    """
    Unit tests for the IdentityServer3 OAuth2 Backend
    """

    def setUp(self):
        super().setUp()
        self.id3_instance = IdentityServer3()
        self.response = {
            "sub": "020cadec-919a-4b06-845e-57915bf76826",
            "refresh_token": "xyz",
            "token_type": "bearer",
            "name": "Edx Openid",
            "session_state": "fcf85c29-5ecf-4edb-b29b-72ce9871cdf7",
            "refresh_expires_in": 1800,
            "family_name": "Openid",
            "scope": "openid email profile",
            "email_verified": False,
            "given_name": "Edx",
            "email": "edxopenid@example.com",
            "not-before-policy": 0,
            "preferred_username": "edxopenid",
            "expires_in": 300
        }

    def test_proper_get_of_user_id(self):
        """
        make sure the "sub" claim works properly to grab user Id
        """
        response = {"sub": 1, "email": "example@example.com"}
        assert self.id3_instance.get_user_id({}, response) == 1

    def test_key_error_thrown_with_no_sub(self):
        """
        test that a KeyError is thrown if the "sub" claim does not exist
        """
        response = {"id": 1}
        assert self.id3_instance.get_user_id({}, response) is None

    def test_proper_config_access(self):
        """
        test that the IdentityServer3 model properly grabs OAuth2Configs
        """
        provider_config = self.configure_identityServer3_provider(backend_name="identityServer3")
        assert self.id3_instance.get_config() == provider_config

    def test_config_after_updating(self):
        """
        Make sure when the OAuth2Config for this backend is updated, the new config is properly grabbed
        """
        original_provider_config = self.configure_identityServer3_provider(enabled=True, slug="original")
        updated_provider_config = self.configure_identityServer3_provider(
            slug="updated",
            backend_name="identityServer3"
        )
        assert self.id3_instance.get_config() == updated_provider_config
        assert self.id3_instance.get_config() != original_provider_config

    @ddt.data(
        ('first_name_claim_key', 'given_name', 'first_name', 'Edx'),
        ('last_name_claim_key', 'family_name', 'last_name', 'Openid'),
        ('full_name_claim_key', 'name', 'fullname', 'Edx Openid'),
        ('email_claim_key', 'email', 'email', 'edxopenid@example.com'),
        ('username_claim_key', 'preferred_username', 'username', 'edxopenid'),
        ('first_name_claim_key', 'family_name', 'first_name', 'Openid'),
        ('last_name_claim_key', 'given_name', 'last_name', 'Edx'),
        ('email_claim_key', 'name', 'email', 'Edx Openid'),
        ('username_claim_key', 'given_name', 'username', 'Edx'),
    )
    @ddt.unpack
    def test_user_details_and_settings(self, setting_field_key, setting_field_value, output_name, output_value):
        """
        Test user details are picked based on the field mapping defined in settings
        """
        provider_config = self.configure_identityServer3_provider(
            enabled=True,
            other_settings=json.dumps({
                setting_field_key: setting_field_value,
            })
        )
        assert provider_config.backend_class().get_user_details(self.response)[output_name] == output_value

    def test_user_details_without_settings(self):
        """
        Test user details fields are mapped to default keys
        """
        provider_config = self.configure_identityServer3_provider(enabled=True)
        assert_dict_contains_subset(
            self,
            {
                "username": "Edx",
                "email": "edxopenid@example.com",
                "first_name": "Edx",
                "last_name": "Openid",
                "fullname": "Edx Openid"
            },
            provider_config.backend_class().get_user_details(self.response),
        )
