"""
Togglable settings for Third Party Auth
"""

from edx_toggles.toggles import WaffleFlag

THIRD_PARTY_AUTH_NAMESPACE = 'thirdpartyauth'

# .. toggle_name: third_party_auth.apple_user_migration
# .. toggle_implementation: WaffleFlag
# .. toggle_default: False
# .. toggle_description: Enable User ID matching while apple migration is in process
# .. toggle_use_cases: temporary
# .. toggle_creation_date: 2023-02-27
# .. toggle_target_removal_date: 2023-05-01
# .. toggle_tickets: LEARNER-8790
# .. toggle_warning: None.
APPLE_USER_MIGRATION_FLAG = WaffleFlag(f'{THIRD_PARTY_AUTH_NAMESPACE}.apple_user_migration', __name__)


def is_apple_user_migration_enabled():
    """
    Returns a boolean if Apple users migration is in process.
    """
    return APPLE_USER_MIGRATION_FLAG.is_enabled()
