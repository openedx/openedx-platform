""" Credit API Serializers """


import datetime
import logging

from zoneinfo import ZoneInfo
from django.conf import settings
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied

from openedx.core.djangoapps.credit.models import CreditCourse, CreditEligibility, CreditProvider, CreditRequest
from openedx.core.djangoapps.credit.signature import get_shared_secret_key, signature
from openedx.core.lib.api.serializers import CourseKeyField
from common.djangoapps.util.date_utils import from_timestamp

log = logging.getLogger(__name__)


class CreditCourseSerializer(serializers.ModelSerializer):
    """ CreditCourse Serializer """

    course_key = CourseKeyField()

    class Meta:
        model = CreditCourse
        exclude = ('id',)


class CreditProviderSerializer(serializers.ModelSerializer):
    """ CreditProvider """
    id = serializers.CharField(source='provider_id')  # pylint:disable=invalid-name
    description = serializers.CharField(source='provider_description')
    status_url = serializers.URLField(source='provider_status_url')
    url = serializers.URLField(source='provider_url')

    class Meta:
        model = CreditProvider
        fields = ('id', 'display_name', 'url', 'status_url', 'description', 'enable_integration',
                  'fulfillment_instructions', 'thumbnail_url',)


class CreditEligibilitySerializer(serializers.ModelSerializer):
    """ CreditEligibility serializer. """
    course_key = serializers.SerializerMethodField()

    def get_course_key(self, obj):
        """ Returns the course key associated with the course. """
        return str(obj.course.course_key)

    class Meta:
        model = CreditEligibility
        fields = ('username', 'course_key', 'deadline',)


class CreditProviderCallbackSerializer(serializers.Serializer):  # pylint:disable=abstract-method
    """
    Serializer for input to the CreditProviderCallback view.

    This is used solely for validating the input.
    """
    request_uuid = serializers.CharField(required=True)
    status = serializers.ChoiceField(required=True, choices=CreditRequest.REQUEST_STATUS_CHOICES)
    timestamp = serializers.IntegerField(required=True)
    signature = serializers.CharField(required=True)

    def __init__(self, **kwargs):
        self.provider = kwargs.pop('provider', None)
        super().__init__(**kwargs)

    def validate_timestamp(self, value):
        """ Ensure the request has been received in a timely manner. """
        date_time = from_timestamp(value)

        # Ensure we converted the timestamp to a datetime
        if not date_time:
            msg = f'[{value}] is not a valid timestamp'
            log.warning(msg)
            raise serializers.ValidationError(msg)

        elapsed = (datetime.datetime.now(ZoneInfo("UTC")) - date_time).total_seconds()
        if elapsed > settings.CREDIT_PROVIDER_TIMESTAMP_EXPIRATION:
            msg = f'[{value}] is too far in the past (over [{elapsed}] seconds).'
            log.warning(msg)
            raise serializers.ValidationError(msg)

        return value

    def _check_keys_exist_for_provider(self, secret_key, provider_id):
        """
        Verify there are keys available in the secret to
        verify signature against.

        Throw error if none are available.
        """

        # Accounts for old way of storing provider key
        if secret_key is None:
            msg = 'Could not retrieve secret key for credit provider [{}]. ' \
                  'Unable to validate requests from provider.'.format(provider_id)
            log.error(msg)
            raise PermissionDenied(msg)

        # Accounts for new way of storing provider key
        # We need at least 1 key here that we can use to validate the signature
        if isinstance(secret_key, list) and not any(secret_key):
            msg = 'Could not retrieve secret key for credit provider [{}]. ' \
                  'Unable to validate requests from provider.'.format(provider_id)
            log.error(msg)
            raise PermissionDenied(msg)

    def _compare_signatures(self, secret_key, provider_id):
        """
        Compare signature we received with the signature we expect/have.

        Throw an error if they don't match.
        """

        data = self.initial_data
        actual_signature = data["signature"]

        # Accounts for old way of storing provider key
        if isinstance(secret_key, str) and signature(data, secret_key) != actual_signature:
            msg = f'Request from credit provider [{provider_id}] had an invalid signature.'
            raise PermissionDenied(msg)

        # Accounts for new way of storing provider key
        if isinstance(secret_key, list):
            # Received value just needs to match one of the keys we have
            key_match = False
            for secretvalue in secret_key:
                if signature(data, secretvalue) == actual_signature:
                    key_match = True

            if not key_match:
                msg = f'Request from credit provider [{provider_id}] had an invalid signature.'
                raise PermissionDenied(msg)

    def validate_signature(self, value):
        """ Validate the signature and ensure the provider is setup properly. """
        provider_id = self.provider.provider_id
        secret_key = get_shared_secret_key(provider_id)

        self._check_keys_exist_for_provider(secret_key, provider_id)
        self._compare_signatures(secret_key, provider_id)

        return value
