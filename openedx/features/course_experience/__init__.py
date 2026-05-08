"""
Unified course experience settings and helper methods.
"""

from django.urls import reverse  # noqa: F401
from django.utils.translation import gettext as _
from edx_toggles.toggles import WaffleFlag
from openedx_filters.exceptions import OpenEdxFilterException
from openedx_filters.learning.filters import CourseHomeUrlCreationStarted

from openedx.core.djangoapps.waffle_utils import CourseWaffleFlag

# Namespace for course experience waffle flags.
WAFFLE_FLAG_NAMESPACE = 'course_experience'

# Waffle flag to disable the separate course outline page and full width content.
DISABLE_COURSE_OUTLINE_PAGE_FLAG = CourseWaffleFlag(  # pylint: disable=toggle-missing-annotation
    f'{WAFFLE_FLAG_NAMESPACE}.disable_course_outline_page', __name__
)

# Waffle flag to let learners access a course before its start date.
COURSE_PRE_START_ACCESS_FLAG = WaffleFlag(f'{WAFFLE_FLAG_NAMESPACE}.pre_start_access', __name__)  # pylint: disable=toggle-missing-annotation

# .. toggle_name: course_experience.enable_course_goals
# .. toggle_implementation: CourseWaffleFlag
# .. toggle_default: False
# .. toggle_description: Used to determine whether or not to use course goals for the particular course.
# .. toggle_use_cases: opt_out, temporary
# .. toggle_creation_date: 2017-09-11
# .. toggle_target_removal_date: None
# .. toggle_warning: This temporary feature toggle does not have a target removal date.
ENABLE_COURSE_GOALS = CourseWaffleFlag(f'{WAFFLE_FLAG_NAMESPACE}.enable_course_goals', __name__)  # pylint: disable=toggle-missing-annotation

# .. toggle_name: course_experience.enable_ses_for_goalreminder
# .. toggle_implementation: CourseWaffleFlag
# .. toggle_default: False
# .. toggle_description: Used to determine whether or not to use AWS SES to send goal reminder emails for the course.
# .. toggle_use_cases: opt_in, temporary
# .. toggle_creation_date: 2024-10-06
# .. toggle_target_removal_date: None
# .. toggle_warning: This temporary feature toggle does not have a target removal date.
ENABLE_SES_FOR_GOALREMINDER = CourseWaffleFlag(f'{WAFFLE_FLAG_NAMESPACE}.enable_ses_for_goalreminder', __name__)  # pylint: disable=toggle-missing-annotation

# Waffle flag to enable anonymous access to a course
SEO_WAFFLE_FLAG_NAMESPACE = 'seo'
COURSE_ENABLE_UNENROLLED_ACCESS_FLAG = CourseWaffleFlag(  # pylint: disable=toggle-missing-annotation
    f'{SEO_WAFFLE_FLAG_NAMESPACE}.enable_anonymous_courseware_access', __name__
)

# .. toggle_name: course_experience.relative_dates
# .. toggle_implementation: CourseWaffleFlag
# .. toggle_default: False
# .. toggle_description: Waffle flag to enable relative dates for course content. A 'Dates' tab will be visible in the
#   course view showing key course dates. Was previously an ExperimentWaffleFlag with experiment_id=17.
# .. toggle_use_cases: opt_in
# .. toggle_creation_date: 2020-02-10
# .. toggle_warning: To set a relative due date for self-paced courses, the weeks_to_complete field for a course run
#   needs to be set. Currently it can be set through the publisher app.
# .. toggle_tickets: https://openedx.atlassian.net/browse/AA-27
RELATIVE_DATES_FLAG = CourseWaffleFlag(f'{WAFFLE_FLAG_NAMESPACE}.relative_dates', __name__)  # pylint: disable=toggle-missing-annotation

