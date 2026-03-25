"""pact test for user service client"""

import logging  # noqa: I001
import os

from django.test import LiveServerTestCase
from django.urls import reverse
from pact import Verifier

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.DEBUG)

PACT_DIR = os.path.dirname(os.path.realpath(__file__))
PACT_FILE = "api-block-contract.json"


class ProviderVerificationServer(LiveServerTestCase):
    """ Django Test Live Server for Pact Verification """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        cls.verifier = Verifier(
            provider='lms',
            provider_base_url=cls.live_server_url,
        )

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()

    def test_verify_pact(self):
        output, _ = self.verifier.verify_pacts(
            os.path.join(PACT_DIR, PACT_FILE),
            headers=['Pact-Authentication: Allow', ],
            provider_states_setup_url=f"{self.live_server_url}{reverse('provider-state-view')}",
        )

        assert output == 0
