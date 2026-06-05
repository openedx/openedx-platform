"""
Tests for the SignatureValidator class.
"""


from unittest.mock import patch

import ddt
from django.test import TestCase
from django.test.client import RequestFactory

from lms.djangoapps.lti_provider.models import LtiConsumer
from lms.djangoapps.lti_provider.signature_validator import SignatureValidator
from openedx.core.djangolib.testing.utils import CacheIsolationMixin

FIXED_TIMESTAMP = 1_000_000_000


def get_lti_consumer():
    """
    Helper method for all Signature Validator tests to get an LtiConsumer object.
    """
    return LtiConsumer(
        consumer_name='Consumer Name',
        consumer_key='Consumer Key',
        consumer_secret='Consumer Secret'
    )


@ddt.ddt
class ClientKeyValidatorTest(TestCase):
    """
    Tests for the check_client_key method in the SignatureValidator class.
    """

    def setUp(self):
        super().setUp()
        self.lti_consumer = get_lti_consumer()

    def test_valid_client_key(self):
        """
        Verify that check_client_key succeeds with a valid key
        """
        key = self.lti_consumer.consumer_key
        assert SignatureValidator(self.lti_consumer).check_client_key(key)

    @ddt.data(
        ('0123456789012345678901234567890123456789',),
        ('',),
        (None,),
    )
    @ddt.unpack
    def test_invalid_client_key(self, key):
        """
        Verify that check_client_key fails with a disallowed key
        """
        assert not SignatureValidator(self.lti_consumer).check_client_key(key)


@ddt.ddt
class NonceValidatorTest(TestCase):
    """
    Tests for the check_nonce method in the SignatureValidator class.
    """

    def setUp(self):
        super().setUp()
        self.lti_consumer = get_lti_consumer()

    def test_valid_nonce(self):
        """
        Verify that check_nonce succeeds with a key of maximum length
        """
        nonce = '0123456789012345678901234567890123456789012345678901234567890123'
        assert SignatureValidator(self.lti_consumer).check_nonce(nonce)

    @ddt.data(
        ('01234567890123456789012345678901234567890123456789012345678901234',),
        ('',),
        (None,),
    )
    @ddt.unpack
    def test_invalid_nonce(self, nonce):
        """
        Verify that check_nonce fails with badly formatted nonce
        """
        assert not SignatureValidator(self.lti_consumer).check_nonce(nonce)


class SignatureValidatorTest(TestCase):
    """
    Tests for the custom SignatureValidator class that uses the oauthlib library
    to check message signatures. Note that these tests mock out the library
    itself, since we assume it to be correct.
    """

    def setUp(self):
        super().setUp()
        self.lti_consumer = get_lti_consumer()

    def test_get_existing_client_secret(self):
        """
        Verify that get_client_secret returns the right value for the correct
        key
        """
        key = self.lti_consumer.consumer_key
        secret = SignatureValidator(self.lti_consumer).get_client_secret(key, None)
        assert secret == self.lti_consumer.consumer_secret

    @patch('oauthlib.oauth1.SignatureOnlyEndpoint.validate_request',
           return_value=(True, None))
    def test_verification_parameters(self, verify_mock):
        """
        Verify that the signature validaton library method is called using the
        correct parameters derived from the HttpRequest.
        """
        body = 'oauth_signature_method=HMAC-SHA1&oauth_version=1.0'
        content_type = 'application/x-www-form-urlencoded'
        request = RequestFactory().post('/url', body, content_type=content_type)
        headers = {'Content-Type': content_type}
        SignatureValidator(self.lti_consumer).verify(request)
        verify_mock.assert_called_once_with(
            request.build_absolute_uri(), 'POST', body.encode('utf-8'), headers)


class TimestampAndNonceValidatorTest(CacheIsolationMixin, TestCase):
    """
    Tests for the validate_timestamp_and_nonce method in SignatureValidator.
    """

    ENABLED_CACHES = ['default']

    def setUp(self):
        super().setUp()
        patcher = patch(
            'lms.djangoapps.lti_provider.signature_validator.time.time',
            return_value=FIXED_TIMESTAMP,
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.validator = SignatureValidator(get_lti_consumer())

    def _call(self, timestamp, nonce, client_key='Consumer Key'):
        return self.validator.validate_timestamp_and_nonce(
            client_key, str(timestamp), nonce, request=None,
        )

    def test_valid_timestamp_and_new_nonce(self):
        assert self._call(FIXED_TIMESTAMP, 'nonce-a')

    def test_stale_timestamp_rejected(self):
        assert not self._call(FIXED_TIMESTAMP - 301, 'nonce-b')

    def test_future_timestamp_rejected(self):
        assert not self._call(FIXED_TIMESTAMP + 301, 'nonce-c')

    def test_malformed_timestamp_rejected(self):
        assert not self.validator.validate_timestamp_and_nonce(
            'Consumer Key', 'not-a-number', 'nonce-d', request=None,
        )

    def test_replay_rejected(self):
        assert self._call(FIXED_TIMESTAMP, 'nonce-e')
        assert not self._call(FIXED_TIMESTAMP, 'nonce-e')

    def test_different_nonce_same_consumer_accepted(self):
        assert self._call(FIXED_TIMESTAMP, 'nonce-f1')
        assert self._call(FIXED_TIMESTAMP, 'nonce-f2')

    def test_same_nonce_different_consumer_accepted(self):
        # Nonces are scoped per client_key; the same nonce string from two
        # different consumers must not block each other.
        assert self._call(FIXED_TIMESTAMP, 'nonce-g', client_key='Consumer Key')
        assert self._call(FIXED_TIMESTAMP, 'nonce-g', client_key='Other Key')
