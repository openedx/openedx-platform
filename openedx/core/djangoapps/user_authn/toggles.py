"""
Toggles for user_authn
"""


from django.conf import settings

from openedx.core.djangoapps.site_configuration import helpers as configuration_helpers
from openedx.core.djangoapps.theming.helpers import get_current_request
from edx_toggles.toggles import WaffleFlag


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
        'ENABLE_AUTHN_MICROFRONTEND', getattr(settings, 'ENABLE_AUTHN_MICROFRONTEND', False)
    )


def is_auto_generated_username_enabled():
    """
    Checks if auto-generated username should be enabled.
    """
    return configuration_helpers.get_value(
        'ENABLE_AUTO_GENERATED_USERNAME', getattr(settings, 'ENABLE_AUTO_GENERATED_USERNAME', False)
    )


# .. toggle_name: user_authn.prevent_password_reuse_on_reset
# .. toggle_implementation: WaffleFlag
# .. toggle_default: False
# .. toggle_description: When enabled, prevents users from resetting their password
#    to the same value as their current password.
# .. toggle_use_cases: temporary
# .. toggle_creation_date: 2026-06-15
# .. toggle_target_removal_date: 2026-08-15
# .. toggle_tickets: https://2u-internal.atlassian.net/browse/AUT-139


PREVENT_PASSWORD_REUSE_ON_RESET = WaffleFlag(
    'user_authn.prevent_password_reuse_on_reset', __name__
)
