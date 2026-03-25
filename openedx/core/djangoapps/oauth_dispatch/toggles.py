"""
Toggles for Oauth Dispatch.
"""


from edx_toggles.toggles import WaffleSwitch  # noqa: I001

OAUTH_DISPATCH_WAFFLE_SWITCH_NAMESPACE = 'oauth_dispatch'

# .. toggle_name: DISABLE_JWT_FOR_MOBILE
# .. toggle_implementation: WaffleSwitch
# .. toggle_default: False
# .. toggle_description: Set toggle to True to ignore mobile requests
#   for JWTs, and instead provide the legacy opaque Bearer token. This
#   toggle can be used as a temporary kill switch as the mobile app
#   attempts to rollout the transition to JWT authentication for the
#   first time.
# .. toggle_use_cases: temporary
# .. toggle_creation_date: 2022-10-14
# .. toggle_target_removal_date: 2022-12-30
DISABLE_JWT_FOR_MOBILE = WaffleSwitch(
    f'{OAUTH_DISPATCH_WAFFLE_SWITCH_NAMESPACE}.disable_jwt_for_mobile',
    __name__
)
