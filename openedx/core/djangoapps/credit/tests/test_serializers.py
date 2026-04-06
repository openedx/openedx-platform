""" Tests for Credit API serializers. """

import pytest
from django.test import TestCase
from rest_framework.exceptions import PermissionDenied

from common.djangoapps.student.tests.factories import UserFactory
from openedx.core.djangoapps.credit import serializers, signature
from openedx.core.djangoapps.credit.tests.factories import CreditEligibilityFactory, CreditProviderFactory


class CreditProviderSerializerTests(TestCase):
    """ CreditProviderSerializer tests. """

    def test_data(self):
        """ Verify the correct fields are serialized. """
        provider = CreditProviderFactory(active=False)
        serializer = serializers.CreditProviderSerializer(provider)
        expected = {
            'id': provider.provider_id,
            'display_name': provider.display_name,
            'url': provider.provider_url,
            'status_url': provider.provider_status_url,
            'description': provider.provider_description,
            'enable_integration': provider.enable_integration,
            'fulfillment_instructions': provider.fulfillment_instructions,
            'thumbnail_url': provider.thumbnail_url,
        }
        self.assertDictEqual(serializer.data, expected)


class CreditEligibilitySerializerTests(TestCase):
    """ CreditEligibilitySerializer tests. """

    def test_data(self):
        """ Verify the correct fields are serialized. """
        user = UserFactory()
        eligibility = CreditEligibilityFactory(username=user.username)
        serializer = serializers.CreditEligibilitySerializer(eligibility)
        expected = {
            'course_key': str(eligibility.course.course_key),
            'deadline': eligibility.deadline.strftime('%Y-%m-%dT%H:%M:%S.%fZ'),
            'username': user.username,
        }
        self.assertDictEqual(serializer.data, expected)


class CreditProviderCallbackSerializerTests(TestCase):
    """ CreditProviderCallbackSerializer tests. """

    def test_check_keys_exist_for_provider_string(self):
        """ Verify _check_keys_exist_for_provider errors if key is None """

        secret_key = None
        provider_id = 'asu'

        serializer = serializers.CreditProviderCallbackSerializer()
        with pytest.raises(PermissionDenied):
            serializer._check_keys_exist_for_provider(secret_key, provider_id)  # lint-amnesty, pylint: disable=protected-access

    def test_check_keys_exist_for_provider_list_no_keys(self):
        """
        Verify _check_keys_exist_for_provider errors if no keys in list
        are truthy. (This accounts for there being 2 keys present both
        of which are None due to ascii encode issues)
        """

        secret_key = [None, None]
        provider_id = 'asu'

        serializer = serializers.CreditProviderCallbackSerializer()
        with pytest.raises(PermissionDenied):
            serializer._check_keys_exist_for_provider(secret_key, provider_id)  # lint-amnesty, pylint: disable=protected-access

    def test_check_keys_exist_for_provider_list_with_key_present(self):
        """
        Verify _check_keys_exist_for_provider does not error when at least
        1 key in config is a valid key
        """

        secret_key = [None, 'abc1234', None]
        provider_id = 'asu'

        serializer = serializers.CreditProviderCallbackSerializer()
        result = serializer._check_keys_exist_for_provider(secret_key, provider_id)  # lint-amnesty, pylint: disable=assignment-from-no-return, protected-access
        # No return value, so we expect successful execution to return None
        assert result is None

    def test_compare_signatures_string_key(self):
        """ Verify compare_signature errors if string key does not match. """
        provider = CreditProviderFactory(
            provider_id='asu',
            active=False,
        )

        # Create a serializer that has a signature which was created with a key
        # that we do not have in our system.
        sig = signature.signature({}, 'iamthewrongkey')
        serializer = serializers.CreditProviderCallbackSerializer(
            data={'signature': sig}
        )
        with pytest.raises(PermissionDenied):
            # The first arg here is key we have (that doesn't match the sig)
            serializer._compare_signatures('abcd1234', provider.provider_id)  # lint-amnesty, pylint: disable=protected-access

    def test_compare_signatures_list_key(self):
        """
        Verify compare_signature errors if no keys that are stored in the list
        in config match the key handed in the signature.
        """
        provider = CreditProviderFactory(
            provider_id='asu',
            active=False,
        )

        sig = signature.signature({}, 'iamthewrongkey')
        serializer = serializers.CreditProviderCallbackSerializer(
            data={'signature': sig}
        )

        with pytest.raises(PermissionDenied):
            # The first arg here is the list of keys he have (that dont matcht the sig)
            serializer._compare_signatures(  # lint-amnesty, pylint: disable=protected-access
                ['abcd1234', 'xyz789'],
                provider.provider_id
            )
