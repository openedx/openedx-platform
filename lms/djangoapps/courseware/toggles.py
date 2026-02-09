"""
Toggles for courseware in-course experience.
"""

from edx_toggles.toggles import SettingToggle, WaffleFlag, WaffleSwitch

from openedx.core.djangoapps.waffle_utils import CourseWaffleFlag

# Namespace for courseware waffle flags.
WAFFLE_FLAG_NAMESPACE = 'courseware'


# Waffle flag to enable the course exit page in the learning MFE.
#
# .. toggle_name: courseware.microfrontend_course_exit_page
# .. toggle_implementation: CourseWaffleFlag
# .. toggle_default: False
# .. toggle_description: Supports staged rollout of the new micro-frontend-based implementation of the course exit page.
# .. toggle_use_cases: open_edx, temporary
# .. toggle_creation_date: 2020-10-02
# .. toggle_target_removal_date: None
# .. toggle_warning: Also set settings.LEARNING_MICROFRONTEND_URL.
# .. toggle_tickets: AA-188
COURSEWARE_MICROFRONTEND_COURSE_EXIT_PAGE = CourseWaffleFlag(
    f'{WAFFLE_FLAG_NAMESPACE}.microfrontend_course_exit_page', __name__
)

# .. toggle_name: courseware.mfe_progress_milestones
# .. toggle_implementation: CourseWaffleFlag
# .. toggle_default: False
# .. toggle_description: Waffle flag to display learner progress milestones in a course. Supports staged
#   rollout to students for a new micro-frontend-based implementation of the courseware page.
# .. toggle_use_cases: temporary, open_edx
# .. toggle_creation_date: 2020-10-07
# .. toggle_target_removal_date: none
# .. toggle_warning: Also set settings.LEARNING_MICROFRONTEND_URL.
# .. toggle_tickets: AA-371
COURSEWARE_MICROFRONTEND_PROGRESS_MILESTONES = CourseWaffleFlag(
    f'{WAFFLE_FLAG_NAMESPACE}.mfe_progress_milestones', __name__
)

# .. toggle_name: courseware.mfe_progress_milestones_streak_celebration
# .. toggle_implementation: CourseWaffleFlag
# .. toggle_default: False
# .. toggle_description: Waffle flag to display a celebration modal when learner completes a configurable streak
#   Supports staged rollout to students for a new micro-frontend-based implementation of the
#   courseware page.
# .. toggle_use_cases: temporary, open_edx
# .. toggle_creation_date: 2021-02-16
# .. toggle_target_removal_date: None
# .. toggle_warning: Also set settings.LEARNING_MICROFRONTEND_URL and
#   COURSEWARE_MICROFRONTEND_PROGRESS_MILESTONES.
# .. toggle_tickets: AA-304
COURSEWARE_MICROFRONTEND_PROGRESS_MILESTONES_STREAK_CELEBRATION = CourseWaffleFlag(
    f'{WAFFLE_FLAG_NAMESPACE}.mfe_progress_milestones_streak_celebration', __name__
)

# .. toggle_name: courseware.mfe_courseware_search
# .. toggle_implementation: WaffleFlag
# .. toggle_default: False
# .. toggle_description: Enables Courseware Search on Learning MFE
# .. toggle_use_cases: temporary
# .. toggle_creation_date: 2023-09-28
# .. toggle_target_removal_date: None
# .. toggle_tickets: KBK-20
# .. toggle_warning: None.
COURSEWARE_MICROFRONTEND_SEARCH_ENABLED = CourseWaffleFlag(
    f'{WAFFLE_FLAG_NAMESPACE}.mfe_courseware_search', __name__
)

# .. toggle_name: courseware.disable_navigation_sidebar_blocks_caching
# .. toggle_implementation: WaffleFlag
# .. toggle_default: False
# .. toggle_description: Disable caching of navigation sidebar blocks on Learning MFE.
#   It can be used when caching the structure of large courses for a large number of users
#   at the same time can overload the cache storage (memcache or redis).
# .. toggle_use_cases: opt_out, open_edx
# .. toggle_creation_date: 2024-03-21
# .. toggle_target_removal_date: None
# .. toggle_tickets: FC-0056
# .. toggle_warning: None.
COURSEWARE_MICROFRONTEND_NAVIGATION_SIDEBAR_BLOCKS_DISABLE_CACHING = CourseWaffleFlag(
    f'{WAFFLE_FLAG_NAMESPACE}.disable_navigation_sidebar_blocks_caching', __name__
)

# .. toggle_name: courseware.mfe_progress_milestones_streak_discount_enabled
# .. toggle_implementation: CourseWaffleFlag
# .. toggle_default: False
# .. toggle_description: This flag enables an engagement discount incentive message.
# .. toggle_warning: This flag depends on the streak celebration feature being enabled
# .. toggle_use_cases: opt_out, open_edx
# .. toggle_creation_date: 2021-08-26
# .. toggle_target_removal_date: None
# .. toggle_tickets: https://openedx.atlassian.net/browse/AA-950
COURSEWARE_MFE_MILESTONES_STREAK_DISCOUNT = CourseWaffleFlag(
    f'{WAFFLE_FLAG_NAMESPACE}.streak_discount_enabled', __name__
)