# .. toggle_name: course_experience.relative_dates_disable_reset
# .. toggle_implementation: CourseWaffleFlag
# .. toggle_default: False
# .. toggle_description: Waffle flag to disable resetting deadlines by learners in self-paced courses. The 'Dates' tab
#   will no longer show a banner about missed deadlines. The deadlines banner will also be hidden on unit pages.
# .. toggle_use_cases: open_edx
# .. toggle_creation_date: 2023-04-27
# .. toggle_warning: For this toggle to have an effect, the RELATIVE_DATES_FLAG toggle must be enabled, too.
RELATIVE_DATES_DISABLE_RESET_FLAG = CourseWaffleFlag(f'{WAFFLE_FLAG_NAMESPACE}.relative_dates_disable_reset', __name__)

# .. toggle_name: course_experience.calendar_sync
# .. toggle_implementation: CourseWaffleFlag
# .. toggle_default: False
# .. toggle_description: This course flag enables a course tool (which is a tool that is added on a course home page)
#   that sends course assignment calendars to course students, whenever they click on the "Subscribe to calendar
#   updates" button. The email contains an ics attachment that students can then use to sync with their own calendar
#   apps.
# .. toggle_warning: For this toggle to have an effect, the RELATIVE_DATES_FLAG toggle must be enabled, too.
# .. toggle_use_cases: temporary
# .. toggle_creation_date: 2021-01-26
# .. toggle_target_removal_date: 2021-04-26
# .. toggle_tickets: https://openedx.atlassian.net/browse/AA-36
CALENDAR_SYNC_FLAG = CourseWaffleFlag(f'{WAFFLE_FLAG_NAMESPACE}.calendar_sync', __name__)  # pylint: disable=toggle-missing-annotation

# .. toggle_name: course_experience.enforce_masquerade_start_dates
# .. toggle_implementation: CourseWaffleFlag
# .. toggle_default: False
# .. toggle_description: When enabled, staff masquerading as students will see the same start date
#   restrictions as actual students. This provides a more accurate preview experience by enforcing
#   section and subsection start dates even when viewing the course as a masqueraded user.
#   When disabled (default), masquerading continues to bypass start date restrictions as before.
# .. toggle_use_cases: opt_in
# .. toggle_creation_date: 2025-10-08
# .. toggle_warning: Enabling this flag means staff members masquerading as students will not be able to access course
#   content before its start date, which may impact course testing workflows.
#   Also, when you masquerade as a student in a course that starts in the future, you will lock yourself out of the
#   course in the current Django session. To revert this, you need to log out and log back in.
ENFORCE_MASQUERADE_START_DATES = CourseWaffleFlag(
    f'{WAFFLE_FLAG_NAMESPACE}.enforce_masquerade_start_dates', __name__
)


def course_home_page_title(_course):
    """
    Returns the title for the course home page.
    """
    return _('Course')


def default_course_url(course_key):
    """
    Returns the default course URL for the current user.

    Arguments:
        course_key (CourseKey): The course id of the current course.
    """
    from .url_helpers import get_learning_mfe_home_url

    if DISABLE_COURSE_OUTLINE_PAGE_FLAG.is_enabled(course_key):
        # Prevent a circular dependency
        from openedx.features.course_experience.url_helpers import make_learning_mfe_courseware_url
        return make_learning_mfe_courseware_url(course_key)

    return get_learning_mfe_home_url(course_key, url_fragment='home')


def course_home_url(course_key):
    """
    Returns the course home page's URL for the current user.

    Arguments:
        course_key (CourseKey): The course key for which the home url is being requested.
    """
    from .url_helpers import get_learning_mfe_home_url
    home_url = get_learning_mfe_home_url(course_key, url_fragment='home')
    try:
        # .. filter_implemented_name: CourseHomeUrlCreationStarted
        # .. filter_type: org.openedx.learning.course.homepage.url.creation.started.v1
        course_key, home_url = CourseHomeUrlCreationStarted.run_filter(
            course_key=course_key, course_home_url=home_url
        )
    except OpenEdxFilterException as exc:  # noqa: F841
        pass

    return home_url
