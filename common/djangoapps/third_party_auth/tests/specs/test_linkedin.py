"""Integration tests for LinkedIn providers."""


from common.djangoapps.third_party_auth.tests.specs import base


def get_localized_name(name):
    """Returns the localizedName from the name object"""
    locale = "{}_{}".format(
        name["preferredLocale"]["language"],
        name["preferredLocale"]["country"]
    )
    return name['localized'].get(locale, '')


class LinkedInOauth2IntegrationTest(base.Oauth2IntegrationTest):  # pylint: disable=test-inherits-tests
    """Integration tests for provider.LinkedInOauth2."""

    PROVIDER_NAME = "linkedin"
    PROVIDER_BACKEND = "linkedin-oauth2"
    PROVIDER_ID = "oa2-linkedin-oauth2"

    def setUp(self):
        super().setUp()
        self.provider = self.configure_linkedin_provider(
            enabled=True,
            visible=True,
            key='linkedin_oauth2_key',
            secret='linkedin_oauth2_secret',
        )

    TOKEN_RESPONSE_DATA = {
        'access_token': 'access_token_value',
        'expires_in': 'expires_in_value',
    }
    USER_RESPONSE_DATA = {
        'lastName': {
            "localized": {
                "en_US": "Doe"
            },
            "preferredLocale": {
                "country": "US",
                "language": "en"
            }
        },
        'id': 'id_value',
        'firstName': {
            "localized": {
                "en_US": "Doe"
            },
            "preferredLocale": {
                "country": "US",
                "language": "en"
            }
        },
    }

    def get_username(self):
        response_data = self.get_response_data()
        first_name = get_localized_name(response_data.get('firstName'))
        last_name = get_localized_name(response_data.get('lastName'))
        return first_name + last_name
