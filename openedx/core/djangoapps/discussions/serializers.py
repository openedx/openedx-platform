"""
Serializers for Discussion views.
"""
from django.core.exceptions import ValidationError
from lti_consumer.api import get_lti_pii_sharing_state_for_course
from lti_consumer.models import LtiConfiguration
from rest_framework import serializers

from openedx.core.djangoapps.discussions.tasks import update_discussions_settings_from_course_task
from openedx.core.djangoapps.django_comment_common.models import CourseDiscussionSettings
from openedx.core.lib.courses import get_course_by_id
from xmodule.modulestore.django import modulestore

from ..content.course_overviews.models import CourseOverviewTab
from .models import DiscussionsConfiguration, Provider
from .utils import available_division_schemes, get_divided_discussions


class LtiSerializer(serializers.ModelSerializer):
    """
    Serialize LtiConfiguration responses
    """
    pii_share_email = serializers.BooleanField(required=False)
    pii_share_username = serializers.BooleanField(required=False)

    class Meta:
        model = LtiConfiguration
        fields = [
            'pii_share_username',
            'pii_share_email',
            'lti_1p1_client_key',
            'lti_1p1_client_secret',
            'lti_1p1_launch_url',
            'version',
        ]

    def to_internal_value(self, data: dict) -> dict:
        """
        Transform the incoming primitive data into a native value
        """
        data = data or {}
        payload = {
            key: value
            for key, value in data.items()
            if key in self.Meta.fields
        }
        return payload

    def update(self, instance: LtiConfiguration, validated_data: dict) -> LtiConfiguration:
        """
        Create/update a model-backed instance
        """
        instance = instance or LtiConfiguration()
        instance.config_store = LtiConfiguration.CONFIG_ON_DB
        pii_sharing_allowed = self.context.get('pii_sharing_allowed', False)
        if validated_data:
            for key, value in validated_data.items():
                if key.startswith('pii_') and not pii_sharing_allowed:
                    raise serializers.ValidationError(
                        "Cannot enable sending PII data until PII sharing for LTI is enabled for the course."
                    )
                if key in self.Meta.fields:
                    setattr(instance, key, value)
            instance.save()
        return instance


class LegacySettingsSerializer(serializers.BaseSerializer):
    """
    Serialize legacy discussions settings
    """

    class Meta:
        fields = [
            'allow_anonymous',
            'allow_anonymous_to_peers',
            'discussion_blackouts',
            'discussion_topics',
            # The following fields are deprecated;
            # they technically still exist in Studio (so we mention them here),
            # but they are not supported in the new experience:
            # 'discussion_link',
            # 'discussion_sort_alpha',
        ]
        fields_cohorts = [
            'always_divide_inline_discussions',
            'divided_course_wide_discussions',
            'divided_inline_discussions',
            'division_scheme',
            'reported_content_email_notifications'
        ]

    def create(self, validated_data):
        """
        We do not need this.
        """
        raise NotImplementedError

    def to_internal_value(self, data: dict) -> dict:
        """
        Transform the incoming primitive data into a native value
        """
        if not isinstance(data.get('allow_anonymous', False), bool):
            raise serializers.ValidationError('Wrong type for allow_anonymous')
        if not isinstance(data.get('allow_anonymous_to_peers', False), bool):
            raise serializers.ValidationError('Wrong type for allow_anonymous_to_peers')
        if not isinstance(data.get('discussion_blackouts', []), list):
            raise serializers.ValidationError('Wrong type for discussion_blackouts')
        if not isinstance(data.get('discussion_topics', {}), dict):
            raise serializers.ValidationError('Wrong type for discussion_topics')
        return data

    def to_representation(self, instance) -> dict:
        """
        Serialize data into a dictionary, to be used as a response
        """
        settings = {
            field.name: field.read_json(instance)
            for field in instance.fields.values()
            if field.name in self.Meta.fields
        }
        discussion_settings = CourseDiscussionSettings.get(instance.id)
        serializer = DiscussionSettingsSerializer(
            discussion_settings,
            context={
                'course': instance,
                'settings': discussion_settings,
            },
            partial=True,
        )
        settings.update({
            key: value
            for key, value in serializer.data.items()
            if key != 'id'
        })
        return settings

    def update(self, instance, validated_data: dict):
        """
        Update and save an existing instance
        """
        save = False
        cohort_settings = {}
        for field, value in validated_data.items():
            if field in self.Meta.fields:
                setattr(instance, field, value)
                save = True
            elif field in self.Meta.fields_cohorts:
                cohort_settings[field] = value
        if cohort_settings:
            discussion_settings = CourseDiscussionSettings.get(instance.id)
            serializer = DiscussionSettingsSerializer(
                discussion_settings,
                context={
                    'course': instance,
                    'settings': discussion_settings,
                },
                data=cohort_settings,
                partial=True,
            )
            if serializer.is_valid(raise_exception=True):
                serializer.save()
        if save:
            modulestore().update_item(instance, self.context['user_id'])
        return instance


