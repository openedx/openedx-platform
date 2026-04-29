"""
Toggles for user_authn
"""


from django.conf import settings

from openedx.core.djangoapps.site_configuration import helpers as configuration_helpers
from openedx.core.djangoapps.theming.helpers import get_current_request


def is_require_third_party_auth_enabled():
    # TODO: Replace function with SettingToggle when it is available.
    return getattr(settings, "ENABLE_REQUIRE_THIRD_PARTY_AUTH", False)


def should_redirect_to_authn_microfrontend():
    """
    Checks if login/registration should be done via MFE.
    """
    request = get_current_request()
    if request and request.GET.get('skip_authn_mfe'):
        return False
    return configuration_helpers.get_value(
        'ENABLE_AUTHN_MICROFRONTEND', settings.FEATURES.get('ENABLE_AUTHN_MICROFRONTEND')
    )


# .. toggle_name: ENABLE_AUTO_GENERATED_USERNAME
# .. toggle_implementation: DjangoSetting
# .. toggle_default: False
# .. toggle_description: Set to True to enable auto-generation of usernames.
# .. toggle_use_cases: open_edx
# .. toggle_creation_date: 2024-02-20
# .. toggle_warning: Changing this setting may affect user authentication, account management and discussions experience.  # noqa: E501


def is_auto_generated_username_enabled():
    """
    Checks if auto-generated username should be enabled.
    """
    return configuration_helpers.get_value(
        'ENABLE_AUTO_GENERATED_USERNAME', settings.FEATURES.get('ENABLE_AUTO_GENERATED_USERNAME')
    )
