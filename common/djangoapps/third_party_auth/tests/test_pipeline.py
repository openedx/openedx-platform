"""Unit tests for third_party_auth/pipeline.py."""


import json
from unittest import mock

import ddt

from common.djangoapps.third_party_auth import pipeline
from common.djangoapps.third_party_auth.tests import testutil
from common.djangoapps.third_party_auth.tests.specs.base import IntegrationTestMixin
from common.djangoapps.third_party_auth.tests.specs.test_testshib import SamlIntegrationTestUtilities
from common.djangoapps.third_party_auth.tests.testutil import simulate_running_pipeline
from openedx.core.djangolib.testing.utils import skip_unless_lms


@skip_unless_lms
@ddt.ddt
class ProviderUserStateTestCase(testutil.TestCase):
    """Tests ProviderUserState behavior."""

    def test_get_unlink_form_name(self):
        google_provider = self.configure_google_provider(enabled=True)
        state = pipeline.ProviderUserState(google_provider, object(), None)
        assert (google_provider.provider_id + '_unlink_form') == state.get_unlink_form_name()

    @ddt.data(
        ('saml', 'tpa-saml'),
        ('oauth', 'google-oauth2'),
    )
    @ddt.unpack
    def test_get_idp_logout_url_from_running_pipeline(self, idp_type, backend_name):
        """
        Test idp logout url setting for running pipeline
        """
        self.enable_saml()
        idp_slug = "test"
        idp_config = {"logout_url": "http://example.com/logout"}
        getattr(self, f'configure_{idp_type}_provider')(
            enabled=True,
            name="Test Provider",
            slug=idp_slug,
            backend_name=backend_name,
            other_settings=json.dumps(idp_config)
        )
        request = mock.MagicMock()
        kwargs = {
            "response": {
                "idp_name": idp_slug
            }
        }
        with simulate_running_pipeline("common.djangoapps.third_party_auth.pipeline", backend_name, **kwargs):
            logout_url = pipeline.get_idp_logout_url_from_running_pipeline(request)
            assert idp_config['logout_url'] == logout_url


@skip_unless_lms
@ddt.ddt
class PipelineOverridesTest(SamlIntegrationTestUtilities, IntegrationTestMixin, testutil.SAMLTestCase):
    """
    Tests for pipeline overrides
    """

    def setUp(self):
        super().setUp()
        self.enable_saml()
        self.provider = self.configure_saml_provider(
            enabled=True,
            name="Test Provider",
            slug='test',
            backend_name='tpa-saml'
        )

    @ddt.data(
        ('S', 'S-b', False, False, False),
        ('S', 'S-3f', True, False, False),
        ('S', 'S-9fe2', True, True, False),
        ('S.K', 'S_K', False, True, True),
        ('S.K.', 'S_K_-9fe2', True, True, True),
        ('usernamewithcharacterlengthofmorethan30chars', 'usernamewithcharacterlengthofm', False, False, False),
        ('usernamewithcharacterlengthofmorethan30chars', 'usernamewithcharacterlengtho-b', True, False, False),
        ('usernamewithcharacterlengthofmorethan30chars', 'usernamewithcharacterlength-3f', True, True, False),
    )
    @ddt.unpack
    @mock.patch('common.djangoapps.third_party_auth.pipeline.user_exists')
    def test_get_username_in_pipeline(
        self,
        idp_username,
        expected_username,
        already_exists_one,
        already_exists_two,
        already_exists_three,
        mock_user_exists,
    ):
        """
        Test get_username method of running pipeline
        """
        details = {
            "username": idp_username,
            "email": "test@example.com"
        }
        mock_user_exists.side_effect = [already_exists_one, already_exists_two, already_exists_three, False]
        __, strategy = self.get_request_and_strategy()
        with mock.patch('common.djangoapps.third_party_auth.pipeline.username_suffix_generator') as mock_suffix:
            mock_suffix.side_effect = ['b', '3f', '9fe2']
            with mock.patch('common.djangoapps.third_party_auth.pipeline.randint') as mock_randint:
                mock_randint.side_effect = [1, 2, 4]
                final_username = pipeline.get_username(strategy, details, self.provider.backend_class())
                assert expected_username == final_username['username']

    @ddt.data(True, False)
    @mock.patch('common.djangoapps.third_party_auth.pipeline.logger')
    @mock.patch('common.djangoapps.third_party_auth.pipeline.user_exists')
    def test_get_username_logs_squelch_pii(self, squelch_pii, mock_user_exists, mock_logger):
        """
        The get_username logs redact usernames and IdP details when SQUELCH_PII_IN_LOGS is enabled.
        """
        details = {"username": "pii_username", "email": "pii@example.com"}
        mock_user_exists.side_effect = [True, False]  # first candidate taken, so a new one is generated and logged
        __, strategy = self.get_request_and_strategy()
        with self.settings(SQUELCH_PII_IN_LOGS=squelch_pii):
            pipeline.get_username(strategy, details, self.provider.backend_class())

        logged = ' '.join(call.args[0] for call in mock_logger.info.call_args_list)
        assert 'New username candidate generated' in logged
        assert 'get_username complete' in logged
        for pii in ('pii_username', 'pii@example.com'):
            assert (pii in logged) is not squelch_pii
        assert ('[REDACTED]' in logged) is squelch_pii

    def test_get_username(self):
        """
        Test get_username method when the first username candidate is available
        """
        details = {
            "username": 'invalid'
        }
        __, strategy = self.get_request_and_strategy()
        final_username = pipeline.get_username(strategy, details, self.provider.backend_class())
        assert final_username['username'] == 'invalid'
