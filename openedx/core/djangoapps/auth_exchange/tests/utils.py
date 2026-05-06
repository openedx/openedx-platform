"""
Test utilities for OAuth access token exchange
"""


from django.conf import settings
from social_django.models import Partial, UserSocialAuth

from common.djangoapps.third_party_auth.tests.utils import ThirdPartyOAuthTestMixin

TPA_FEATURES_KEY = 'ENABLE_THIRD_PARTY_AUTH'
TPA_FEATURE_ENABLED = TPA_FEATURES_KEY in settings.FEATURES


class AccessTokenExchangeTestMixin(ThirdPartyOAuthTestMixin):
    """
    A mixin to define test cases for access token exchange. The following
    methods must be implemented by subclasses:
    * _assert_error(data, expected_error, expected_error_description)
    * _assert_success(data, expected_scopes)
    """
    def setUp(self):
        super().setUp()

        # Initialize to minimal data
        self.data = {
            "access_token": self.access_token,
            "client_id": self.client_id,
        }

    def _assert_error(self, _data, _expected_error, _expected_error_description, error_code):
        """
        Given request data, execute a test and check that the expected error
        was returned (along with any other appropriate assertions).
        """
        raise NotImplementedError()

    def _assert_success(self, data, expected_scopes, expected_logged_in_user=None):
        """
        Given request data, execute a test and check that the expected scopes
        were returned (along with any other appropriate assertions).
        """
        raise NotImplementedError()

    def _create_client(self):
        """
        Create an oauth2 client application using class defaults.
        """
        return self.create_public_client(self.user, self.client_id)

    def test_minimal(self):
        self._setup_provider_response(success=True)
        self._assert_success(self.data, expected_scopes=[])

    def test_scopes(self):
        self._setup_provider_response(success=True)
        self.data["scope"] = "profile email"
        self._assert_success(self.data, expected_scopes=["profile", "email"])

    def test_missing_fields(self):
        for field in ["access_token", "client_id"]:
            data = dict(self.data)
            del data[field]
            self._assert_error(data, "invalid_request", f"{field} is required")  # pylint: disable=no-value-for-parameter

    def test_invalid_client(self):
        self.data["client_id"] = "nonexistent_client"
        self._assert_error(  # pylint: disable=no-value-for-parameter
            self.data,
            "invalid_client",
            "nonexistent_client is not a valid client_id"
        )

    def test_confidential_client(self):
        self.data['client_id'] += '_confidential'
        self.oauth_client = self.create_confidential_client(self.user, self.data['client_id'])
        self._assert_error(  # pylint: disable=no-value-for-parameter
            self.data,
            "invalid_client",
            f"{self.client_id}_confidential is not a public client",
        )

    def test_inactive_user(self):
        self.user.is_active = False
        self.user.save()
        self._setup_provider_response(success=True)
        self._assert_success(self.data, expected_scopes=[])

    def test_invalid_acess_token(self):
        self._setup_provider_response(success=False)
        self._assert_error(self.data, "invalid_grant", "access_token is not valid")  # pylint: disable=no-value-for-parameter

    def test_no_linked_user(self):
        UserSocialAuth.objects.all().delete()
        Partial.objects.all().delete()
        self._setup_provider_response(success=True)
        self._assert_error(self.data, "invalid_grant", "access_token is not valid")  # pylint: disable=no-value-for-parameter

    def test_user_automatically_linked_by_email(self):
        UserSocialAuth.objects.all().delete()
        Partial.objects.all().delete()
        self._setup_provider_response(success=True, email=self.user.email)
        self._assert_success(self.data, expected_scopes=[])

    def test_inactive_user_not_automatically_linked(self):
        UserSocialAuth.objects.all().delete()
        Partial.objects.all().delete()
        self._setup_provider_response(success=True, email=self.user.email)
        self.user.is_active = False
        self.user.save()
        self._assert_error(self.data, "invalid_grant", "access_token is not valid")  # pylint: disable=no-value-for-parameter