# .. toggle_name: courseware.optimized_render_xblock
# .. toggle_implementation: CourseWaffleFlag
# .. toggle_default: False
# .. toggle_description: Waffle flag that determines whether we speed up the render_xblock for browsers by
#   removing unnecessary JavaScript and CSS. It is possible that this could introduce edge cases with content
#   that relies on these assets, so being a CourseWaffleFlag will give us the flexibility to exempt courses
#   from these optimizations.
# .. toggle_use_cases: temporary
# .. toggle_creation_date: 2021-02-09
# .. toggle_target_removal_date: 2021-05-01
COURSEWARE_OPTIMIZED_RENDER_XBLOCK = CourseWaffleFlag(
    f'{WAFFLE_FLAG_NAMESPACE}.optimized_render_xblock', __name__
)

# .. toggle_name: COURSES_INVITE_ONLY
# .. toggle_implementation: SettingToggle
# .. toggle_type: feature_flag
# .. toggle_default: False
# .. toggle_description: Setting this sets the default value of INVITE_ONLY across all courses in a given deployment
# .. toggle_category: admin
# .. toggle_use_cases: open_edx
# .. toggle_creation_date: 2019-05-16
# .. toggle_expiration_date: None
# .. toggle_tickets: https://github.com/mitodl/edx-platform/issues/123
COURSES_INVITE_ONLY = SettingToggle('COURSES_INVITE_ONLY', default=False)


ENABLE_OPTIMIZELY_IN_COURSEWARE = WaffleSwitch(  # lint-amnesty, pylint: disable=toggle-missing-annotation
    'RET.enable_optimizely_in_courseware', __name__
)

# .. toggle_name: courseware.discovery_default_language_filter
# .. toggle_implementation: WaffleSwitch
# .. toggle_default: False
# .. toggle_description: Enable courses to be filtered by user language by default.
# .. toggle_use_cases: open_edx
# .. toggle_creation_date: 2023-11-02
# .. toggle_target_removal_date: None
# .. toggle_warning: The ENABLE_COURSE_DISCOVERY feature flag should be enabled.
# .. toggle_tickets: https://github.com/openedx/edx-platform/pull/33647
ENABLE_COURSE_DISCOVERY_DEFAULT_LANGUAGE_FILTER = WaffleSwitch(
    f'{WAFFLE_FLAG_NAMESPACE}.discovery_default_language_filter', __name__
)

# .. toggle_name: courseware.unify_site_and_translation_language
# .. toggle_implementation: WaffleFlag
# .. toggle_default: False
# .. toggle_description: Update LMS to use site language for xpert unit translations and enable new header site language switcher.
# .. toggle_use_cases: opt_in
# .. toggle_creation_date: 2026-01-08
# .. toggle_target_removal_date: None
# .. toggle_warning: n/a
# .. toggle_tickets: https://github.com/openedx/edx-platform/pull/37854
ENABLE_UNIFIED_SITE_AND_TRANSLATION_LANGUAGE = WaffleFlag(
    f'{WAFFLE_FLAG_NAMESPACE}.unify_site_and_translation_language', __name__
)


def course_exit_page_is_active(course_key):
    return COURSEWARE_MICROFRONTEND_COURSE_EXIT_PAGE.is_enabled(course_key)


def courseware_mfe_progress_milestones_are_active(course_key):
    return COURSEWARE_MICROFRONTEND_PROGRESS_MILESTONES.is_enabled(course_key)


def streak_celebration_is_active(course_key):
    return (
        courseware_mfe_progress_milestones_are_active(course_key) and
        COURSEWARE_MICROFRONTEND_PROGRESS_MILESTONES_STREAK_CELEBRATION.is_enabled(course_key)
    )


def course_is_invitation_only(courselike) -> bool:
    """Returns whether the course is invitation only or not."""
    # We also mark Old Mongo courses (deprecated keys) as invitation only to cut off enrollment
    return COURSES_INVITE_ONLY.is_enabled() or courselike.invitation_only or courselike.id.deprecated


def courseware_mfe_search_is_enabled(course_key=None):
    """
    Return whether the courseware.mfe_courseware_search flag is on.
    """
    return COURSEWARE_MICROFRONTEND_SEARCH_ENABLED.is_enabled(course_key)


def courseware_disable_navigation_sidebar_blocks_caching(course_key=None):
    """
    Return whether the courseware.disable_navigation_sidebar_blocks_caching flag is on.
    """
    return COURSEWARE_MICROFRONTEND_NAVIGATION_SIDEBAR_BLOCKS_DISABLE_CACHING.is_enabled(course_key)
