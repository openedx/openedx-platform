"""
MFE Context API Serializers
"""

from rest_framework import serializers


class ProvidersSerializer(serializers.Serializer):
    """
    Providers Serializers
    """

    id = serializers.CharField(allow_null=True)
    name = serializers.CharField(allow_null=True)
    iconClass = serializers.CharField(allow_null=True)
    iconImage = serializers.CharField(allow_null=True)
    skipHintedLogin = serializers.BooleanField(default=False)
    skipRegistrationForm = serializers.BooleanField(default=False)
    loginUrl = serializers.CharField(allow_null=True)
    registerUrl = serializers.CharField(allow_null=True)


class PipelineUserDetailsSerializer(serializers.Serializer):
    """
    Pipeline User Details Serializers
    """

    username = serializers.CharField(allow_null=True)
    email = serializers.CharField(allow_null=True)
    name = serializers.CharField(source='fullname', allow_null=True)
    firstName = serializers.CharField(source='first_name', allow_null=True)
    lastName = serializers.CharField(source='last_name', allow_null=True)


class ContextDataSerializer(serializers.Serializer):
    """
    Context Data Serializers
    """

    currentProvider = serializers.CharField(allow_null=True)
    platformName = serializers.CharField(allow_null=True)
    providers = serializers.ListField(
        child=ProvidersSerializer(),
        allow_null=True
    )
    secondaryProviders = serializers.ListField(
        child=ProvidersSerializer(),
        allow_null=True
    )
    finishAuthUrl = serializers.CharField(allow_null=True)
    errorMessage = serializers.CharField(allow_null=True)
    registerFormSubmitButtonText = serializers.CharField(allow_null=True)
    autoSubmitRegForm = serializers.BooleanField(default=False)
    syncLearnerProfileData = serializers.BooleanField(default=False)
    countryCode = serializers.CharField(allow_null=True)
    welcomePageRedirectUrl = serializers.CharField(allow_null=True)
    pipelineUserDetails = serializers.SerializerMethodField()

    def get_pipelineUserDetails(self, obj):
        if obj.get('pipeline_user_details'):
            return PipelineUserDetailsSerializer(obj.get('pipeline_user_details')).data
        return {}

    def to_representation(self, instance):
        """
        Serialize the declared fields, then merge in the context's ``extra_context`` entries.

        ``extra_context`` holds entries contributed by plugins, which this serializer cannot
        declare fields for. They are merged as-is: the contributor owns their shape, and no
        coercion is applied.
        """
        representation = super().to_representation(instance)
        representation.update(instance.get('extra_context') or {})
        return representation


class MFEContextSerializer(serializers.Serializer):
    """
    Serializer class to convert the keys of MFE Context Response dict object to camelCase format.
    """

    contextData = ContextDataSerializer(
        source='context_data',
        default={}
    )
    registrationFields = serializers.DictField(
        source='registration_fields',
        default={}
    )
    optionalFields = serializers.DictField(
        source='optional_fields',
        default={
            'extended_profile': []
        }
    )
