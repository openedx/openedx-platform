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

    @override_waffle_flag(DISCOUNT_APPLICABILITY_FLAG, active=True)
    def test_verify_pact(self):
        (
            Verifier(name='lms')
            .add_transport(url=self.live_server_url)
            .add_source(os.path.join(PACT_DIR, PACT_FILE))
            .add_custom_header('Pact-Authentication', 'Allow')
            .state_handler(
                f"{self.live_server_url}{reverse('courseware_api:provider-state-view')}",
                body=True,
            )
            .verify()
        )
