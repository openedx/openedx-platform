"""
Signal handlers for third_party_auth app.
"""

from django.db.models.signals import post_save
from django.dispatch import receiver
from edx_django_utils.monitoring import set_custom_attribute

from common.djangoapps.third_party_auth.models import SAMLConfiguration, SAMLProviderConfig


@receiver(post_save, sender=SAMLConfiguration)
def update_saml_provider_configs_on_configuration_change(sender, instance, created, **kwargs):
    """
    Signal handler to create a new SAMLProviderConfig when SAMLConfiguration is updated.

    When a SAMLConfiguration is updated and a new version is created, this handler
    generates a corresponding SAMLProviderConfig that references the latest
    configuration version, ensuring all providers remain aligned with the most
    current settings.
    """
    # .. custom_attribute_name: saml_config_signal.new_config_id
    # .. custom_attribute_description: Records the ID of the new SAML configuration instance.
    set_custom_attribute('saml_config_signal.new_config_id', instance.id)

    # .. custom_attribute_name: saml_config_signal.slug
    # .. custom_attribute_description: Records the slug of the SAML configuration instance.
    set_custom_attribute('saml_config_signal.slug', instance.slug)

    try:
        # Find all existing SAMLProviderConfig instances (current_set) that should be
        # pointing to this slug but are pointing to an older version
        existing_providers = SAMLProviderConfig.objects.current_set().filter(
            saml_configuration__site_id=instance.site_id,
            saml_configuration__slug=instance.slug
        ).exclude(saml_configuration_id=instance.id).exclude(saml_configuration_id__isnull=True)

        updated_count = 0
        for provider_config in existing_providers:
            provider_config.saml_configuration = instance
            provider_config.save()
            updated_count += 1

        # .. custom_attribute_name: saml_config_signal.updated_count
        # .. custom_attribute_description: The number of SAMLProviderConfig records updated to point to the new configuration.
        set_custom_attribute('saml_config_signal.updated_count', updated_count)

    except Exception as e:  # pylint: disable=broad-except
        # .. custom_attribute_name: saml_config_signal.error_message
        # .. custom_attribute_description: Records any error message that occurs during SAML provider config updates.
        set_custom_attribute('saml_config_signal.error_message', str(e))