class DiscussionsConfigurationSerializer(serializers.ModelSerializer):
    """
    Serialize configuration responses
    """

    class Meta:
        model = DiscussionsConfiguration
        course_fields = [
            'provider_type',
            'enable_in_context',
            'enable_graded_units',
            'unit_level_visibility',
            'posting_restrictions',
        ]
        fields = [
            'enabled',
        ] + course_fields

    def _get_course(self):
        """
        Get course and save it in the context, so it doesn't need to be reloaded.
        """
        if self.context.get('course') is None:
            self.context['course'] = get_course_by_id(self.instance.context_key)
        return self.context['course']

    def create(self, validated_data):
        """
        We do not need this.
        """
        raise NotImplementedError

    def to_internal_value(self, data: dict) -> dict:
        """
        Transform the *incoming* primitive data into a native value.
        """
        payload = super().to_internal_value(data)
        payload.update({
            'lti_configuration': data.get('lti_configuration', {}),
            'plugin_configuration': data.get('plugin_configuration', {}),
        })
        return payload

    def to_representation(self, instance: DiscussionsConfiguration) -> dict:
        """
        Serialize data into a dictionary, to be used as a response
        """
        course_key = instance.context_key
        active_provider = instance.provider_type
        provider_type = self.context.get('provider_type') or active_provider
        payload = super().to_representation(instance)
        course_pii_sharing_allowed = get_lti_pii_sharing_state_for_course(course_key)

        # LTI configuration is only stored for the active provider.
        if provider_type == active_provider:
            lti_configuration = LtiSerializer(instance=instance.lti_configuration)
            lti_configuration_data = lti_configuration.data
            plugin_configuration = instance.plugin_configuration
        else:
            lti_configuration_data = {}
            plugin_configuration = {}

        course = get_course_by_id(course_key)
        if provider_type in [Provider.LEGACY, Provider.OPEN_EDX]:
            legacy_settings = LegacySettingsSerializer(course, data=plugin_configuration)
            if legacy_settings.is_valid(raise_exception=True):
                plugin_configuration = legacy_settings.data
            if provider_type == Provider.OPEN_EDX:
                plugin_configuration.update({
                    "group_at_subsection": instance.plugin_configuration.get("group_at_subsection", False)
                })
        lti_configuration_data.update({'pii_sharing_allowed': course_pii_sharing_allowed})
        payload.update({
            'provider_type': provider_type,
            'lti_configuration': lti_configuration_data,
            'plugin_configuration': plugin_configuration,
        })
        return payload

    def update(self, instance: DiscussionsConfiguration, validated_data: dict) -> DiscussionsConfiguration:
        """
        Update and save an existing instance
        """
        # This needs to check which fields have changed, so do it before
        # fields are copied over.
        instance = self._update_course_configuration(instance, validated_data)
        instance = self._update_plugin_configuration(instance, validated_data)
        for key in self.Meta.fields:
            value = validated_data.get(key)
            if value is not None:
                setattr(instance, key, value)
        # _update_* helpers assume `enabled` and `provider_type`
        # have already been set
        instance = self._update_lti(instance, validated_data)
        instance.save()
        # find the discussion tab and update its visibility as per discussions configurations
        # It can go out of sync due to unknown reasons
        CourseOverviewTab.objects.filter(
            course_overview_id=instance.context_key,
            type='discussion'
        ).update(is_hidden=not instance.enabled)
        update_discussions_settings_from_course_task.delay(str(instance.context_key))
        return instance

    def _update_lti(
        self,
        instance: DiscussionsConfiguration,
        validated_data: dict,
    ) -> DiscussionsConfiguration:
        """
        Update LtiConfiguration
        """
        lti_configuration_data = validated_data.get('lti_configuration')

        if not instance.supports_lti():
            instance.lti_configuration = None
        elif lti_configuration_data:
            lti_configuration = instance.lti_configuration or LtiConfiguration()
            lti_serializer = LtiSerializer(
                lti_configuration,
                data=lti_configuration_data,
                partial=True,
                context={
                    'pii_sharing_allowed': get_lti_pii_sharing_state_for_course(instance.context_key),
                }
            )
            if lti_serializer.is_valid(raise_exception=True):
                lti_serializer.save()
            instance.lti_configuration = lti_configuration
        return instance

    def _update_plugin_configuration(
        self,
        instance: DiscussionsConfiguration,
        validated_data: dict,
    ) -> DiscussionsConfiguration:
        """
        Create/update legacy provider settings
        """
        plugin_configuration = validated_data.pop('plugin_configuration', {})
        updated_provider_type = validated_data.get('provider_type') or instance.provider_type

        if updated_provider_type in [Provider.LEGACY, Provider.OPEN_EDX]:
            legacy_settings = LegacySettingsSerializer(
                self._get_course(),
                context={
                    'user_id': self.context['user_id'],
                },
                data=plugin_configuration,
            )
            if legacy_settings.is_valid(raise_exception=True):
                legacy_settings.save()
            instance.plugin_configuration = {
                "group_at_subsection": plugin_configuration.get("group_at_subsection", False)
            }
        else:
            instance.plugin_configuration = plugin_configuration
        return instance

    def _update_course_configuration(
        self,
        instance: DiscussionsConfiguration,
        validated_data: dict,
    ) -> DiscussionsConfiguration:
        """
        Update configuration settings that are stored in the course.
        """
        save = False
        updated_provider_type = validated_data.get('provider_type') or instance.provider_type
        course = self._get_course()
        for key in self.Meta.course_fields:
            value = validated_data.get(key)
            course_value = course.discussions_settings.get(key, None)
            # Delay loading course till we know something has actually been updated
            if value is not None and value != course_value:
                course.discussions_settings[key] = value
                save = True
        new_plugin_config = validated_data.get('plugin_configuration', None)
        if new_plugin_config and new_plugin_config != instance.plugin_configuration:
            save = True
            # Any fields here that aren't already stored in the course structure
            # or in other models should be stored here.
            course.discussions_settings[updated_provider_type] = {
                key: value
                for key, value in new_plugin_config.items()
                if (
                    key not in LegacySettingsSerializer.Meta.fields and
                    key not in LegacySettingsSerializer.Meta.fields_cohorts
                )
            }
        # Toggle discussion tab is_hidden. Before Palm, we would mark the discussion tab with the is_hidden property.
        # Redwood and later, we disable discussions entirely by toggling the discussion configuration enabled property.
        # This ensures pre-Palm courses import with discussions tab appropriately shown/hidden.
        for tab in course.tabs:
            if tab.tab_id == 'discussion' and tab.is_hidden == validated_data.get('enabled'):
                tab.is_hidden = not validated_data.get('enabled')
                save = True
                break
        if save:
            modulestore().update_item(course, self.context['user_id'])
        return instance


