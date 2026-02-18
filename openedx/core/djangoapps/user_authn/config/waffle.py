"""
Waffle flags and switches for user authn.
"""

from edx_toggles.toggles import WaffleFlag, WaffleSwitch

_WAFFLE_NAMESPACE = 'user_authn'

# .. toggle_name: user_authn.enable_login_using_thirdparty_auth_only
# .. toggle_implementation: WaffleSwitch
# .. toggle_default: False
# .. toggle_description: When enabled, users must be sign in using their allowed domain SSO account. This includes sign-
#   ins to the Django admin dashboard at "/admin".
# .. toggle_use_cases: temporary
# .. toggle_creation_date: 2019-11-20
# .. toggle_target_removal_date: 2020-01-31
# .. toggle_warning: Requires THIRD_PARTY_AUTH_ONLY_DOMAIN to also be set.
# .. toggle_tickets: ENT-2461
ENABLE_LOGIN_USING_THIRDPARTY_AUTH_ONLY = WaffleSwitch(
    f'{_WAFFLE_NAMESPACE}.enable_login_using_thirdparty_auth_only', __name__
)

# .. toggle_name: user_authn.enable_pwned_password_api
# .. toggle_implementation: WaffleSwitch
# .. toggle_default: False
# .. toggle_description: When enabled, user password's vulnerability would be checked via pwned password database
# .. toggle_use_cases: temporary
# .. toggle_creation_date: 2021-09-22
# .. toggle_target_removal_date: 2021-12-31
# .. toggle_tickets: VAN-664
ENABLE_PWNED_PASSWORD_API = WaffleSwitch(
    f'{_WAFFLE_NAMESPACE}.enable_pwned_password_api', __name__
)

# .. toggle_name: user_authn.enable_enterprise_redirect_to_authn
# .. toggle_implementation: WaffleFlag
# .. toggle_default: False
# .. toggle_description: When enabled, allows Enterprise/B2B customers to be redirected to the AuthN MFE instead of
#   the legacy Django login templates. This flag provides an incremental rollout mechanism for migrating Enterprise
#   customers to the modern authentication experience. The flag has no effect on users with external authentication
#   providers (SAML/TPA), who always remain on the legacy flow. B2C users are redirected to the MFE by default
#   regardless of this flag.
# .. toggle_use_cases: opt_in
# .. toggle_creation_date: 2026-02-18
# .. toggle_warning: This flag only affects Enterprise customers without external auth providers (SAML/TPA).
#   Enabling this flag for an Enterprise customer with complex SSO requirements may break authentication flows.
ENABLE_ENTERPRISE_REDIRECT_TO_AUTHN = WaffleFlag(
    f'{_WAFFLE_NAMESPACE}.enable_enterprise_redirect_to_authn', __name__
)
