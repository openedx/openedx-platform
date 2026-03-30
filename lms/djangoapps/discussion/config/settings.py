"""
Discussion settings.
"""
from django.conf import settings

from openedx.core.djangoapps.waffle_utils import CourseWaffleFlag

WAFFLE_NAMESPACE = 'discussion'


def is_forum_daily_digest_enabled():
    """Returns whether forum notification features should be visible"""
    # .. toggle_name: FEATURES['ENABLE_FORUM_DAILY_DIGEST']
    # .. toggle_implementation: DjangoSetting
    # .. toggle_default: False
    # .. toggle_description: Settings for forums/discussions to on/off daily digest
    #   feature. Set this to True if you want to enable users to subscribe and unsubscribe
    #   for daily digest. This setting enables deprecation of daily digest.
    # .. toggle_use_cases: open_edx
    # .. toggle_creation_date: 2020-03-09
    return settings.FEATURES.get('ENABLE_FORUM_DAILY_DIGEST', False)

# .. toggle_name: discussion.enable_captcha
# .. toggle_implementation: CourseWaffleFlag
# .. toggle_default: False
# .. toggle_description: When the flag is ON, users will be able to see captcha for discussion
# .. toggle_use_cases: temporary, open_edx
# .. toggle_creation_date: 2025-07-12
# .. toggle_target_removal_date: 2025-07-29
# .. toggle_warning: When the flag is ON, users will be able to see captcha for discussion.
ENABLE_CAPTCHA_IN_DISCUSSION = CourseWaffleFlag(f'{WAFFLE_NAMESPACE}.enable_captcha', __name__)
