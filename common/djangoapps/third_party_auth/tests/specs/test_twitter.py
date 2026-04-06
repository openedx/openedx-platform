"""  # lint-amnesty, pylint: disable=cyclic-import
Separate integration test for Twitter which is an OAuth1 provider.
"""


from unittest.mock import patch

from common.djangoapps.third_party_auth.tests.specs import base


class TwitterIntegrationTest(base.Oauth2IntegrationTest):  # lint-amnesty, pylint: disable=test-inherits-tests
    """Integration tests for Twitter backend."""

    PROVIDER_NAME = "twitter"
    PROVIDER_BACKEND = "twitter-oauth2"
    PROVIDER_ID = "oa2-twitter-oauth2"

    def setUp(self):
        super().setUp()
        self.provider = self.configure_twitter_provider(
            enabled=True,
            visible=True,
            key='twitter_oauth1_key',
            secret='twitter_oauth1_secret',
        )

        # To test an OAuth1 provider, we need to patch an additional method:
        patcher = patch(
            'social_core.backends.twitter.TwitterOAuth.unauthorized_token',
            create=True,
            return_value="unauth_token"
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    TOKEN_RESPONSE_DATA = {
        'access_token': 'access_token_value',
        'token_type': 'bearer',
    }
    USER_RESPONSE_DATA = {
        'id': 10101010,
        'name': 'Bob Loblaw',
        'description': 'A Twitter User',
        'screen_name': 'bobloblaw',
        'location': 'Twitterverse',
        'followers_count': 77,
        'verified': False,
    }

    def get_username(self):
        response_data = self.get_response_data()
        return response_data.get('screen_name')
