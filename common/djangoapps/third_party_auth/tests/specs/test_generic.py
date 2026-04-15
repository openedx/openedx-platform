"""
Use the 'Dummy' auth provider for generic integration tests of third_party_auth.
"""
from common.djangoapps.third_party_auth.tests import testutil
from common.djangoapps.third_party_auth.tests.utils import skip_unless_thirdpartyauth

from .base import IntegrationTestMixin


@skip_unless_thirdpartyauth()
class GenericIntegrationTest(IntegrationTestMixin, testutil.TestCase):
    """
    Basic integration tests of third_party_auth using Dummy provider
    """
    PROVIDER_ID = "oa2-dummy"
    PROVIDER_NAME = "Dummy"
    PROVIDER_BACKEND = "dummy"

    USER_EMAIL = "adama@fleet.colonies.gov"
    USER_NAME = "William Adama"
    USER_USERNAME = "Galactica1"

    def setUp(self):
        super().setUp()
        self.configure_dummy_provider(enabled=True, visible=True)

    def do_provider_login(self, provider_redirect_url):
        """
        Mock logging in to the Dummy provider
        """
        # For the Dummy provider, the provider redirect URL is self.complete_url
        assert provider_redirect_url == (self.url_prefix + self.complete_url)
        return self.client.get(provider_redirect_url)
