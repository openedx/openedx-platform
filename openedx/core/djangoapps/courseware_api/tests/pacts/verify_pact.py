"""pact test for user service client"""

import logging
import os

from django.test import LiveServerTestCase
from django.urls import reverse
from edx_toggles.toggles.testutils import override_waffle_flag
from pact import Verifier

from openedx.features.discounts.applicability import DISCOUNT_APPLICABILITY_FLAG

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.DEBUG)

PACT_DIR = os.path.dirname(os.path.realpath(__file__))
PACT_FILE = "api-courseware-contract.json"


class ProviderVerificationServer(LiveServerTestCase):
    """ Live Server for Pact Verification"""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        cls.PACT_URL = cls.live_server_url

        cls.verifier = Verifier(
            provider='lms',
            provider_base_url=cls.PACT_URL,
        )

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()

    @override_waffle_flag(DISCOUNT_APPLICABILITY_FLAG, active=True)
    def test_verify_pact(self):
        output, _ = self.verifier.verify_pacts(
            os.path.join(PACT_DIR, PACT_FILE),
            headers=['Pact-Authentication: Allow', ],
            provider_states_setup_url=f"{self.PACT_URL}{reverse('courseware_api:provider-state-view')}",
        )

        assert output == 0