class DiscussionSettingsSerializer(serializers.Serializer):
    """
    Serializer for course discussion settings.
    """
    divided_discussions = serializers.ListField(
        child=serializers.CharField(),
        write_only=True,
    )
    divided_course_wide_discussions = serializers.ListField(
        child=serializers.CharField(),
        read_only=True,
    )
    divided_inline_discussions = serializers.ListField(
        child=serializers.CharField(),
        read_only=True,
    )
    always_divide_inline_discussions = serializers.BooleanField()
    reported_content_email_notifications = serializers.BooleanField()
    division_scheme = serializers.CharField()

    def to_internal_value(self, data: dict) -> dict:
        """
        Transform the *incoming* primitive data into a native value.
        """
        payload = super().to_internal_value(data) or {}
        course = self.context['course']
        instance = self.context['settings']
        if any(item in data for item in ('divided_course_wide_discussions', 'divided_inline_discussions')):
            divided_course_wide_discussions, divided_inline_discussions = get_divided_discussions(
                course, instance
            )
            divided_course_wide_discussions = data.get(
                'divided_course_wide_discussions',
                divided_course_wide_discussions
            )
            divided_inline_discussions = data.get('divided_inline_discussions', divided_inline_discussions)
            try:
                payload['divided_discussions'] = divided_course_wide_discussions + divided_inline_discussions
            except TypeError as error:
                raise ValidationError(str(error)) from error
        for item in ('always_divide_inline_discussions', 'division_scheme'):
            if item in data:
                payload[item] = data[item]
        return payload

    def to_representation(self, instance: CourseDiscussionSettings) -> dict:
        """
        Return a serialized representation of the course discussion settings.
        """
        course = self.context['course']
        instance = self.context['settings']
        course_key = course.id
        divided_course_wide_discussions, divided_inline_discussions = get_divided_discussions(
            course, instance
        )
        payload = {
            'id': instance.id,
            'divided_inline_discussions': divided_inline_discussions,
            'divided_course_wide_discussions': divided_course_wide_discussions,
            'always_divide_inline_discussions': instance.always_divide_inline_discussions,
            'division_scheme': instance.division_scheme,
            'available_division_schemes': available_division_schemes(course_key),
            'reported_content_email_notifications': instance.reported_content_email_notifications,
        }
        return payload

    def create(self, validated_data):
        """
        This method intentionally left empty
        """

    def update(self, instance: CourseDiscussionSettings, validated_data: dict) -> CourseDiscussionSettings:
        """
        Update and save an existing instance
        """
        if not any(field in validated_data for field in self.fields):
            raise ValidationError('Bad request')
        try:
            instance.update(validated_data)
        except ValueError as e:
            raise ValidationError(str(e)) from e
        return instance


