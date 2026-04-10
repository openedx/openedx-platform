"""
Tests for the SignatureValidator class.
"""


from unittest.mock import patch

import ddt
from django.test import TestCase, override_settings
from django.test.client import RequestFactory

from lms.djangoapps.lti_provider.models import LtiConsumer
from lms.djangoapps.lti_provider.signature_validator import SignatureValidator


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


@override_settings(CACHES={
    'default': {
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
        'LOCATION': 'lti-nonce-regression-tests',
    },
})
class NonceReplayRegressionTest(TestCase):
    """
    Regression tests for LTI nonce/timestamp replay protection.

    Prior to the fix, ``validate_timestamp_and_nonce`` was a hard-coded
    ``return True``, disabling every oauthlib replay check. An attacker
    who captured a valid LTI launch could replay it indefinitely.

    ``lms.envs.test`` configures a ``DummyCache`` by default, which
    would make ``cache.add`` a no-op and silently pass every replay
    test. Override the backend to ``LocMemCache`` so the regression
    tests actually exercise the nonce-tracking code path.
    """

    def setUp(self):
        super().setUp()
        from django.core.cache import cache
        cache.clear()
        self.validator = SignatureValidator(get_lti_consumer())

    def _now_ts(self):
        import time
        return str(int(time.time()))

    def test_sec_lti_replay_fresh_timestamp_accepted_regression(self):
        """A fresh timestamp with an unseen nonce must be accepted."""
        assert self.validator.validate_timestamp_and_nonce(
            'Consumer Key', self._now_ts(), 'nonce-A', request=None,
        ) is True

    def test_sec_lti_replay_duplicate_nonce_rejected_regression(self):
        """A second request with the same nonce within the window must fail."""
        ts = self._now_ts()
        assert self.validator.validate_timestamp_and_nonce(
            'Consumer Key', ts, 'nonce-B', request=None,
        ) is True
        assert self.validator.validate_timestamp_and_nonce(
            'Consumer Key', ts, 'nonce-B', request=None,
        ) is False

    def test_sec_lti_replay_stale_timestamp_rejected_regression(self):
        """A timestamp older than the window must be rejected."""
        import time
        stale = str(int(time.time()) - 3600)
        assert self.validator.validate_timestamp_and_nonce(
            'Consumer Key', stale, 'nonce-C', request=None,
        ) is False

    def test_sec_lti_replay_future_timestamp_rejected_regression(self):
        """A timestamp far in the future must be rejected."""
        import time
        future = str(int(time.time()) + 3600)
        assert self.validator.validate_timestamp_and_nonce(
            'Consumer Key', future, 'nonce-D', request=None,
        ) is False

    def test_sec_lti_replay_malformed_timestamp_rejected_regression(self):
        """A non-numeric timestamp must be rejected."""
        assert self.validator.validate_timestamp_and_nonce(
            'Consumer Key', 'not-a-number', 'nonce-E', request=None,
        ) is False

    def test_sec_lti_replay_nonce_isolated_per_consumer_regression(self):
        """
        Same nonce string from different consumer keys must each be
        accepted once: nonces are scoped per consumer.
        """
        ts = self._now_ts()
        assert self.validator.validate_timestamp_and_nonce(
            'Consumer Key', ts, 'nonce-F', request=None,
        ) is True
        # A different consumer key must still accept the same nonce value.
        other_validator = SignatureValidator(LtiConsumer(
            consumer_name='Other', consumer_key='Other Key', consumer_secret='s',
        ))
        assert other_validator.validate_timestamp_and_nonce(
            'Other Key', ts, 'nonce-F', request=None,
        ) is True
