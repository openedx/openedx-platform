"""
Unit tests for third_party_auth LTI auth providers
"""


import unittest

from oauthlib.common import Request

from common.djangoapps.third_party_auth.lti import LTI_PARAMS_KEY, LTIAuthBackend
from common.djangoapps.third_party_auth.tests.testutil import ThirdPartyAuthTestMixin
from common.test.utils import assert_dict_contains_subset


class UnitTestLTI(unittest.TestCase, ThirdPartyAuthTestMixin):
    """
    Unit tests for third_party_auth LTI auth providers
    """

    def test_get_user_details_missing_keys(self):
        lti = LTIAuthBackend()
        details = lti.get_user_details({LTI_PARAMS_KEY: {
            'lis_person_name_full': 'Full name'
        }})
        assert details == {'fullname': 'Full name'}

    def test_get_user_details_extra_keys(self):
        lti = LTIAuthBackend()
        details = lti.get_user_details({LTI_PARAMS_KEY: {
            'lis_person_name_full': 'Full name',
            'lis_person_name_given': 'Given',
            'lis_person_name_family': 'Family',
            'email': 'user@example.com',
            'other': 'something else'
        }})
        assert details == {'fullname': 'Full name', 'first_name': 'Given',
                           'last_name': 'Family', 'email': 'user@example.com'}

    def test_get_user_id(self):
        lti = LTIAuthBackend()
        user_id = lti.get_user_id(None, {LTI_PARAMS_KEY: {
            'oauth_consumer_key': 'consumer',
            'user_id': 'user'
        }})
        assert user_id == 'consumer:user'

    def test_validate_lti_valid_request(self):
        request = Request(
            uri='https://example.com/lti',
            http_method='POST',
            body=self.read_data_file('lti_valid_request.txt')
        )
        parameters = LTIAuthBackend._get_validated_lti_params_from_values(  # pylint: disable=protected-access
            request=request, current_time=1436823554,
            lti_consumer_valid=True, lti_consumer_secret='secret',
            lti_max_timestamp_age=10
        )
        assert parameters
        assert_dict_contains_subset(
            self,
            {
                'custom_extra': 'parameter',
                'user_id': '292832126'
            },
            parameters,
        )

    def test_validate_lti_valid_request_with_get_params(self):
        request = Request(
            uri='https://example.com/lti?user_id=292832126&lti_version=LTI-1p0',
            http_method='POST',
            body=self.read_data_file('lti_valid_request_with_get_params.txt')
        )
        parameters = LTIAuthBackend._get_validated_lti_params_from_values(  # pylint: disable=protected-access
            request=request, current_time=1436823554,
            lti_consumer_valid=True, lti_consumer_secret='secret',
            lti_max_timestamp_age=10
        )
        assert parameters
        assert_dict_contains_subset(
            self,
            {
                'custom_extra': 'parameter',
                'user_id': '292832126'
            },
            parameters,
        )

    def test_validate_lti_old_timestamp(self):
        request = Request(
            uri='https://example.com/lti',
            http_method='POST',
            body=self.read_data_file('lti_old_timestamp.txt')
        )
        parameters = LTIAuthBackend._get_validated_lti_params_from_values(  # pylint: disable=protected-access
            request=request, current_time=1436900000,
            lti_consumer_valid=True, lti_consumer_secret='secret',
            lti_max_timestamp_age=10
        )
        assert not parameters

    def test_validate_lti_invalid_signature(self):
        request = Request(
            uri='https://example.com/lti',
            http_method='POST',
            body=self.read_data_file('lti_invalid_signature.txt')
        )
        parameters = LTIAuthBackend._get_validated_lti_params_from_values(  # pylint: disable=protected-access
            request=request, current_time=1436823554,
            lti_consumer_valid=True, lti_consumer_secret='secret',
            lti_max_timestamp_age=10
        )
        assert not parameters

    def test_validate_lti_cannot_add_get_params(self):
        request = Request(
            uri='https://example.com/lti?custom_another=parameter',
            http_method='POST',
            body=self.read_data_file('lti_cannot_add_get_params.txt')
        )
        parameters = LTIAuthBackend._get_validated_lti_params_from_values(  # pylint: disable=protected-access
            request=request, current_time=1436823554,
            lti_consumer_valid=True, lti_consumer_secret='secret',
            lti_max_timestamp_age=10
        )
        assert not parameters

    def test_validate_lti_garbage(self):
        request = Request(
            uri='https://example.com/lti',
            http_method='POST',
            body=self.read_data_file('lti_garbage.txt')
        )
        parameters = LTIAuthBackend._get_validated_lti_params_from_values(  # pylint: disable=protected-access
            request=request, current_time=1436823554,
            lti_consumer_valid=True, lti_consumer_secret='secret',
            lti_max_timestamp_age=10
        )
        assert not parameters
