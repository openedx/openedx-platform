from urllib.parse import urljoin  # noqa: I001

import responses

from scripts.user_retirement.utils import edx_api

FAKE_ACCESS_TOKEN = 'THIS_IS_A_JWT'
CONTENT_TYPE = 'application/json'


class OAuth2Mixin:
    @staticmethod
    def mock_access_token_response(status=200):
        """
        Mock POST requests to retrieve an access token for this site's service user.
        """
        responses.add(
            responses.POST,
            urljoin('http://localhost:18000/', edx_api.OAUTH_ACCESS_TOKEN_URL),
            status=status,
            json={'access_token': FAKE_ACCESS_TOKEN, 'expires_in': 60},
            content_type=CONTENT_TYPE
        )
