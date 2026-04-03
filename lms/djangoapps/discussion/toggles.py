"""
Discussions feature toggles
"""

from openedx.core.djangoapps.discussions.config.waffle import WAFFLE_FLAG_NAMESPACE
from openedx.core.djangoapps.waffle_utils import CourseWaffleFlag

# .. toggle_name: discussions.enable_discussions_mfe
# .. toggle_implementation: CourseWaffleFlag
# .. toggle_default: False
# .. toggle_description: Waffle flag to use the new MFE experience for discussions in the course tab
# .. toggle_use_cases: temporary, open_edx
# .. toggle_creation_date: 2021-11-05
# .. toggle_target_removal_date: 2022-12-05
ENABLE_DISCUSSIONS_MFE = CourseWaffleFlag(
    f"{WAFFLE_FLAG_NAMESPACE}.enable_discussions_mfe", __name__
)


# .. toggle_name: discussions.only_verified_users_can_post
# .. toggle_implementation: CourseWaffleFlag
# .. toggle_default: False
# .. toggle_description: Waffle flag to allow only verified users to post in discussions
# .. toggle_use_cases: temporary, open_edx
# .. toggle_creation_date: 2025-22-07
# .. toggle_target_removal_date: 2026-04-01
ONLY_VERIFIED_USERS_CAN_POST = CourseWaffleFlag(
    f"{WAFFLE_FLAG_NAMESPACE}.only_verified_users_can_post", __name__
)


# .. toggle_name: discussions.enable_rate_limit
# .. toggle_implementation: CourseWaffleFlag
# .. toggle_default: False
# .. toggle_description: Waffle flag to enable rate limit on discussions
# .. toggle_use_cases: temporary, open_edx
# .. toggle_creation_date: 2025-07-29
# .. toggle_target_removal_date: 2026-07-29
ENABLE_RATE_LIMIT_IN_DISCUSSION = CourseWaffleFlag(f'{WAFFLE_FLAG_NAMESPACE}.enable_rate_limit', __name__)


# .. toggle_name: discussions.enable_discussion_ban
# .. toggle_implementation: CourseWaffleFlag
# .. toggle_default: False
# .. toggle_description: Waffle flag to enable ban user functionality in discussion moderation.
#    When enabled, moderators can ban users from discussions at course or organization level
#    during bulk delete operations. This addresses crypto spam attacks and harassment.
# .. toggle_use_cases: opt_in
# .. toggle_creation_date: 2024-11-24
# .. toggle_target_removal_date: 2025-06-01
# .. toggle_warning: This feature requires proper moderator training to prevent misuse.
#    Ensure DISCUSSION_MODERATION_BAN_EMAIL_ENABLED is configured appropriately for your environment.
# .. toggle_tickets: COSMO2-736
ENABLE_DISCUSSION_BAN = CourseWaffleFlag(
    f'{WAFFLE_FLAG_NAMESPACE}.enable_discussion_ban', __name__
)