class DiscussionsProviderSerializer(serializers.Serializer):
    """
    Serializer for a discussion provider
    """
    features = serializers.ListField(child=serializers.CharField(), help_text="Features supported by the provider")
    supports_lti = serializers.BooleanField(default=False, help_text="Whether the provider supports LTI")
    external_links = serializers.DictField(help_text="External documentation and links for provider")
    messages = serializers.ListField(child=serializers.CharField(), help_text="Custom messaging for provider")
    has_full_support = serializers.BooleanField(help_text="Whether the provider is fully supported")
    admin_only_config = serializers.BooleanField(help_text="Whether the provider can only be configured by admins")


class DiscussionsFeatureSerializer(serializers.Serializer):
    """
    Serializer for discussions features
    """
    id = serializers.CharField(help_text="Feature ID")
    feature_support_type = serializers.CharField(help_text="Feature support level classification")


class DiscussionsProvidersSerializer(serializers.Serializer):
    """
    Serializer for discussion providers.
    """
    active = serializers.CharField(
        read_only=True,
        help_text="The current active provider",
    )
    features = serializers.ListField(
        child=DiscussionsFeatureSerializer(read_only=True),
        help_text="Features support classification levels",
    )
    available = serializers.DictField(
        child=DiscussionsProviderSerializer(read_only=True),
        help_text="List of available providers",
    )
